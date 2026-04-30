"""
agent.py — LiveKit Voice AI Agent for MyKare Health.

This module defines the real-time voice pipeline that connects to a LiveKit
room, receives audio from the patient, processes it through an
STT → LLM (with tool calling) → TTS pipeline, and sends audio back.

Run with:
    python agent.py start
"""

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Annotated

from dotenv import load_dotenv

load_dotenv()

# ─── Logging ──────────────────────────────────────────────────────

logger = logging.getLogger("mykare-voice-agent")
logging.basicConfig(level=logging.INFO)

# ─── LiveKit imports ──────────────────────────────────────────────
# NOTE: These require the livekit packages to be installed.
# Uncomment the livekit lines in requirements.txt first.

from livekit.agents import (
    AutoSubscribe,
    JobContext,
    WorkerOptions,
    cli,
    llm as livekit_llm,
)
from livekit.agents.pipeline import VoicePipelineAgent
from livekit.plugins import deepgram, cartesia, openai, silero

# ─── Application imports ─────────────────────────────────────────

from prompts import SYSTEM_PROMPT
from tools import (
    identify_user,
    fetch_slots,
    book_appointment,
    retrieve_appointments,
    cancel_appointment,
    modify_appointment,
    end_conversation,
)


# ─── Session State ────────────────────────────────────────────────


@dataclass
class SessionState:
    """Tracks everything that persists across turns in a conversation."""

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    phone: str | None = None
    name: str | None = None
    conversation_history: list = field(default_factory=list)
    intent: str = "inquiry"
    started_at: str = field(
        default_factory=lambda: datetime.now(tz=timezone.utc).isoformat()
    )


# ─── Data Channel Event Helper ────────────────────────────────────


async def emit_tool_event(
    room,
    event_type: str,
    tool_name: str,
    status: str,
    message: str,
    data=None,
):
    """Publish a JSON tool-event payload to the LiveKit room data channel.

    This lets the frontend display real-time updates like
    "Fetching slots…" or "Booking confirmed ✅".
    """
    payload = {
        "type": "tool_event",
        "event_type": event_type,  # "tool_start" or "tool_end"
        "tool": tool_name,
        "status": status,  # "in_progress", "success", or "error"
        "message": message,
        "data": data,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }
    try:
        await room.local_participant.publish_data(
            json.dumps(payload).encode("utf-8"),
            reliable=True,
        )
    except Exception as exc:
        logger.error("Failed to emit tool event: %s", exc)


# ─── Tool Registry ────────────────────────────────────────────────


def build_tool_functions(state: SessionState, room=None):
    """Return an ``livekit_llm.FunctionContext`` with all 7 tool wrappers.

    Each wrapper logs the call, emits data-channel events, delegates
    to the matching function in tools.py, and updates session state.
    """

    class MykareTools(livekit_llm.FunctionContext):
        # ── identify_user ──────────────────────────────────────

        @livekit_llm.callable_function(
            name="identify_user",
            description=(
                "Identify or register a caller by their phone number. "
                "Must be called before any other tool."
            ),
        )
        async def tool_identify_user(
            self,
            phone: Annotated[
                str,
                livekit_llm.TypeInfo(
                    description="The caller's phone number (10-15 digits)."
                ),
            ],
            name: Annotated[
                str,
                livekit_llm.TypeInfo(
                    description="The caller's name, if provided."
                ),
            ] = "",
        ):
            tool_name = "identify_user"
            logger.info("Tool call: %s(phone=%s, name=%s)", tool_name, phone, name)

            if room:
                await emit_tool_event(
                    room, "tool_start", tool_name,
                    "in_progress", "Looking up your information…",
                )

            result = identify_user(phone, name=name or None)

            # Update session state on success
            if result.get("success"):
                user_data = result.get("data", {})
                state.phone = user_data.get("phone", state.phone)
                state.name = user_data.get("name", state.name)

            state.conversation_history.append(
                {"role": "tool", "tool": tool_name, "result": result}
            )

            if room:
                await emit_tool_event(
                    room, "tool_end", tool_name,
                    "success" if result.get("success") else "error",
                    result.get("message", ""),
                    result.get("data"),
                )

            return json.dumps(result)

        # ── fetch_slots ────────────────────────────────────────

        @livekit_llm.callable_function(
            name="fetch_slots",
            description=(
                "Fetch available appointment slots. Optionally filter by "
                "a preferred date in YYYY-MM-DD format."
            ),
        )
        async def tool_fetch_slots(
            self,
            preferred_date: Annotated[
                str,
                livekit_llm.TypeInfo(
                    description="Preferred date in YYYY-MM-DD format (optional)."
                ),
            ] = "",
        ):
            tool_name = "fetch_slots"
            logger.info("Tool call: %s(preferred_date=%s)", tool_name, preferred_date)

            if room:
                await emit_tool_event(
                    room, "tool_start", tool_name,
                    "in_progress", "Checking available appointment slots…",
                )

            result = fetch_slots(preferred_date=preferred_date or None)

            state.conversation_history.append(
                {"role": "tool", "tool": tool_name, "result": result}
            )

            if room:
                await emit_tool_event(
                    room, "tool_end", tool_name,
                    "success" if result.get("success") else "error",
                    result.get("message", ""),
                    result.get("data"),
                )

            return json.dumps(result)

        # ── book_appointment ───────────────────────────────────

        @livekit_llm.callable_function(
            name="book_appointment",
            description=(
                "Book an appointment for the caller after they have "
                "confirmed the date and time."
            ),
        )
        async def tool_book_appointment(
            self,
            phone: Annotated[
                str,
                livekit_llm.TypeInfo(description="Caller's phone number."),
            ],
            name: Annotated[
                str,
                livekit_llm.TypeInfo(description="Caller's full name."),
            ],
            slot_date: Annotated[
                str,
                livekit_llm.TypeInfo(
                    description="Appointment date in YYYY-MM-DD format."
                ),
            ],
            slot_time: Annotated[
                str,
                livekit_llm.TypeInfo(
                    description="Appointment time in HH:MM 24-hour format."
                ),
            ],
            notes: Annotated[
                str,
                livekit_llm.TypeInfo(
                    description="Optional notes for the appointment."
                ),
            ] = "",
        ):
            tool_name = "book_appointment"
            logger.info(
                "Tool call: %s(phone=%s, name=%s, date=%s, time=%s)",
                tool_name, phone, name, slot_date, slot_time,
            )

            if room:
                await emit_tool_event(
                    room, "tool_start", tool_name,
                    "in_progress", "Booking your appointment…",
                )

            result = book_appointment(
                phone, name, slot_date, slot_time,
                notes=notes or None,
            )

            state.conversation_history.append(
                {"role": "tool", "tool": tool_name, "result": result}
            )

            if room:
                status = "success" if result.get("success") else "error"
                await emit_tool_event(
                    room, "tool_end", tool_name,
                    status, result.get("message", ""),
                    result.get("data"),
                )

            return json.dumps(result)

        # ── retrieve_appointments ──────────────────────────────

        @livekit_llm.callable_function(
            name="retrieve_appointments",
            description=(
                "Retrieve all active appointments for a caller so the "
                "agent can read them out loud."
            ),
        )
        async def tool_retrieve_appointments(
            self,
            phone: Annotated[
                str,
                livekit_llm.TypeInfo(description="Caller's phone number."),
            ],
        ):
            tool_name = "retrieve_appointments"
            logger.info("Tool call: %s(phone=%s)", tool_name, phone)

            if room:
                await emit_tool_event(
                    room, "tool_start", tool_name,
                    "in_progress", "Looking up your appointments…",
                )

            result = retrieve_appointments(phone)

            state.conversation_history.append(
                {"role": "tool", "tool": tool_name, "result": result}
            )

            if room:
                await emit_tool_event(
                    room, "tool_end", tool_name,
                    "success" if result.get("success") else "error",
                    result.get("message", ""),
                    result.get("data"),
                )

            return json.dumps(result)

        # ── cancel_appointment ─────────────────────────────────

        @livekit_llm.callable_function(
            name="cancel_appointment",
            description="Cancel a specific appointment by its ID.",
        )
        async def tool_cancel_appointment(
            self,
            appointment_id: Annotated[
                int,
                livekit_llm.TypeInfo(
                    description="The ID of the appointment to cancel."
                ),
            ],
            phone: Annotated[
                str,
                livekit_llm.TypeInfo(description="Caller's phone number."),
            ],
        ):
            tool_name = "cancel_appointment"
            logger.info(
                "Tool call: %s(id=%s, phone=%s)",
                tool_name, appointment_id, phone,
            )

            if room:
                await emit_tool_event(
                    room, "tool_start", tool_name,
                    "in_progress", "Cancelling your appointment…",
                )

            result = cancel_appointment(appointment_id, phone)

            state.conversation_history.append(
                {"role": "tool", "tool": tool_name, "result": result}
            )

            if room:
                await emit_tool_event(
                    room, "tool_end", tool_name,
                    "success" if result.get("success") else "error",
                    result.get("message", ""),
                    result.get("data"),
                )

            return json.dumps(result)

        # ── modify_appointment ─────────────────────────────────

        @livekit_llm.callable_function(
            name="modify_appointment",
            description="Reschedule an existing appointment to a new date and time.",
        )
        async def tool_modify_appointment(
            self,
            appointment_id: Annotated[
                int,
                livekit_llm.TypeInfo(
                    description="The ID of the appointment to modify."
                ),
            ],
            phone: Annotated[
                str,
                livekit_llm.TypeInfo(description="Caller's phone number."),
            ],
            new_date: Annotated[
                str,
                livekit_llm.TypeInfo(
                    description="New appointment date in YYYY-MM-DD format."
                ),
            ],
            new_time: Annotated[
                str,
                livekit_llm.TypeInfo(
                    description="New appointment time in HH:MM 24-hour format."
                ),
            ],
        ):
            tool_name = "modify_appointment"
            logger.info(
                "Tool call: %s(id=%s, phone=%s, new=%s %s)",
                tool_name, appointment_id, phone, new_date, new_time,
            )

            if room:
                await emit_tool_event(
                    room, "tool_start", tool_name,
                    "in_progress", "Rescheduling your appointment…",
                )

            result = modify_appointment(
                appointment_id, phone, new_date, new_time,
            )

            state.conversation_history.append(
                {"role": "tool", "tool": tool_name, "result": result}
            )

            if room:
                await emit_tool_event(
                    room, "tool_end", tool_name,
                    "success" if result.get("success") else "error",
                    result.get("message", ""),
                    result.get("data"),
                )

            return json.dumps(result)

        # ── end_conversation ───────────────────────────────────

        @livekit_llm.callable_function(
            name="end_conversation",
            description=(
                "Wrap up the call — generate a summary and save it. "
                "Only call after asking if the caller needs anything else."
            ),
        )
        async def tool_end_conversation(
            self,
            session_id: Annotated[
                str,
                livekit_llm.TypeInfo(description="The current session ID."),
            ],
            phone: Annotated[
                str,
                livekit_llm.TypeInfo(
                    description="Caller's phone number (optional)."
                ),
            ] = "",
            conversation_history: Annotated[
                str,
                livekit_llm.TypeInfo(
                    description=(
                        "JSON string of the conversation history list. "
                        "Each item has 'role' and 'content' keys."
                    )
                ),
            ] = "[]",
        ):
            tool_name = "end_conversation"
            logger.info("Tool call: %s(session_id=%s)", tool_name, session_id)

            if room:
                await emit_tool_event(
                    room, "tool_start", tool_name,
                    "in_progress", "Wrapping up the call…",
                )

            # Deserialize conversation_history from JSON string
            try:
                history = json.loads(conversation_history)
            except (json.JSONDecodeError, TypeError):
                history = state.conversation_history

            result = end_conversation(
                session_id,
                phone=phone or state.phone,
                conversation_history=history,
            )

            # Update state
            if result.get("success"):
                summary = result.get("data", {})
                state.intent = summary.get("intent", state.intent)

            state.conversation_history.append(
                {"role": "tool", "tool": tool_name, "result": result}
            )

            if room:
                await emit_tool_event(
                    room, "tool_end", tool_name,
                    "success" if result.get("success") else "error",
                    result.get("message", ""),
                    result.get("data"),
                )

            return json.dumps(result)

    return MykareTools()


# ─── Agent Entry Point ────────────────────────────────────────────


async def entrypoint(ctx: JobContext):
    """LiveKit calls this when a new room session starts."""

    # 1. Connect to the room
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    logger.info("Connected to room: %s", ctx.room.name)

    # 2. Create session state
    state = SessionState()

    # 3. Wait for a participant
    participant = await ctx.wait_for_participant()
    logger.info(
        "Participant joined: %s (identity: %s)",
        participant.name,
        participant.identity,
    )

    # 4. STT — Deepgram Nova-2, Indian English
    stt = deepgram.STT(model="nova-2", language="en-IN")

    # 5. TTS — Cartesia
    voice_id = os.getenv("CARTESIA_VOICE_ID", "sonic-english")
    tts = cartesia.TTS(voice=voice_id)

    # 6. LLM — OpenAI gpt-4o-mini
    llm = openai.LLM(model="gpt-4o-mini")

    # 7. Build tool function context
    fnc_ctx = build_tool_functions(state, room=ctx.room)

    # 8. Build initial chat context with system prompt
    chat_ctx = livekit_llm.ChatContext()
    chat_ctx.append(role="system", text=SYSTEM_PROMPT)

    # 9. Create the voice pipeline agent
    agent = VoicePipelineAgent(
        vad=silero.VAD.load(),
        stt=stt,
        llm=llm,
        tts=tts,
        chat_ctx=chat_ctx,
        fnc_ctx=fnc_ctx,
    )

    # 10. Track user speech
    @agent.on("user_speech_committed")
    def _on_user_speech(msg):
        text = msg.content if hasattr(msg, "content") else str(msg)
        state.conversation_history.append(
            {"role": "user", "content": text}
        )
        logger.debug("User: %s", text)

    # 11. Track agent speech
    @agent.on("agent_speech_committed")
    def _on_agent_speech(msg):
        text = msg.content if hasattr(msg, "content") else str(msg)
        state.conversation_history.append(
            {"role": "assistant", "content": text}
        )

    # 12. Start the agent
    agent.start(ctx.room, participant=participant)
    logger.info("Agent session started — session_id=%s", state.session_id)

    # 13. Opening greeting
    await agent.say(
        "Hello! Thank you for calling Mykare Health. My name is Aria, "
        "and I'm here to help you with your appointments. "
        "Could I start with your phone number, please?",
        allow_interruptions=True,
    )


# ─── Worker Setup ─────────────────────────────────────────────────


def run_agent():
    """Start the LiveKit agent worker programmatically (called from main.py
    or any other module that needs to launch the worker)."""
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))

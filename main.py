"""
main.py — FastAPI application for MyKare Voice AI Backend.

Handles REST endpoints for the frontend: health checks, LiveKit token
generation, appointment lookups, and call-session management.

Run with:
    uvicorn main:app --reload --port 8000
"""

import json
import logging
import os
import platform
import sys
import time
import traceback
import uuid
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

load_dotenv()

from db import create_call_log, db_health, get_call_log, init_db
from tools import retrieve_appointments

# ─── Logging ──────────────────────────────────────────────────────

logger = logging.getLogger("mykare-api")
logging.basicConfig(level=logging.INFO)

# ─── App Setup ────────────────────────────────────────────────────

SERVER_START_TIME = time.time()

app = FastAPI(
    title="Mykare Voice AI Backend",
    description=(
        "Backend API for the Mykare Health voice AI agent. "
        "Handles appointment booking, LiveKit token generation, "
        "and session management."
    ),
    version="1.0.0",
)

# CORS — allow all origins in development; restrict in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # TODO: restrict to frontend origin in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Startup Event ────────────────────────────────────────────────


@app.on_event("startup")
async def startup():
    """Initialise the database on server start.

    The LiveKit agent worker is a **separate** process — start it with:
        python agent.py start
    """
    init_db()
    logger.info("🚀 Mykare Voice AI Backend started (v1.0.0)")


# ─── Request / Response Models ────────────────────────────────────


class TokenRequest(BaseModel):
    room_name: str = Field(default_factory=lambda: str(uuid.uuid4()))
    participant_name: str = "user"
    participant_identity: str = Field(default_factory=lambda: str(uuid.uuid4()))


class TokenResponse(BaseModel):
    token: str
    room_name: str
    livekit_url: str


class AppointmentResponse(BaseModel):
    success: bool
    data: list
    message: str


class SummaryResponse(BaseModel):
    success: bool
    data: Optional[dict] = None
    message: str


class SessionStartRequest(BaseModel):
    session_id: str
    phone: Optional[str] = None


class TavusSessionRequest(BaseModel):
    persona_id: Optional[str] = None
    replica_id: Optional[str] = None
    conversation_name: Optional[str] = "Mykare Voice Session"
    custom_greeting: Optional[str] = None


# ─── Helpers ──────────────────────────────────────────────────────


def _format_uptime(seconds: float) -> str:
    """Convert raw seconds to a human-readable string like '2d 4h 32m 10s'."""
    s = int(seconds)
    days, s = divmod(s, 86400)
    hours, s = divmod(s, 3600)
    minutes, secs = divmod(s, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


_REQUIRED_ENV_VARS = [
    "LIVEKIT_URL",
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
    "DEEPGRAM_API_KEY",
    "CARTESIA_API_KEY",
    "GEMINI_API_KEY",
]


# ─── ENDPOINT 1: GET / ───────────────────────────────────────────


@app.get("/", tags=["General"])
async def root():
    """Ping — confirms the server is alive."""
    return {
        "message": "Mykare Voice AI Backend is running",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health",
    }


# ─── ENDPOINT 2: GET /health ─────────────────────────────────────


@app.get("/health", tags=["General"])
async def health():
    """Full system health check."""
    uptime_secs = time.time() - SERVER_START_TIME
    db = db_health()

    env_status = {
        var: ("set" if os.getenv(var) else "missing")
        for var in _REQUIRED_ENV_VARS
    }

    services = {
        "livekit": "configured" if os.getenv("LIVEKIT_URL") else "not configured",
        "deepgram": "configured" if os.getenv("DEEPGRAM_API_KEY") else "not configured",
        "cartesia": "configured" if os.getenv("CARTESIA_API_KEY") else "not configured",
        "gemini": "configured" if os.getenv("GEMINI_API_KEY") else "not configured",
    }

    overall = "ok" if db.get("status") == "ok" else "degraded"

    return {
        "status": overall,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "uptime": {
            "seconds": int(uptime_secs),
            "human": _format_uptime(uptime_secs),
        },
        "server": {
            "python_version": sys.version,
            "platform": platform.platform(),
            "environment": os.getenv("ENV", "development"),
        },
        "database": db,
        "environment_variables": env_status,
        "services": services,
    }


# ─── ENDPOINT 3: POST /token ─────────────────────────────────────


@app.post("/token", response_model=TokenResponse, tags=["LiveKit"])
async def generate_token(body: TokenRequest):
    """Generate a LiveKit room access token for the browser client."""
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")
    livekit_url = os.getenv("LIVEKIT_URL", "")

    if not api_key or not api_secret:
        raise HTTPException(
            status_code=503,
            detail="LiveKit is not configured on this server.",
        )

    try:
        from livekit import api as livekit_api

        token = (
            livekit_api.AccessToken(api_key, api_secret)
            .with_identity(body.participant_identity)
            .with_name(body.participant_name)
            .with_grants(
                livekit_api.VideoGrants(
                    room_join=True,
                    room=body.room_name,
                )
            )
        )
        jwt = token.to_jwt()

        return TokenResponse(
            token=jwt,
            room_name=body.room_name,
            livekit_url=livekit_url,
        )
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="LiveKit SDK is not installed. "
                   "Uncomment livekit lines in requirements.txt and reinstall.",
        )
    except Exception as exc:
        logger.error("Token generation failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate token: {exc}",
        )


# ─── ENDPOINT 4: GET /appointments/{phone} ───────────────────────


@app.get(
    "/appointments/{phone}",
    response_model=AppointmentResponse,
    tags=["Appointments"],
)
async def get_appointments_endpoint(phone: str):
    """Return all active appointments for a phone number."""
    result = retrieve_appointments(phone)
    return AppointmentResponse(
        success=result.get("success", False),
        data=result.get("data", []),
        message=result.get("message", ""),
    )


# ─── ENDPOINT 5: GET /summary/{session_id} ───────────────────────


@app.get(
    "/summary/{session_id}",
    response_model=SummaryResponse,
    tags=["Sessions"],
)
async def get_summary(session_id: str):
    """Return the call summary for a completed session."""
    try:
        log = get_call_log(session_id)
        if not log:
            return SummaryResponse(
                success=False,
                data=None,
                message="Session not found.",
            )
        return SummaryResponse(
            success=True,
            data=log,
            message="Call summary retrieved.",
        )
    except Exception as exc:
        logger.error("Failed to fetch summary: %s", exc)
        return SummaryResponse(
            success=False,
            data=None,
            message=f"Error retrieving session: {exc}",
        )


# ─── ENDPOINT 6: POST /session/start ─────────────────────────────


@app.post("/session/start", tags=["Sessions"])
async def start_session(body: SessionStartRequest):
    """Create a new call-log entry when a session begins."""
    try:
        log = create_call_log(body.session_id, phone=body.phone)
        return log
    except Exception as exc:
        logger.error("Failed to create session: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create session: {exc}",
        )


# ─── ENDPOINT 7: POST /avatar/session ────────────────────────────


@app.post("/avatar/session", tags=["Avatar"])
async def create_tavus_session(body: TavusSessionRequest):
    """Create a Tavus conversation and return a joinable conversation URL."""
    tavus_api_key = os.getenv("TAVUS_API_KEY")
    default_replica = os.getenv("TAVUS_REPLICA_ID")
    default_persona = os.getenv("TAVUS_PERSONA_ID")

    if not tavus_api_key:
        raise HTTPException(status_code=503, detail="Tavus is not configured on this server.")

    payload = {}
    replica_id = body.replica_id or default_replica
    persona_id = body.persona_id or default_persona

    if not replica_id and not persona_id:
        raise HTTPException(
            status_code=400,
            detail="Provide persona_id or set TAVUS_REPLICA_ID/TAVUS_PERSONA_ID in environment.",
        )

    if replica_id:
        payload["replica_id"] = replica_id
    if persona_id:
        payload["persona_id"] = persona_id
    if body.conversation_name:
        payload["conversation_name"] = body.conversation_name
    if body.custom_greeting:
        payload["custom_greeting"] = body.custom_greeting

    request = urllib.request.Request(
        url="https://tavusapi.com/v2/conversations",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-api-key": tavus_api_key,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read().decode("utf-8")
            data = json.loads(raw) if raw else {}
            return {
                "success": True,
                "data": data,
                "message": "Tavus avatar session created.",
            }
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        logger.error("Tavus create conversation failed: %s", detail)
        raise HTTPException(
            status_code=502,
            detail=f"Tavus API error: {detail}",
        )
    except Exception as exc:
        logger.error("Tavus session creation failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create Tavus session: {exc}",
        )


# ─── Global Exception Handler ────────────────────────────────────


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch any unhandled exception and return a safe JSON response."""
    logger.error(
        "Unhandled exception on %s %s:\n%s",
        request.method,
        request.url.path,
        traceback.format_exc(),
    )
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": "internal_server_error",
            "message": "An unexpected error occurred. Please try again.",
        },
    )


# ─── Main ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

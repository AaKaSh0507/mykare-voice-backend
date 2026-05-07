# Mykare Voice AI — Backend
![Python 3.11](https://img.shields.io/badge/Python-3.11-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-API-009688) ![LiveKit](https://img.shields.io/badge/LiveKit-Voice%20Agent-1f2937) ![Deployed on Fly.io](https://img.shields.io/badge/Deployed%20on-Fly.io-7B3FE4)

A production-deployed Python backend powering TalkRx, a healthcare voice AI agent that handles appointment booking through natural voice conversation.

## Overview

This backend provides a FastAPI server that exposes REST endpoints consumed by the React frontend for health checks, session lifecycle, appointments, and summaries. A LiveKit agent worker runs as a separate process to handle real-time voice calls across an STT -> LLM -> TTS pipeline. Core application data is stored in SQLite on a persistent Fly Volume, including users, appointments, slots, and call logs. During live calls, the LLM invokes a 7-tool function-calling system to identify users, manage appointments, and persist conversation outcomes.

## Architecture

```text
Browser (React)
    │
    ├── REST (HTTPS) ──→ FastAPI Server (Fly.io)
    │                        │
    │                        └── SQLite DB (Fly Volume)
    │
    └── WebRTC (WSS) ──→ LiveKit Cloud
                             │
                        Agent Worker (Fly.io)
                             ├── Deepgram STT
                             ├── OpenAI GPT-4o-mini
                             └── Cartesia TTS
```

- Browser (React): Patient-facing UI that starts sessions, renders call state, and displays summaries.
- FastAPI Server (Fly.io): HTTP API for health, token generation, appointments, and call-log lifecycle events.
- SQLite DB (Fly Volume): Persistent relational store mounted at `/data` for slots, appointments, users, and logs.
- LiveKit Cloud: Real-time media transport for WebRTC audio and data between browser and agent.
- Agent Worker (Fly.io): Voice orchestration runtime that executes the conversational pipeline and tool calls.
- Deepgram STT: Converts live speech to text with en-IN optimized recognition.
- OpenAI GPT-4o-mini: Drives intent understanding, dialog policy, and structured tool invocation.
- Cartesia TTS: Generates low-latency spoken responses streamed back to the caller.

## Tech Stack

| Layer | Technology | Purpose |
| --- | --- | --- |
| API Server | FastAPI + Uvicorn | REST endpoints, token generation |
| Voice Pipeline | LiveKit Agents 0.8.x | Real-time audio orchestration |
| STT | Deepgram Nova-2 | Speech to text, en-IN language |
| LLM | OpenAI GPT-4o-mini | Intent understanding, tool calling |
| TTS | Cartesia | Low-latency voice synthesis |
| Avatar | Tavus CVI | Lip-synced video avatar |
| Database | SQLite + Fly Volumes | Persistent appointment storage |
| Deployment | Fly.io | API server + background worker |

## Project Structure

```text
mykare-voice-backend/
├── main.py           # FastAPI app, all HTTP endpoints
├── agent.py          # LiveKit voice pipeline agent
├── tools.py          # 7 LLM tool functions
├── db.py             # SQLite schema and all CRUD ops
├── prompts.py        # Agent system prompt (Aria persona)
├── check_env.py      # Credential validation script
├── Dockerfile        # Container build for Fly.io
├── fly.toml          # API server Fly config
├── fly.worker.toml   # Background worker Fly config
├── requirements.txt  # Python dependencies
└── tests/
    ├── test_db.py    # Database layer tests
    └── test_tools.py # Tool function tests
```

## API Endpoints

| Method | Endpoint | Description |
| --- | --- | --- |
| GET | `/` | Health ping |
| GET | `/health` | Full system status (DB, services, uptime) |
| POST | `/token` | Generate LiveKit room JWT for browser |
| POST | `/session/start` | Create call log entry |
| GET | `/appointments/{phone}` | Fetch user appointments |
| GET | `/summary/{session_id}` | Fetch post-call summary |

<details>
<summary>Example response: GET /health</summary>

```json
{
  "status": "ok",
  "timestamp": "2026-05-07T11:09:25.932651+00:00",
  "uptime": {
    "seconds": 17,
    "human": "17s"
  },
  "server": {
    "python_version": "3.11.15",
    "platform": "Linux-6.12.47-fly-x86_64-with-glibc2.41",
    "environment": "production"
  },
  "database": {
    "status": "ok",
    "db_path": "/data/mykare.db",
    "tables": {
      "users": 0,
      "slots": 77,
      "appointments": 0,
      "call_logs": 0
    },
    "available_slots": 77
  },
  "environment_variables": {
    "LIVEKIT_URL": "set",
    "LIVEKIT_API_KEY": "set",
    "LIVEKIT_API_SECRET": "set",
    "DEEPGRAM_API_KEY": "set",
    "CARTESIA_API_KEY": "set",
    "GEMINI_API_KEY": "set"
  },
  "services": {
    "livekit": "configured",
    "deepgram": "configured",
    "cartesia": "configured",
    "gemini": "configured"
  }
}
```

</details>

<details>
<summary>Example response: POST /token</summary>

```json
{
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "room_name": "6f6e8c95-0c4b-4ea2-8cd3-7de95b4861f7",
  "livekit_url": "wss://test-project-urbly3yf.livekit.cloud"
}
```

</details>

## Agent Tools

| Tool | Trigger | Description |
| --- | --- | --- |
| `identify_user` | Start of every call | Validates phone, upserts user |
| `fetch_slots` | User asks for availability | Returns grouped available slots |
| `book_appointment` | User confirms a slot | Validates, books, marks slot taken |
| `retrieve_appointments` | User asks for bookings | Returns active appointments |
| `cancel_appointment` | User wants to cancel | Cancels and frees the slot |
| `modify_appointment` | User wants to reschedule | Swaps old slot for new slot |
| `end_conversation` | Call wrapping up | Saves summary to call_logs |

## Local Development

Prerequisites:
- Python 3.11+
- pip
- A `.env` file with all credentials (see `.env.example`)

1. Clone and enter the repo.

```bash
git clone https://github.com/AaKaSh0507/mykare-voice-backend
cd mykare-voice-backend
```

2. Create and activate virtual environment.

```bash
python -m venv venv
source venv/bin/activate
```

3. Install dependencies.

```bash
pip install -r requirements.txt
```

4. Copy env template and fill in credentials.

```bash
cp .env.example .env
```

5. Verify required keys are present.

```bash
python check_env.py
```

6. Start API server.

```bash
uvicorn main:app --reload --port 8000
```

7. Start agent worker in a separate terminal.

```bash
python agent.py start
```

8. Verify health endpoint.

```bash
curl http://localhost:8000/health
```

## Deployment

| Service | Platform | URL |
| --- | --- | --- |
| API Server | Fly.io | https://mykare-api.fly.dev |
| Agent Worker | Fly.io | mykare-worker (background process) |
| Database | Fly Volume | /data/mykare.db (1GB persistent disk) |

```bash
# Deploy API server
flyctl deploy --app mykare-api

# Deploy agent worker
flyctl deploy --app mykare-worker --config fly.worker.toml

# Check logs
flyctl logs --app mykare-api
flyctl logs --app mykare-worker
```

## Environment Variables

| Variable | Required | Description |
| --- | --- | --- |
| `ENV` | No | development or production |
| `DB_PATH` | No | SQLite file path, default /data/mykare.db |
| `LIVEKIT_URL` | Yes | LiveKit server WSS URL |
| `LIVEKIT_API_KEY` | Yes | LiveKit API key |
| `LIVEKIT_API_SECRET` | Yes | LiveKit API secret |
| `DEEPGRAM_API_KEY` | Yes | Deepgram STT key |
| `CARTESIA_API_KEY` | Yes | Cartesia TTS key |
| `CARTESIA_VOICE_ID` | No | Cartesia voice ID, default sonic-english |
| `OPENAI_API_KEY` | Yes | OpenAI API key |
| `TAVUS_API_KEY` | Yes | Tavus avatar API key |
| `TAVUS_REPLICA_ID` | Yes | Tavus persona/replica ID |

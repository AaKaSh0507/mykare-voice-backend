# MyKare Voice Backend

A production-grade FastAPI backend for a healthcare voice AI agent that handles appointment booking via real-time voice conversation. Built with LiveKit for real-time communication, Deepgram for speech-to-text, Cartesia for text-to-speech, and OpenAI for conversational intelligence.

---

## Prerequisites

- **Python 3.11+**
- **pip** (Python package manager)
- **SQLite 3** (bundled with Python)

---

## Local Setup

```bash
# 1. Clone the repository
git clone https://github.com/your-org/mykare-voice-backend.git
cd mykare-voice-backend

# 2. Create and activate a virtual environment
python -m venv venv
source venv/bin/activate        # macOS / Linux
# venv\Scripts\activate          # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment variables
cp .env.example .env
# Edit .env and fill in the required API keys

# 5. Initialize the database (optional — also runs on first startup)
python db.py

# 6. Start the development server
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

---

## Environment Variables

| Variable              | Service   | Description                                      |
|-----------------------|-----------|--------------------------------------------------|
| `ENV`                 | App       | Runtime environment (`development`, `production`) |
| `DB_PATH`             | App       | Path to the SQLite database file (default: `mykare.db`) |
| `LIVEKIT_URL`         | LiveKit   | WebSocket URL for the LiveKit server              |
| `LIVEKIT_API_KEY`     | LiveKit   | API key for LiveKit authentication                |
| `LIVEKIT_API_SECRET`  | LiveKit   | API secret for LiveKit authentication             |
| `DEEPGRAM_API_KEY`    | Deepgram  | API key for Deepgram speech-to-text               |
| `CARTESIA_API_KEY`    | Cartesia  | API key for Cartesia text-to-speech               |
| `OPENAI_API_KEY`      | OpenAI    | API key for OpenAI LLM (GPT-4)                   |

---

## API Endpoints

> **TODO** — Endpoint documentation will be added as routes are implemented.

| Method | Path | Description |
|--------|------|-------------|
| — | — | *Coming soon* |

---

## Project Structure

```
mykare-voice-backend/
├── .env.example        # Template for environment variables
├── .gitignore          # Git ignore rules
├── requirements.txt    # Python dependencies
├── README.md           # This file
├── main.py             # FastAPI application entrypoint
├── agent.py            # Voice AI agent logic
├── tools.py            # Agent tool definitions (function calling)
├── prompts.py          # System and user prompt templates
└── db.py               # SQLite database layer
```

---

## License

Private — All rights reserved.

import os
import sys

from dotenv import load_dotenv

load_dotenv()

REQUIRED_KEYS = [
    "LIVEKIT_URL",
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
    "DEEPGRAM_API_KEY",
    "CARTESIA_API_KEY",
    "CARTESIA_VOICE_ID",
    "GEMINI_API_KEY",
    "TAVUS_API_KEY",
    "TAVUS_REPLICA_ID",
]


def is_missing_or_placeholder(value):
    cleaned = (value or "").strip()
    return not cleaned or cleaned.startswith("PASTE_")


def main():
    missing_count = 0
    for key in REQUIRED_KEYS:
        value = os.getenv(key)
        if is_missing_or_placeholder(value):
            print(f"❌ {key} is MISSING or still a placeholder")
            missing_count += 1
        else:
            print(f"✅ {key} is set")

    if missing_count == 0:
        print("🎉 All credentials are set. Ready to run the agent.")
        sys.exit(0)

    print(f"⚠️  {missing_count} credential(s) need to be filled in.")
    sys.exit(1)


if __name__ == "__main__":
    main()

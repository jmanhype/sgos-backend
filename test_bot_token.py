#!/usr/bin/env python3
"""Quick test: verify bot token works."""
from dotenv import load_dotenv
from pathlib import Path
import os, requests

load_dotenv(Path(__file__).parent / ".env", override=True)

token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
print(f"Token: {len(token)} chars")

if not token:
    print("ERROR: No token!")
    exit(1)

resp = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=5)
print(f"Bot: {resp.json()}")

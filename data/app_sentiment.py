"""
data/app_sentiment.py
───────────────────────
Market-sentiment value for the consumer app's Home tab gauge.

The manual hand-set write path (formerly `save_sentiment`, wired to a
website POST form) has been removed — see IMPLEMENTATION_SPEC_weekly_
report_and_sentiment.md Phase 1. `GET /api/sentiment` still reads through
`load_sentiment()` below for now; automatic computation from Nifty-500
breadth data replaces this file's role entirely in Phase 2.
"""

import os
import json
from config.settings import APP_SENTIMENT_FILE, APP_SENTIMENT_DEFAULT


def load_sentiment() -> dict:
    if os.path.exists(APP_SENTIMENT_FILE):
        try:
            with open(APP_SENTIMENT_FILE, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "sentiment" in data:
                    return data
        except Exception:
            pass
    return {
        "sentiment" : APP_SENTIMENT_DEFAULT,
        "updated_at": None,
        "note"      : None,
    }

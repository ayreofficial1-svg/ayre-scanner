"""
data/app_sentiment.py
───────────────────────
Placeholder market-sentiment value for the consumer app's "Insights" tab
gauge. No scoring logic lives here yet — see master prompt §5 for the real
formula (NSE advance/decline, VIX, etc.) planned for later.

GET /api/sentiment just returns whatever is in APP_SENTIMENT_FILE (a single
JSON object), falling back to APP_SENTIMENT_DEFAULT if the file doesn't
exist yet. Edit the file by hand (or via a future admin endpoint) to change
the number the app shows — no app release required.
"""

import os
import json
import datetime
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
    }


def save_sentiment(value: int) -> dict:
    value = max(0, min(100, int(value)))
    data = {
        "sentiment" : value,
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    with open(APP_SENTIMENT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return data

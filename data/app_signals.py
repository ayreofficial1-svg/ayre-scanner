"""
data/app_signals.py
────────────────────
Persistent store for the consumer app's "Signals" tab — the list of stocks
the admin chooses to recommend from the website. Read by the Flutter app via
GET /api/signals; written by the website admin panel via POST/DELETE.

Entry schema (JSON list, newest first)
───────────────────────────────────────
  [
    {
      "id"        : "b3f1...",        # uuid4 hex
      "symbol"    : "RELIANCE",       # bare NSE symbol
      "rationale" : "Breakout above SMA44 with rising volume.",
      "date_added": "2026-07-03",     # ISO date
      "added_by"  : "raghav"          # username from session
    },
    ...
  ]

Storage is a single JSON file (same pattern as scanner/watchlist.py). Fine
for a single admin-curated list; swap for a real DB later without changing
the read/write API used by main.py.
"""

import os
import json
import uuid
import datetime
from config.settings import APP_SIGNALS_FILE


def load_signals() -> list[dict]:
    if os.path.exists(APP_SIGNALS_FILE):
        try:
            with open(APP_SIGNALS_FILE, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception:
            pass
    return []


def save_signals(signals: list[dict]) -> None:
    with open(APP_SIGNALS_FILE, "w", encoding="utf-8") as f:
        json.dump(signals, f, indent=2)


def add_signal(symbol: str, rationale: str, added_by: str) -> dict:
    """Append a new signal (newest-first) and persist it. Returns the entry."""
    entry = {
        "id"        : uuid.uuid4().hex,
        "symbol"    : symbol.strip().upper(),
        "rationale" : rationale.strip(),
        "date_added": datetime.date.today().isoformat(),
        "added_by"  : added_by or "unknown",
    }
    signals = load_signals()
    signals.insert(0, entry)
    save_signals(signals)
    return entry


def delete_signal(signal_id: str) -> bool:
    """Remove a signal by id. Returns True if something was removed."""
    signals = load_signals()
    remaining = [s for s in signals if s.get("id") != signal_id]
    if len(remaining) == len(signals):
        return False
    save_signals(remaining)
    return True

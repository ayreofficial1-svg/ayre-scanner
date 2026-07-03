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
      "added_by"  : "raghav",         # username from session
      "active"    : true              # false once deactivated via DELETE
    },
    ...
  ]

DELETE /api/signals/<id> does NOT remove the entry — it flips "active" to
false so history is preserved. GET /api/signals only shows active=true
entries to the consumer app.

Storage is a single JSON file (same pattern as scanner/watchlist.py). Fine
for a single admin-curated list; swap for a real DB later without changing
the read/write API used by main.py.
"""

import os
import json
import uuid
import datetime
from config.settings import APP_SIGNALS_FILE


def load_signals(active_only: bool = False) -> list[dict]:
    """
    Load all signals. Pass active_only=True to filter out deactivated ones
    (this is what GET /api/signals uses for the consumer app).
    """
    signals: list[dict] = []
    if os.path.exists(APP_SIGNALS_FILE):
        try:
            with open(APP_SIGNALS_FILE, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    signals = data
        except Exception:
            pass

    if active_only:
        # Entries written before the "active" field existed are treated as active.
        signals = [s for s in signals if s.get("active", True)]
    return signals


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
        "active"    : True,
    }
    signals = load_signals()
    signals.insert(0, entry)
    save_signals(signals)
    return entry


def delete_signal(signal_id: str) -> bool:
    """
    Deactivate a signal by id (sets active=False; does not remove the entry).
    Returns True if a matching, currently-active signal was found and
    deactivated; False if no such signal exists (already inactive counts
    as "nothing to do" and also returns False).
    """
    signals = load_signals()
    found = False
    for s in signals:
        if s.get("id") == signal_id and s.get("active", True):
            s["active"] = False
            found = True
            break
    if not found:
        return False
    save_signals(signals)
    return True

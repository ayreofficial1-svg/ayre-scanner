"""
data/breadth.py
────────────────
Persistence for the full Nifty-500 market-breadth snapshot (spec §4).
Written by the background breadth poller (main.py::_breadth_loop /
_refresh_full_breadth), six times a day; read by GET /api/breadth/full.

This module never triggers a Fyers call itself — it only reads/writes the
cached JSON file, so a restart doesn't blank the Home donut until the next
hourly slot. The actual paced 10-call Fyers fetch lives in
data/quotes.py::fetch_full_market_breadth.

File shape
──────────
{
  "as_of": "18 Sep 2026 11:45:12",
  "advances": 268,
  "declines": 194,
  "unchanged": 38,
  "avg_change_pct": 0.34,
  "coverage": 500
}
"""

import os
import json
import datetime

from config.settings import BREADTH_FULL_FILE

# Fixed UTC+5:30 offset — mirrors main.py's _IST; no pytz/zoneinfo dependency.
_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def load_full_breadth() -> dict | None:
    """Returns the last-saved snapshot, or None if none exists yet / unreadable."""
    if os.path.exists(BREADTH_FULL_FILE):
        try:
            with open(BREADTH_FULL_FILE, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "coverage" in data:
                    return data
        except Exception:
            pass
    return None


def save_full_breadth(
    advances: int,
    declines: int,
    unchanged: int,
    avg_change_pct: float,
    coverage: int,
) -> dict:
    data = {
        "as_of"          : datetime.datetime.now(_IST).strftime("%d %b %Y %H:%M:%S"),
        "advances"       : advances,
        "declines"       : declines,
        "unchanged"      : unchanged,
        "avg_change_pct" : avg_change_pct,
        "coverage"       : coverage,
    }
    with open(BREADTH_FULL_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return data

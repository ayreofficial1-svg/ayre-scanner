"""
data/market_close.py
---------------------
Persistence for the end-of-session "closing snapshot".

Why this exists
---------------
Every live market endpoint (/api/market, the movers, the index constituents)
is fed by the Fyers WebSocket, which main.py deliberately shuts down once the
market closes. Without something to fall back on, those endpoints have nothing
to serve after 15:30 IST - the app would show empty states overnight, at
weekends and on holidays.

main.py::_close_snapshot_loop therefore keeps one snapshot of the last
reading, refreshed about once a minute during the session. At 15:30 IST the
INDEX values in it are re-read from the latest ticks (and once more a few
seconds later) and the snapshot is marked final. Nothing else is re-collected
at the close - no scan, no REST call. main.py serves it whenever the market is
closed, until the next session overwrites it.

This module only reads and writes that JSON file. It never touches Fyers and
never raises on read - a missing or corrupt file simply means "no snapshot
yet". Same plain load/save-JSON pattern as data/breadth.py and
data/universe_stats.py.

File shape
----------
{
  "trade_date" : "2026-09-24",                    # IST date of the session
  "captured_at": "2026-09-24T15:32:00+05:30",
  "final"      : true,                            # false = last intraday reading
  "markets"    : [ {key, name, value, change, points}, ... ],
  "movers"     : {"gainers": [...], "losers": [...], "most_active": [...]},
  "constituents": {"nifty": [rows], "sensex": [rows], "bank_nifty": [rows]},
  "breadth"    : {"advances": 0, "declines": 0, "unchanged": 0} | null
}
"""

import os
import json

from config.settings import MARKET_CLOSE_FILE


def load_close_snapshot() -> dict | None:
    """Returns the last-saved snapshot, or None if there isn't a usable one."""
    if not os.path.exists(MARKET_CLOSE_FILE):
        return None
    try:
        with open(MARKET_CLOSE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    if (
        isinstance(data, dict)
        and isinstance(data.get("trade_date"), str)
        and isinstance(data.get("markets"), list)
        and data["markets"]
    ):
        return data
    return None


def save_close_snapshot(snapshot: dict) -> None:
    """
    Write the snapshot atomically (temp file, then rename) so a crash or a
    concurrent read can never see a half-written file.
    """
    tmp_path = f"{MARKET_CLOSE_FILE}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f)
    os.replace(tmp_path, MARKET_CLOSE_FILE)

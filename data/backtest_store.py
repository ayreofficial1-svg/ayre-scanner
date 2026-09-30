"""
data/backtest_store.py
──────────────────────
Persistence for the shared Backtest page.

One backtest state exists for the whole website. Whoever runs a backtest,
every user sees the same date, filter, running status and results, and a
page refresh or server restart does not lose them.

This module only reads and writes one JSON file. It never touches Fyers and
never raises on read — a missing or corrupt file just means "no backtest
yet". Same plain load/save-JSON pattern as data/universe_stats.py and
data/market_close.py (atomic temp-file-then-rename write).

File shape
──────────
{
  "revision"     : 7,                      # bumps on every change; lets clients skip unchanged polls
  "updated_at"   : "2026-09-30T11:02:10+05:30",
  "selected_date": "2026-09-26",           # date shown in the picker (shared)
  "filter"       : "all",                  # result filter (shared)
  "running_job"  : {"job_id", "date", "created_at"} | null,
  "result"       : { ...last completed backtest payload... } | null,
  "error"        : "..." | null            # last failed run's message
}
"""

import os
import json
import datetime

from config.settings import BACKTEST_STATE_FILE

_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

VALID_FILTERS = {"all", "signal", "watchlist", "none", "no_data"}


def _empty() -> dict:
    return {
        "revision": 0,
        "updated_at": None,
        "selected_date": None,
        "filter": "all",
        "running_job": None,
        "result": None,
        "error": None,
    }


def now_iso() -> str:
    return datetime.datetime.now(_IST).isoformat(timespec="seconds")


def load_backtest_state() -> dict:
    """Return the saved state, or an empty well-shaped dict. Never raises."""
    if os.path.exists(BACKTEST_STATE_FILE):
        try:
            with open(BACKTEST_STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                state = _empty()
                state.update({k: data[k] for k in state if k in data})
                if state["filter"] not in VALID_FILTERS:
                    state["filter"] = "all"
                return state
        except Exception:
            pass
    return _empty()


def save_backtest_state(state: dict) -> None:
    """Write atomically so a crash or concurrent read never sees a partial file."""
    tmp_path = f"{BACKTEST_STATE_FILE}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp_path, BACKTEST_STATE_FILE)

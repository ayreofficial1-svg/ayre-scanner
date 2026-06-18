"""
scanner/watchlist.py
────────────────────
Persistent watchlist for stocks with an imminent MACD crossover.

Lifecycle
─────────
  add     : Stock passes C1 + C2, MACD crossover is imminent but hasn't happened.
  promote : On next scan, if the crossover has now occurred, stock moves to signal.
  expire  : Entries older than WATCHLIST_TTL_DAYS (based on added date) are dropped.
  cleanup : Entries where price has fallen below SMA44 are removed intra-scan.

Entry schema (JSON)
───────────────────
  {
    "RELIANCE": {
      "added"     : "2026-04-04",   # ISO date first added to watchlist
      "close"     : 1234.50,        # close price when added
      "sma44"     : 1200.00         # SMA44 when added
    }
  }

Alert log schema (JSON)
────────────────────────
  {
    "RELIANCE": {
      "date"     : "2026-04-04",
      "time"     : "10:32:15",
      "close_price": 1240.00
    }
  }
"""

import os
import json
import datetime
from config.settings import WATCHLIST_FILE, ALERT_LOG_FILE, WATCHLIST_TTL_DAYS


# ── Watchlist I/O ─────────────────────────────────────────────────────────────

def load_watchlist() -> dict:
    if os.path.exists(WATCHLIST_FILE):
        try:
            with open(WATCHLIST_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_watchlist(watchlist: dict) -> None:
    with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
        json.dump(watchlist, f, indent=2)


# ── Watchlist operations ──────────────────────────────────────────────────────

def clean_watchlist(watchlist: dict) -> dict:
    """
    Remove entries older than WATCHLIST_TTL_DAYS.
    Uses the 'added' date for TTL calculation.
    """
    today  = datetime.date.today()
    cutoff = today - datetime.timedelta(days=WATCHLIST_TTL_DAYS)
    cleaned = {}
    for sym, data in watchlist.items():
        added_str = data.get("added", "2000-01-01")
        try:
            added_date = datetime.date.fromisoformat(added_str)
        except ValueError:
            continue
        if added_date >= cutoff:
            cleaned[sym] = data
    return cleaned


def add_to_watchlist(
    watchlist : dict,
    symbol    : str,
    close     : float,
    sma44     : float,
) -> None:
    """
    Add a symbol to the watchlist.
    If the symbol is already present, it is NOT overwritten — the original
    added date is preserved so TTL remains accurate.
    """
    if symbol in watchlist:
        return   # already tracked; do not reset TTL

    watchlist[symbol] = {
        "added": str(datetime.date.today()),
        "close": round(close, 2),
        "sma44": round(sma44, 2),
    }


def remove_from_watchlist(watchlist: dict, symbol: str) -> None:
    watchlist.pop(symbol, None)


# ── Alert log I/O ─────────────────────────────────────────────────────────────

def load_alert_log() -> dict:
    if os.path.exists(ALERT_LOG_FILE):
        try:
            with open(ALERT_LOG_FILE, encoding="utf-8") as f:
                log = json.load(f)
            if isinstance(log, dict):
                for entry in log.values():
                    if (
                        isinstance(entry, dict)
                        and "close_price" not in entry
                        and "buy_price" in entry
                    ):
                        entry["close_price"] = entry.pop("buy_price")
                return log
        except Exception:
            pass
    return {}


def save_alert_log(log: dict) -> None:
    with open(ALERT_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2)


def clean_alert_log(log: dict) -> dict:
    """Remove entries from previous trading days."""
    today = str(datetime.date.today())
    return {k: v for k, v in log.items() if v.get("date") == today}


def is_already_alerted(symbol: str, log: dict) -> bool:
    return symbol in log


def mark_alerted(symbol: str, log: dict, close_price: float) -> None:
    log[symbol] = {
        "date"       : str(datetime.date.today()),
        "time"       : datetime.datetime.now().strftime("%H:%M:%S"),
        "close_price": close_price,
    }

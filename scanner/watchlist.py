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
      "date"     : "2026-04-04",       # IST date first alerted (Trade Ready)
      "time"     : "10:32:15",         # IST time first alerted (Trade Ready)
      "close_price": 1240.00
    }
  }

  The "date"/"time" pair here is the source of truth for a signal's
  Trade Ready timestamp: it is written once, the first time a symbol
  is newly alerted on a given trading day, and left untouched by later
  scans that same day even if the symbol keeps qualifying as a signal.
  See scanner/engine.py, which reads it back as `trade_ready_at`.
"""

import os
import json
import datetime
from config.settings import WATCHLIST_FILE, ALERT_LOG_FILE, WATCHLIST_TTL_DAYS

# Fixed UTC+5:30 offset — no pytz/zoneinfo dependency.
# Mirrors main.py's _IST: ensures watchlist/alert-log dates and the
# Trade Ready timestamp are always IST wall-clock, regardless of the
# host timezone (e.g. Railway, which runs UTC).
_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


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
    today  = datetime.datetime.now(_IST).date()
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
        "added": str(datetime.datetime.now(_IST).date()),
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
    today = str(datetime.datetime.now(_IST).date())
    return {k: v for k, v in log.items() if v.get("date") == today}


def is_already_alerted(symbol: str, log: dict) -> bool:
    return symbol in log


def mark_alerted(symbol: str, log: dict, close_price: float) -> None:
    now_ist = datetime.datetime.now(_IST)
    log[symbol] = {
        "date"       : str(now_ist.date()),
        "time"       : now_ist.strftime("%H:%M:%S"),
        "close_price": close_price,
    }

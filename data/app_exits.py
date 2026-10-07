"""
data/app_exits.py
──────────────────
Persistent store for exit calls — the "time to get out of this pick" messages
the admin sends from the website's Signals tab (below the signal form).

An exit call is an event, not a live listing: it is saved once, pushed to the
phones once, and then kept as history. It carries exactly three values.

Entry schema (JSON list, newest first)
───────────────────────────────────────
  [
    {
      "id"         : "b3f1...",          # uuid4 hex
      "symbol"     : "RELIANCE",         # stock name, as picked on the website
      "profit"     : 120.0,              # ₹ per share; negative = a loss
      "exit_price" : 2850.0,             # ₹ (middle of the range when one was given)
      "exit_low"   : 2845.0,             # ₹, optional exit range
      "exit_high"  : 2855.0,             # ₹, optional exit range
      "created_at" : "<ISO timestamp>",  # bookkeeping only
      "added_by"   : "raghav"            # username from session, bookkeeping only
    },
    ...
  ]

Storage is a single JSON file, same pattern as data/app_devices.py: a lock
guards the read-modify-write cycle and the file is replaced atomically so a
crash never leaves half a file.
"""

import os
import json
import uuid
import threading
import datetime

from config.settings import APP_EXITS_FILE

_lock = threading.Lock()

# History is for the admin's reference only; the app never reads it.
_MAX_ENTRIES = 500


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _load() -> list[dict]:
    if not os.path.exists(APP_EXITS_FILE):
        return []
    try:
        with open(APP_EXITS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []
    except Exception:
        return []


def _save(entries: list[dict]) -> None:
    tmp = f"{APP_EXITS_FILE}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)
    os.replace(tmp, APP_EXITS_FILE)


def load_exits() -> list[dict]:
    """All saved exit calls, newest first."""
    with _lock:
        return _load()


def add_exit(symbol: str, profit: float, exit_price: float, added_by: str | None,
             exit_low: float | None = None, exit_high: float | None = None) -> dict:
    """
    Save a new exit call (newest first) and return it. exit_price is the single
    price (the middle of the range when a range was given); exit_low/exit_high
    are the optional exit range and are stored only when both are present.
    """
    entry = {
        "id"        : uuid.uuid4().hex,
        "symbol"    : " ".join(str(symbol or "").split()).upper(),
        "profit"    : float(profit),
        "exit_price": float(exit_price),
        "created_at": _now_iso(),
        "added_by"  : added_by or "unknown",
    }
    if exit_low is not None and exit_high is not None:
        entry["exit_low"] = float(exit_low)
        entry["exit_high"] = float(exit_high)
    with _lock:
        entries = _load()
        entries.insert(0, entry)
        _save(entries[:_MAX_ENTRIES])
    return entry


def delete_exit(exit_id: str) -> bool:
    """Remove a saved exit call from the history. Returns True if it existed."""
    with _lock:
        entries = _load()
        kept = [e for e in entries if e.get("id") != exit_id]
        if len(kept) == len(entries):
            return False
        _save(kept)
        return True

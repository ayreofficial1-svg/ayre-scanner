"""
data/entry_hits.py
──────────────────
ADMIN-ONLY store for entry detection (Phase 5). One JSON file on the persistent
volume, atomic writes, restart-safe.

  {
    "hits":  [ {...}, ... ],              # newest first, kept ENTRY_STORE_RETENTION_DAYS days
    "arms":  { "<signal_id>": {...} },    # what the detector is watching for admin signals
    "meta":  { "days": { "2026-10-06": {"first_sweep": "09:16:04", "emails": 2} },
               "last_sweep_at": "...", "last_hit_at": "..." }
  }

Hit record
──────────
  id, key, day, kind ("admin" | "scanner"), symbol, signal_id, level,
  direction ("up" | "down" | "touch"), detected_at (IST ISO), price_at_detection,
  day_high, day_low, source ("sweep"), extended (bool), extended_pct,
  late_start (bool), exact_minute, status, status_at, status_by, draft_signal_id,
  plus a few scanner details (change_pct, cross_type, trade_ready_at).

Statuses: new | reviewed | dismissed | draft_created | entry_reached_published

"Once per day" lives in the hit KEYS ("adm:<signal>:<entry>:<day>" and
"scn:<symbol>:<day>"): a restart can never create a duplicate, and the next
trading day has new keys by itself.

ONE-WAY WALL: this module (like scanner/entry_detect.py) never imports the push
modules or the app signals feed, and no app-facing endpoint reads it.
"""

import os
import json
import uuid
import threading
import datetime

from config.settings import ENTRY_HITS_FILE, ENTRY_STORE_RETENTION_DAYS
from config.persistence import atomic_write_json

_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
_lock = threading.RLock()
_state: dict | None = None

DONE_STATUSES = ("dismissed", "entry_reached_published")
VALID_STATUSES = ("new", "reviewed", "dismissed", "draft_created", "entry_reached_published")


def today_ist() -> str:
    return datetime.datetime.now(_IST).date().isoformat()


def now_ist_iso() -> str:
    return datetime.datetime.now(_IST).isoformat(timespec="seconds")


def _blank() -> dict:
    return {"hits": [], "arms": {}, "meta": {"days": {}}}


def _load_file() -> dict:
    if not os.path.exists(ENTRY_HITS_FILE):
        return _blank()
    try:
        with open(ENTRY_HITS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _blank()
        data.setdefault("hits", [])
        data.setdefault("arms", {})
        meta = data.setdefault("meta", {})
        meta.setdefault("days", {})
        return data
    except Exception:
        return _blank()


def _get() -> dict:
    global _state
    if _state is None:
        _state = _load_file()
        _prune(_state)
    return _state


def _prune(state: dict) -> None:
    cutoff = (datetime.datetime.now(_IST).date()
              - datetime.timedelta(days=ENTRY_STORE_RETENTION_DAYS)).isoformat()
    state["hits"] = [h for h in state["hits"] if str(h.get("day", "")) >= cutoff]
    state["meta"]["days"] = {d: v for d, v in state["meta"]["days"].items() if d >= cutoff}


def _save(state: dict) -> None:
    atomic_write_json(ENTRY_HITS_FILE, state, indent=1)


def mutate(fn) -> bool:
    """Run fn(state) under the lock; save when it returns True. Returns fn's result."""
    with _lock:
        state = _get()
        changed = bool(fn(state))
        if changed:
            _prune(state)
            _save(state)
        return changed


# ── Reads (all return copies) ────────────────────────────────────────────────
def hits(days: int = 1) -> list[dict]:
    cutoff = (datetime.datetime.now(_IST).date() - datetime.timedelta(days=max(0, days - 1))).isoformat()
    with _lock:
        return [dict(h) for h in _get()["hits"] if str(h.get("day", "")) >= cutoff]


def get_hit(hit_id: str) -> dict | None:
    with _lock:
        for h in _get()["hits"]:
            if h.get("id") == hit_id:
                return dict(h)
    return None


def has_key(key: str) -> bool:
    with _lock:
        return any(h.get("key") == key for h in _get()["hits"])


def hits_today_count() -> int:
    day = today_ist()
    with _lock:
        return sum(1 for h in _get()["hits"] if h.get("day") == day)


def arms_snapshot() -> dict:
    with _lock:
        return json.loads(json.dumps(_get()["arms"]))


def meta_snapshot() -> dict:
    with _lock:
        return json.loads(json.dumps(_get()["meta"]))


# ── Writes ───────────────────────────────────────────────────────────────────
def add_hit(hit: dict) -> dict | None:
    """Insert a hit unless its key already exists. Returns the stored record or None."""
    record = {"id": uuid.uuid4().hex, "status": "new", **hit}
    result: dict = {}

    def _do(state):
        if any(h.get("key") == record["key"] for h in state["hits"]):
            return False
        state["hits"].insert(0, record)
        state["meta"]["last_hit_at"] = record.get("detected_at")
        # a dismissed/published decision belongs to the old level; a fresh hit is "new"
        result["rec"] = dict(record)
        return True

    mutate(_do)
    return result.get("rec")


def set_status(hit_id: str, status: str, by: str | None, *, clear: tuple = (), **extra) -> dict | None:
    """
    Change a hit's admin status (and stop/continue detection for its signal).
    `extra` values (when not None) are stored on the hit; field names in `clear`
    are removed from it (used to unlink a draft signal so a new one can be made).
    """
    if status not in VALID_STATUSES:
        return None
    result: dict = {}

    def _do(state):
        for h in state["hits"]:
            if h.get("id") != hit_id:
                continue
            h["status"] = status
            h["status_at"] = now_ist_iso()
            h["status_by"] = by or "unknown"
            h.update({k: v for k, v in extra.items() if v is not None})
            for field in clear:
                h.pop(field, None)
            sid = h.get("signal_id")
            arm = state["arms"].get(sid) if sid else None
            if arm is not None and h.get("kind") == "admin":
                arm["done"] = status in DONE_STATUSES
            result["rec"] = dict(h)
            return True
        return False

    mutate(_do)
    return result.get("rec")


def update_hit(hit_id: str, **fields) -> None:
    def _do(state):
        for h in state["hits"]:
            if h.get("id") == hit_id:
                h.update(fields)
                return True
        return False
    mutate(_do)


def rearm_signal(signal_id: str) -> bool:
    """Admin 'Re-arm': clear the done flag and the baseline so detection starts fresh."""
    def _do(state):
        arm = state["arms"].get(signal_id)
        if arm is None:
            return False
        arm["done"] = False
        arm["rearm"] = True      # detector re-baselines on its next sweep
        return True
    return mutate(_do)

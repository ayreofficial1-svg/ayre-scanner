"""
data/push_audit.py
──────────────────
Bounded audit log of every MANUAL notification the admin sent (or that the
guard refused). One JSON file on the persistent volume, newest first, capped
at PUSH_AUDIT_LOG_MAX entries (oldest dropped), atomic writes.

Entry fields
────────────
  id, at (ISO UTC), day (IST date), admin, type, key (stock / message id),
  status  : "sending" | "done" | "refused"
  audience, attempted, sent, failed   (filled when the send finishes)
  reason  : why it was refused (status == "refused")

The log is also the source of truth for the server-side duplicate window and
the daily cap, so both survive a restart.
"""

import os
import json
import uuid
import threading
import datetime

from config.settings import PUSH_AUDIT_LOG_FILE, PUSH_AUDIT_LOG_MAX
from config.persistence import atomic_write_json

_lock = threading.Lock()
_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def ist_day(moment: datetime.datetime | None = None) -> str:
    return (moment or now_utc()).astimezone(_IST).date().isoformat()


def _load() -> list[dict]:
    if not os.path.exists(PUSH_AUDIT_LOG_FILE):
        return []
    try:
        with open(PUSH_AUDIT_LOG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []
    except Exception:
        return []


def _save(entries: list[dict]) -> None:
    atomic_write_json(PUSH_AUDIT_LOG_FILE, entries[:PUSH_AUDIT_LOG_MAX])


def append(entry: dict) -> dict:
    """Insert a new entry (newest first) and return it."""
    row = {
        "id": uuid.uuid4().hex,
        "at": now_utc().isoformat(),
        "day": ist_day(),
        **entry,
    }
    with _lock:
        entries = _load()
        entries.insert(0, row)
        _save(entries)
    return row


def update(entry_id: str, **fields) -> None:
    with _lock:
        entries = _load()
        for e in entries:
            if e.get("id") == entry_id:
                e.update(fields)
                _save(entries)
                return


def recent(limit: int = 20) -> list[dict]:
    with _lock:
        return _load()[: max(1, int(limit))]


def sends_today() -> int:
    """Manual sends counted against today's cap (refusals do not count)."""
    today = ist_day()
    with _lock:
        return sum(
            1 for e in _load()
            if e.get("day") == today and e.get("status") in ("sending", "done")
        )


def last_send(kind: str, key: str, within_seconds: float | None = None) -> dict | None:
    """Most recent non-refused send of this type + key (optionally within N seconds)."""
    cutoff = None
    if within_seconds is not None:
        cutoff = now_utc() - datetime.timedelta(seconds=within_seconds)
    with _lock:
        for e in _load():
            if e.get("type") != kind or e.get("key") != key:
                continue
            if e.get("status") not in ("sending", "done"):
                continue
            if cutoff is not None:
                try:
                    if datetime.datetime.fromisoformat(str(e.get("at"))) < cutoff:
                        return None
                except ValueError:
                    return None
            return e
    return None

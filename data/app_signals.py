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


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _is_visible(entry: dict, now: datetime.datetime | None = None) -> bool:
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if not entry.get("enabled", entry.get("active", True)):
        return False
    start = str(entry.get("start_at") or "").strip()
    end = str(entry.get("end_at") or "").strip()
    try:
        start_at = datetime.datetime.fromisoformat(start) if start else None
        if start_at and start_at.tzinfo is None:
            start_at = start_at.replace(tzinfo=datetime.timezone.utc)
        if start_at and start_at > now:
            return False
    except ValueError:
        pass
    try:
        end_at = datetime.datetime.fromisoformat(end) if end else None
        if end_at and end_at.tzinfo is None:
            end_at = end_at.replace(tzinfo=datetime.timezone.utc)
        if end_at and end_at <= now:
            return False
    except ValueError:
        pass
    return True


def _normalize_signal(entry: dict) -> dict:
    active = bool(entry.get("active", entry.get("enabled", True)))
    return {
        **entry,
        "symbol": str(entry.get("symbol") or "").strip().upper(),
        "rationale": str(entry.get("rationale") or "").strip(),
        "active": active,
        "enabled": bool(entry.get("enabled", active)),
        "featured": bool(entry.get("featured", False)),
        "pinned": bool(entry.get("pinned", False)),
        "display_order": int(entry.get("display_order") or 0),
        "category": (entry.get("category") or "").strip() or None,
        "image_url": (entry.get("image_url") or "").strip() or None,
        "start_at": (entry.get("start_at") or "").strip() or None,
        "end_at": (entry.get("end_at") or "").strip() or None,
        "tags": entry.get("tags") if isinstance(entry.get("tags"), list) else [],
    }


def _sort_key(entry: dict) -> tuple[int, int, str]:
    pinned = 0 if entry.get("pinned") else 1
    order = int(entry.get("display_order") or 0)
    updated = str(entry.get("updated_at") or entry.get("date_added") or "")
    return (pinned, order, updated)


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

    normalized = [_normalize_signal(s) for s in signals if isinstance(s, dict)]
    if active_only:
        normalized = [s for s in normalized if _is_visible(s)]
    return sorted(normalized, key=_sort_key)


def save_signals(signals: list[dict]) -> None:
    with open(APP_SIGNALS_FILE, "w", encoding="utf-8") as f:
        json.dump(signals, f, indent=2)


def add_signal(symbol: str, rationale: str, added_by: str, **fields) -> dict:
    """Append a new signal (newest-first) and persist it. Returns the entry."""
    entry = {
        "id"        : uuid.uuid4().hex,
        "symbol"    : symbol.strip().upper(),
        "rationale" : rationale.strip(),
        "date_added": datetime.date.today().isoformat(),
        "added_by"  : added_by or "unknown",
        "active"    : True,
        "enabled"   : bool(fields.get("enabled", True)),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        **fields,
    }
    entry = _normalize_signal(entry)
    signals = load_signals()
    signals.insert(0, entry)
    save_signals(signals)
    return entry


def update_signal(signal_id: str, **fields) -> dict | None:
    signals = load_signals()
    for idx, signal in enumerate(signals):
        if signal.get("id") == signal_id:
            updated = {**signal, **fields, "updated_at": _now_iso()}
            if "symbol" in fields:
                updated["symbol"] = str(fields["symbol"]).strip().upper()
            if "enabled" in fields:
                updated["active"] = bool(fields["enabled"])
            signals[idx] = _normalize_signal(updated)
            save_signals(signals)
            return signals[idx]
    return None


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
            s["enabled"] = False
            s["updated_at"] = _now_iso()
            found = True
            break
    if not found:
        return False
    save_signals(signals)
    return True

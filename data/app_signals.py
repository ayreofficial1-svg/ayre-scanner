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
      "active"    : true,             # false once deactivated via DELETE
      "entry_price": 2850.0,          # optional — website admin panel field
      "entry_low"  : 2840.0,          # optional entry range; entry_price = its middle
      "entry_high" : 2860.0,
      "exit_price" : 3050.0,          # optional
      "exit_low"   : 3045.0,          # optional exit range around exit_price
      "exit_high"  : 3055.0,
      "stop_loss"  : 2760.0           # optional
    },
    ...
  ]

DELETE /api/signals/<id> does NOT remove the entry — it flips "active" to
false so history is preserved. GET /api/signals only shows active=true
entries to the consumer app.

Publication gate (Phase 3)
──────────────────────────
Every signal also has a publication state, separate from "active"/"enabled":

  "published"   : true | false   # false = Draft (admin only)
  "published_at": ISO timestamp  # set by set_published()
  "published_by": username       # set by set_published()

  • New signals are created as Drafts (published=False).
  • LEGACY records that have no "published" field count as Published, so
    every signal that was live before this phase stays live.
  • The consumer app feed shows a signal only when it is Published AND
    visible by the existing rules (enabled + start_at/end_at window).
  • Only set_published() changes the publication fields. add_signal() and
    update_signal() silently drop any client-supplied value for them, so a
    normal save can never publish anything.

Storage is a single JSON file (same pattern as scanner/watchlist.py). Fine
for a single admin-curated list; swap for a real DB later without changing
the read/write API used by main.py.
"""

import os
import json
import uuid
import datetime
from config.settings import APP_SIGNALS_FILE
from config.persistence import atomic_write_json


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


# Public alias — there is exactly one definition of "visible" (enabled +
# schedule window).
def is_visible(entry: dict, now: datetime.datetime | None = None) -> bool:
    return _is_visible(entry, now)


# Fields only set_published() may change. Anything a client sends for them
# through add_signal()/update_signal() is dropped.
_PUBLICATION_FIELDS = ("published", "published_at", "published_by", "unpublished_at")


# Entry-reached publication (Phase 6). Only set_entry_reached() writes these;
# a client save can never set them.
#   public (shown in the app feed once published): entry_reached_at,
#     entry_reached_price, entry_reached_extended
#   admin only (stripped from the app feed): entry_reached_level,
#     entry_reached_hit_id, entry_reached_by, entry_reached_published_at
ENTRY_REACHED_PUBLIC_FIELDS = ("entry_reached_at", "entry_reached_price", "entry_reached_extended")
ENTRY_REACHED_ADMIN_FIELDS = ("entry_reached_level", "entry_reached_hit_id",
                              "entry_reached_by", "entry_reached_published_at")
ENTRY_REACHED_FIELDS = ENTRY_REACHED_PUBLIC_FIELDS + ENTRY_REACHED_ADMIN_FIELDS


def _is_published(entry: dict) -> bool:
    """Legacy records (no "published" field) count as Published."""
    value = entry.get("published")
    if value is None:
        return True
    return bool(value)


def is_live(entry: dict, now: datetime.datetime | None = None) -> bool:
    """What the consumer app may show: Published AND visible."""
    return _is_published(entry) and _is_visible(entry, now)


def _without_publication_fields(fields: dict) -> dict:
    return {k: v for k, v in fields.items()
            if k not in _PUBLICATION_FIELDS and k not in ENTRY_REACHED_FIELDS}


def _normalize_signal(entry: dict) -> dict:
    active = bool(entry.get("active", entry.get("enabled", True)))
    return {
        **entry,
        "symbol": str(entry.get("symbol") or "").strip().upper(),
        "rationale": str(entry.get("rationale") or "").strip(),
        "active": active,
        "enabled": bool(entry.get("enabled", active)),
        "published": _is_published(entry),
        "featured": bool(entry.get("featured", False)),
        "pinned": bool(entry.get("pinned", False)),
        "display_order": int(entry.get("display_order") or 0),
        "category": (entry.get("category") or "").strip() or None,
        "image_url": (entry.get("image_url") or "").strip() or None,
        "start_at": (entry.get("start_at") or "").strip() or None,
        "end_at": (entry.get("end_at") or "").strip() or None,
        "tags": entry.get("tags") if isinstance(entry.get("tags"), list) else [],
        "entry_price": _to_float_or_none(entry.get("entry_price")),
        # Entry range (website "Draft & publish signals" panel). entry_price is kept
        # equal to the middle of the range, so detection and the app keep working.
        "entry_low": _to_float_or_none(entry.get("entry_low")),
        "entry_high": _to_float_or_none(entry.get("entry_high")),
        "exit_price": _to_float_or_none(entry.get("exit_price")),
        # Exit range around exit_price (same rules as the exit alert's range).
        "exit_low": _to_float_or_none(entry.get("exit_low")),
        "exit_high": _to_float_or_none(entry.get("exit_high")),
        "stop_loss": _to_float_or_none(entry.get("stop_loss")),
    }


def _to_float_or_none(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sort_key(entry: dict) -> tuple[int, int, str]:
    pinned = 0 if entry.get("pinned") else 1
    order = int(entry.get("display_order") or 0)
    updated = str(entry.get("updated_at") or entry.get("date_added") or "")
    return (pinned, order, updated)


def load_signals(active_only: bool = False, published_only: bool = False) -> list[dict]:
    """
    Load all signals. Pass active_only=True to filter out deactivated /
    out-of-window ones, and published_only=True to filter out Drafts and
    unpublished signals. The consumer app feed (GET /api/signals) uses both;
    the admin website view uses neither.
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
    if published_only:
        normalized = [s for s in normalized if _is_published(s)]
    return sorted(normalized, key=_sort_key)


def save_signals(signals: list[dict]) -> None:
    atomic_write_json(APP_SIGNALS_FILE, signals)


def add_signal(symbol: str, rationale: str, added_by: str, **fields) -> dict:
    """
    Append a new signal (newest-first) and persist it. Returns the entry.
    The signal is always created as a Draft (published=False); use
    set_published() to publish it.
    """
    fields = _without_publication_fields(fields)
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
        "published": False,
    }
    entry = _normalize_signal(entry)
    signals = load_signals()
    signals.insert(0, entry)
    save_signals(signals)
    return entry


def _level_changed(before, after) -> bool:
    a, b = _to_float_or_none(before), _to_float_or_none(after)
    if a is None and b is None:
        return False
    if a is None or b is None:
        return True
    return abs(a - b) > 1e-9


def update_signal(signal_id: str, **fields) -> dict | None:
    """Edit a signal. Never changes its publication state."""
    fields = _without_publication_fields(fields)
    signals = load_signals()
    for idx, signal in enumerate(signals):
        if signal.get("id") == signal_id:
            updated = {**signal, **fields, "updated_at": _now_iso()}
            # Edit rule (Phase 6): published entry-reached facts described the OLD
            # level, so they are withdrawn from the app view at save time.
            if signal.get("entry_reached_at") and (
                _level_changed(signal.get("entry_price"), updated.get("entry_price"))
                or str(updated.get("symbol") or "").strip().upper() != str(signal.get("symbol") or "").strip().upper()
            ):
                for k in ENTRY_REACHED_FIELDS:
                    updated.pop(k, None)
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


def set_published(signal_id: str, published: bool, by: str | None = None) -> dict | None:
    """
    Publish (Draft -> visible in the app) or unpublish (back to Draft) a
    signal. Returns the updated signal, or None if the id does not exist.

    Bookkeeping only: does NOT touch updated_at (that would reorder the feed)
    and sends nothing.
    """
    signals = load_signals()
    for idx, signal in enumerate(signals):
        if signal.get("id") != signal_id:
            continue
        updated = dict(signal)
        updated["published"] = bool(published)
        if published:
            updated["published_at"] = _now_iso()
            updated["published_by"] = by or "unknown"
            updated.pop("unpublished_at", None)
        else:
            updated["unpublished_at"] = _now_iso()
            # Unpublish rule (Phase 6): entry-reached facts leave with the signal.
            for k in ENTRY_REACHED_FIELDS:
                updated.pop(k, None)
        signals[idx] = _normalize_signal(updated)
        save_signals(signals)
        return signals[idx]
    return None


def set_notification_state(signal_id: str, kind: str) -> None:
    """
    Record that a MANUAL notification went out for this signal.

      kind == "new"    -> notified_at (+ the price levels at that moment)
      kind == "update" -> update_notified_at (+ refreshed levels)

    The stored levels let the website show "changed since last notification".
    Bookkeeping only: does not touch updated_at or the publication state.
    """
    signals = load_signals()
    for idx, signal in enumerate(signals):
        if signal.get("id") != signal_id:
            continue
        updated = dict(signal)
        now = _now_iso()
        if kind == "new":
            updated["notified_at"] = now
        else:
            updated["update_notified_at"] = now
        updated["notified_levels"] = {
            "symbol": updated.get("symbol"),
            "entry_price": updated.get("entry_price"),
            "entry_low": updated.get("entry_low"),
            "entry_high": updated.get("entry_high"),
            "exit_price": updated.get("exit_price"),
            "exit_low": updated.get("exit_low"),
            "exit_high": updated.get("exit_high"),
            "stop_loss": updated.get("stop_loss"),
        }
        signals[idx] = _normalize_signal(updated)
        save_signals(signals)
        return


def set_entry_reached(signal_id: str, fields: dict | None) -> dict | None:
    """
    Publish (fields = {at, price, extended, level, hit_id, by}) or withdraw
    (fields = None) the "entry reached" facts on a signal. Called only from an
    admin endpoint. Bookkeeping only: does not touch updated_at, the
    publication state, or send anything. Returns the signal or None.
    """
    signals = load_signals()
    for idx, signal in enumerate(signals):
        if signal.get("id") != signal_id:
            continue
        updated = dict(signal)
        for k in ENTRY_REACHED_FIELDS:
            updated.pop(k, None)
        if fields:
            updated["entry_reached_at"] = fields.get("at")
            updated["entry_reached_price"] = fields.get("price")
            updated["entry_reached_extended"] = bool(fields.get("extended"))
            updated["entry_reached_level"] = fields.get("level")
            updated["entry_reached_hit_id"] = fields.get("hit_id")
            updated["entry_reached_by"] = fields.get("by") or "unknown"
            updated["entry_reached_published_at"] = _now_iso()
        signals[idx] = _normalize_signal(updated)
        save_signals(signals)
        return signals[idx]
    return None

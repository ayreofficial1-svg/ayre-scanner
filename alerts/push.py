"""
alerts/push.py
──────────────
Push notifications to the mobile app through Firebase Cloud Messaging (FCM).

This is the phone-delivery counterpart of alerts/notify.py (terminal / sound /
desktop / email). It is deliberately separate from fire_alert(): fire_alert
reports the *raw scanner's* hits to the operator, whereas users of the app
should only be pushed the picks an admin has curated and published — the
same picks GET /api/signals serves.

NOTHING in this module decides to send. The only caller of send_to_devices()
is alerts/manual_push.py, which is reached only from admin website endpoints
(explicit button + confirmation + server-side guard + audit log). The old
fire-and-forget notify_*/broadcast helpers were removed in the final
hardening phase so no automatic path can be wired back in by accident.

Design rules
────────────
  • Never raise into a request. Sending runs on a background thread and every
    failure is logged and swallowed — publishing a signal must succeed
    whether or not FCM is reachable or configured.
  • Optional dependency. If firebase-admin isn't installed or no credentials
    are set, everything here is a quiet no-op (see is_configured()).
  • Self-cleaning. Tokens FCM reports as unregistered are removed from the
    device registry so it doesn't accumulate dead installs.

Notification kinds (the app tells them apart by data["type"])
─────────────────────────────────────────────────────────────
  signal         a published pick, announced by the admin → opens Signals
  signal_update  an already-announced pick changed → opens Signals
  entry_reached  a published pick reached its entry level → opens Signals
  exit           exit call (stock, profit, exit price) → opens the Alerts screen
  general        custom message from the Notifications panel

Wording is plain and short: a 2-3 word heading, the stock, one short line.
Each kind has a pool of variations; one is picked at random and never the
same as the previous one. The app keeps a matching pool
(lib/services/notification_copy.dart) for entries it records itself.

Credentials: see FIREBASE_SERVICE_ACCOUNT_* in config/settings.py.
"""

import os
import json
import base64
import datetime
import random
import re
import threading

from config.settings import (
    FIREBASE_SERVICE_ACCOUNT_JSON,
    FIREBASE_SERVICE_ACCOUNT_BASE64,
    PUSH_ANDROID_CHANNEL_ID,
)
from data.app_devices import list_devices, remove_tokens
from auth import firebase_app as _firebase_app

try:
    import firebase_admin
    from firebase_admin import credentials as _fb_credentials
    from firebase_admin import messaging as _fb_messaging
    _SDK_AVAILABLE = True
except ImportError:          # firebase-admin not installed → push disabled
    _SDK_AVAILABLE = False

_APP_NAME = "ayre-push"
_BATCH_SIZE = 500            # FCM's per-multicast ceiling

_init_lock = threading.Lock()
_app = None
_init_failed = False

# What the most recent send did, kept so GET /api/push/status can show it —
# otherwise a push that FCM rejects (or that had nobody to go to) leaves no
# trace on the website. In memory only; resets on restart.
_last_send: dict | None = None


def last_send() -> dict | None:
    """Outcome of the most recent send attempt, or None if none since startup."""
    return dict(_last_send) if _last_send else None


def _record(title: str, data: dict | None, result: dict, errors: dict, note: str = "") -> None:
    global _last_send
    _last_send = {
        "at"     : datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "title"  : title,
        "type"   : (data or {}).get("type"),
        **result,
        "errors" : errors,
        "note"   : note,
    }


# ── Credentials / initialisation ─────────────────────────────────────────────

def _credential_source() -> str | None:
    """Which credential the environment provides, or None."""
    return _firebase_app.credential_source()


def _ensure_app():
    """Shared Firebase app (auth/firebase_app.py). None if push is unavailable."""
    global _app, _init_failed
    if _app is not None:
        return _app
    _app = _firebase_app.get_app()
    if _app is None:
        _init_failed = True
    return _app


def is_configured() -> bool:
    """True when push can actually be attempted (SDK installed + credentials present)."""
    return _SDK_AVAILABLE and _credential_source() is not None and not _init_failed


# ── Sending ──────────────────────────────────────────────────────────────────

def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def send_to_devices(
    devices: list[dict],
    title: str,
    body: str,
    data: dict | None = None,
) -> dict:
    """
    Send one notification to the given devices. Blocking — alerts/manual_push.py
    runs it on a background thread after its guard has approved the send.

    Returns {"attempted", "sent", "failed", "pruned"}.
    """
    result = {"attempted": 0, "sent": 0, "failed": 0, "pruned": 0}
    errors: dict[str, int] = {}
    tokens = [d["token"] for d in devices if d.get("token")]
    if not tokens:
        _record(title, data, result, errors,
                "No registered device to send to (none registered, or none opted in to this kind of alert).")
        print("   ⚠️   Push: nothing sent — no registered device for this alert")
        return result

    app = _ensure_app()
    if app is None:
        _record(title, data, result, errors, "Firebase is not initialised on the server.")
        print("   ⚠️   Push: nothing sent — Firebase is not initialised")
        return result

    payload = {str(k): str(v) for k, v in (data or {}).items() if v is not None}
    dead: list[str] = []

    for start in range(0, len(tokens), _BATCH_SIZE):
        batch = tokens[start:start + _BATCH_SIZE]
        message = _fb_messaging.MulticastMessage(
            tokens=batch,
            notification=_fb_messaging.Notification(
                title=_clip(title, 100),
                body=_clip(body, 240),
            ),
            data=payload,
            android=_fb_messaging.AndroidConfig(
                priority="high",
                notification=_fb_messaging.AndroidNotification(
                    channel_id=PUSH_ANDROID_CHANNEL_ID,
                ),
            ),
            apns=_fb_messaging.APNSConfig(
                payload=_fb_messaging.APNSPayload(
                    aps=_fb_messaging.Aps(sound="default"),
                ),
            ),
        )
        try:
            response = _fb_messaging.send_each_for_multicast(message, app=app)
        except Exception as e:
            result["attempted"] += len(batch)
            result["failed"] += len(batch)
            reason = f"{type(e).__name__}: {e}"[:200]
            errors[reason] = errors.get(reason, 0) + len(batch)
            print(f"   ⚠️   Push: batch send failed — {reason}")
            continue

        result["attempted"] += len(batch)
        for token, item in zip(batch, response.responses):
            if item.success:
                result["sent"] += 1
                continue
            result["failed"] += 1
            reason = f"{type(item.exception).__name__}: {item.exception}"[:200]
            errors[reason] = errors.get(reason, 0) + 1
            # Only prune on an explicit "this token is dead" answer. Other
            # errors (quota, transient outage, a payload problem) say nothing
            # about the token, and deleting on those would wipe live devices.
            if isinstance(
                item.exception,
                (_fb_messaging.UnregisteredError, _fb_messaging.SenderIdMismatchError),
            ):
                dead.append(token)

    if dead:
        result["pruned"] = remove_tokens(dead)

    _record(title, data, result, errors)
    for reason, count in errors.items():
        print(f"   ⚠️   Push: {count} failed — {reason}")
    print(
        f"   🔔  Push: {result['sent']}/{result['attempted']} delivered"
        + (f", {result['pruned']} stale token(s) removed" if result["pruned"] else "")
    )
    return result


# ── Wording ──────────────────────────────────────────────────────────────────

_NEW_PICK = [
    ("New Pick",        "A fresh pick is ready."),
    ("New Signal",      "Check the Signals tab."),
    ("Fresh Setup",     "A new setup is available."),
    ("New Opportunity", "See the Signals tab."),
    ("Buy Setup",       "A new setup is ready."),
    ("Fresh Pick",      "Take a look in Signals."),
    ("Buy Signal",      "Open the Signals tab."),
]

_REVISED = [
    ("Signal Updated", "The signal has been updated."),
    ("Pick Revised",   "The existing pick has changed."),
    ("Updated Signal", "Check the latest details."),
    ("Setup Updated",  "The setup has been updated."),
    ("Pick Updated",   "Open Signals for the change."),
]

_ENTRY_REACHED = [
    ("Level Reached",       "has reached its entry level."),
    ("Entry Level Reached", "touched its entry level."),
    ("Entry Update",        "reached its entry level."),
]

_EXIT_PROFIT = ["Book Profit", "Exit Signal", "Time to Exit", "Sell Signal", "Take Profit"]
_EXIT_LOSS   = ["Exit Signal", "Time to Exit", "Sell Signal"]   # "Book Profit" would be wrong

_last_pick: dict[str, int] = {}
_pick_lock = threading.Lock()


def _pick(key: str, pool: list):
    """Random item from pool, never the same one twice in a row for this key."""
    with _pick_lock:
        index = random.randrange(len(pool))
        if len(pool) > 1 and index == _last_pick.get(key):
            index = (index + 1 + random.randrange(len(pool) - 1)) % len(pool)
        _last_pick[key] = index
        return pool[index]


def format_rupees(value: float) -> str:
    """₹2,850 · ₹120.50 · ₹1,23,456 — Indian grouping, decimals only if needed."""
    paise_total = int(round(abs(float(value)) * 100))
    rupees, paise = divmod(paise_total, 100)
    digits = str(rupees)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        digits = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head) + "," + tail
    return f"{'-' if value < 0 else ''}₹{digits}" + (f".{paise:02d}" if paise else "")


def build_new_signal_text(symbol: str) -> tuple[str, str]:
    heading, line = _pick("new", _NEW_PICK)
    return f"{heading}: {symbol}", line


def build_revised_signal_text(symbol: str) -> tuple[str, str]:
    heading, line = _pick("revised", _REVISED)
    return f"{heading}: {symbol}", line


def build_entry_reached_text(symbol: str) -> tuple[str, str]:
    """Informational wording only ("level reached"), never advice."""
    heading, line = _pick("entry_reached", _ENTRY_REACHED)
    return f"{heading}: {symbol}", f"{symbol} {line} Open Signals for details."


def build_exit_text(stock: str, profit: float, exit_price: float) -> tuple[str, str]:
    loss = profit < 0
    heading = _pick("exit-loss" if loss else "exit", _EXIT_LOSS if loss else _EXIT_PROFIT)
    label = "Loss" if loss else "Profit"
    return (
        f"{heading}: {stock}",
        f"{label} {format_rupees(abs(profit))} | Exit {format_rupees(exit_price)}",
    )

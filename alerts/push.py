"""
alerts/push.py
──────────────
Push notifications to the mobile app through Firebase Cloud Messaging (FCM).

This is the phone-delivery counterpart of alerts/notify.py (terminal / sound /
desktop / email). It is deliberately separate from fire_alert(): fire_alert
reports the *raw scanner's* hits to the operator, whereas users of the app
should only be pushed the picks an admin has curated and published — the
same picks GET /api/signals serves. main.py therefore calls
notify_new_signal() from the admin signal endpoints, not from the scan loop.

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
  signal         notify_new_signal()      a newly published pick → opens Signals
  signal_update  notify_revised_signal()  an already-announced pick changed
                                          → opens Signals
  exit           notify_exit()            exit call (stock, profit, exit price)
                                          → opens the app's Alerts screen

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
    if FIREBASE_SERVICE_ACCOUNT_JSON.strip():
        return "json"
    if FIREBASE_SERVICE_ACCOUNT_BASE64.strip():
        return "base64"
    path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if path and os.path.isfile(path):
        return "file"
    return None


def _build_credential():
    source = _credential_source()
    if source == "json":
        return _fb_credentials.Certificate(json.loads(FIREBASE_SERVICE_ACCOUNT_JSON))
    if source == "base64":
        decoded = base64.b64decode(FIREBASE_SERVICE_ACCOUNT_BASE64).decode("utf-8")
        return _fb_credentials.Certificate(json.loads(decoded))
    if source == "file":
        return _fb_credentials.Certificate(os.environ["GOOGLE_APPLICATION_CREDENTIALS"].strip())
    return None


def _ensure_app():
    """Initialise the Firebase app once. Returns it, or None if push is unavailable."""
    global _app, _init_failed
    if _app is not None:
        return _app
    if _init_failed or not _SDK_AVAILABLE:
        return None
    with _init_lock:
        if _app is not None:
            return _app
        try:
            cred = _build_credential()
            if cred is None:
                _init_failed = True
                return None
            _app = firebase_admin.initialize_app(cred, name=_APP_NAME)
            print("   🔔  Push: Firebase initialised")
        except Exception as e:
            _init_failed = True
            print(f"   ⚠️   Push: Firebase init failed — {e}")
            return None
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
    Send one notification to the given devices. Blocking — callers that sit on
    a request thread should use the notify_*/broadcast helpers below, which
    hand this off to a background thread.

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


def _in_background(fn, *args, **kwargs) -> None:
    def _run():
        try:
            fn(*args, **kwargs)
        except Exception as e:      # never let a push problem surface anywhere
            print(f"   ⚠️   Push: unexpected error — {e}")
    threading.Thread(target=_run, daemon=True).start()


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


def build_exit_text(stock: str, profit: float, exit_price: float) -> tuple[str, str]:
    loss = profit < 0
    heading = _pick("exit-loss" if loss else "exit", _EXIT_LOSS if loss else _EXIT_PROFIT)
    label = "Loss" if loss else "Profit"
    return (
        f"{heading}: {stock}",
        f"{label} {format_rupees(abs(profit))} | Exit {format_rupees(exit_price)}",
    )


# ── Public entry points ──────────────────────────────────────────────────────

def notify_new_signal(signal: dict) -> None:
    """
    Push a newly published admin signal to every device that opted in to
    signal alerts. Fire-and-forget: returns immediately.
    """
    if not is_configured():
        return

    symbol = str(signal.get("symbol") or "").strip().upper()
    if not symbol:
        return
    title, body = build_new_signal_text(symbol)

    _in_background(
        lambda: send_to_devices(
            list_devices(topic="signals"),
            title=title,
            body=body,
            data={
                "type"     : "signal",
                "symbol"   : symbol,
                "signal_id": signal.get("id"),
            },
        )
    )


def notify_revised_signal(signal: dict) -> None:
    """
    Push a separate "this pick changed" notification for a signal that was
    already announced. Same audience as notify_new_signal. Fire-and-forget.
    """
    if not is_configured():
        return

    symbol = str(signal.get("symbol") or "").strip().upper()
    if not symbol:
        return
    title, body = build_revised_signal_text(symbol)

    _in_background(
        lambda: send_to_devices(
            list_devices(topic="signals"),
            title=title,
            body=body,
            data={
                "type"     : "signal_update",
                "symbol"   : symbol,
                "signal_id": signal.get("id"),
            },
        )
    )


def notify_exit(stock_name: str, profit: float, exit_price: float) -> None:
    """
    Push an exit call to every registered device. Fire-and-forget.

    Only three values are needed: the stock name, the profit (negative for a
    loss) and the exit price. The app shows it in its Alerts section and
    tapping the notification opens that screen.
    """
    if not is_configured():
        return

    stock = " ".join(str(stock_name or "").split()).upper()
    if not stock:
        return
    try:
        profit = float(profit)
        exit_price = float(exit_price)
    except (TypeError, ValueError):
        return
    title, body = build_exit_text(stock, profit, exit_price)

    _in_background(
        lambda: send_to_devices(
            list_devices(),
            title=title,
            body=body,
            data={
                "type"      : "exit",
                "symbol"    : stock,
                "profit"    : profit,
                "exit_price": exit_price,
            },
        )
    )


def broadcast(title: str, body: str, data: dict | None = None) -> None:
    """Push a custom message to every registered device. Fire-and-forget."""
    if not is_configured():
        return
    payload = {"type": "general", **(data or {})}
    _in_background(send_to_devices, list_devices(), title, body, payload)
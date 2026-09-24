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

Credentials: see FIREBASE_SERVICE_ACCOUNT_* in config/settings.py.
"""

import os
import json
import base64
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
    tokens = [d["token"] for d in devices if d.get("token")]
    if not tokens:
        return result

    app = _ensure_app()
    if app is None:
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
            print(f"   ⚠️   Push: batch send failed — {e}")
            continue

        result["attempted"] += len(batch)
        for token, item in zip(batch, response.responses):
            if item.success:
                result["sent"] += 1
                continue
            result["failed"] += 1
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
    rationale = str(signal.get("rationale") or "").strip()

    def _send():
        send_to_devices(
            list_devices(topic="signals"),
            title=f"New scanner pick: {symbol}",
            body=rationale or "A new pick is on the Signals tab.",
            data={
                "type"     : "signal",
                "symbol"   : symbol,
                "signal_id": signal.get("id"),
            },
        )

    _in_background(_send)


def broadcast(title: str, body: str, data: dict | None = None) -> None:
    """Push a custom message to every registered device. Fire-and-forget."""
    if not is_configured():
        return
    payload = {"type": "general", **(data or {})}
    _in_background(send_to_devices, list_devices(), title, body, payload)

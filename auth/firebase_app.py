"""
auth/firebase_app.py
────────────────────
Single, shared Firebase Admin app for the whole backend.

Used by:
  • alerts/push.py        — FCM push sending
  • auth/app_auth.py      — verifying mobile-app users' Firebase ID tokens

Credentials come from config.settings (FIREBASE_SERVICE_ACCOUNT_JSON /
FIREBASE_SERVICE_ACCOUNT_BASE64) or GOOGLE_APPLICATION_CREDENTIALS.
Never raises: get_app() returns None when Firebase is unavailable.
"""

import os
import json
import base64
import threading

from config.settings import (
    FIREBASE_SERVICE_ACCOUNT_JSON,
    FIREBASE_SERVICE_ACCOUNT_BASE64,
)

try:
    import firebase_admin
    from firebase_admin import credentials as _fb_credentials
    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False

# Kept identical to the name push.py always used, so push behaviour is unchanged.
APP_NAME = "ayre-push"

_lock = threading.Lock()
_app = None
_init_failed = False


def credential_source() -> str | None:
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
    source = credential_source()
    if source == "json":
        return _fb_credentials.Certificate(json.loads(FIREBASE_SERVICE_ACCOUNT_JSON))
    if source == "base64":
        decoded = base64.b64decode(FIREBASE_SERVICE_ACCOUNT_BASE64).decode("utf-8")
        return _fb_credentials.Certificate(json.loads(decoded))
    if source == "file":
        return _fb_credentials.Certificate(os.environ["GOOGLE_APPLICATION_CREDENTIALS"].strip())
    return None


def get_app():
    """Initialise the Firebase app once. Returns it, or None if unavailable."""
    global _app, _init_failed
    if _app is not None:
        return _app
    if _init_failed or not SDK_AVAILABLE:
        return None
    with _lock:
        if _app is not None:
            return _app
        try:
            cred = _build_credential()
            if cred is None:
                _init_failed = True
                return None
            _app = firebase_admin.initialize_app(cred, name=APP_NAME)
            print("   🔔  Firebase Admin initialised")
        except Exception as e:
            _init_failed = True
            print(f"   ⚠️   Firebase Admin init failed — {e}")
            return None
    return _app


def is_configured() -> bool:
    """True when Firebase can actually be attempted (SDK + credentials present)."""
    return SDK_AVAILABLE and credential_source() is not None and not _init_failed

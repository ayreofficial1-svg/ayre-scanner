"""
data/app_devices.py
────────────────────
Registry of mobile-app installs that can receive push notifications.

The Flutter app registers its Firebase Cloud Messaging (FCM) token here via
POST /api/devices/register and removes it via POST /api/devices/unregister.
alerts/push.py reads it to decide who to send to.

Entry schema (JSON object keyed by token)
─────────────────────────────────────────
  {
    "<fcm token>": {
      "token"        : "<fcm token>",
      "platform"     : "android" | "ios" | "unknown",
      "signals"      : true,             # wants "new signal" pushes
      "app_version"  : "2.0.0" | null,
      "registered_at": "<ISO timestamp>",
      "last_seen_at" : "<ISO timestamp>"
    }
  }

A token is the identity: re-registering the same token updates its
preferences in place rather than adding a duplicate. Tokens FCM reports as
dead are pruned by alerts/push.py through remove_tokens().

Storage is a single JSON file like the other app_*.json stores. A lock guards
the read-modify-write cycle because registrations arrive on Flask request
threads while pushes prune from a background thread.
"""

import os
import json
import threading
import datetime

from config.settings import APP_DEVICES_FILE, PUSH_MAX_DEVICES

_lock = threading.Lock()

_PLATFORMS = {"android", "ios"}


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _valid_token(token: str) -> bool:
    # FCM tokens are ~150-200 chars of URL-safe characters plus ':' — accept a
    # generous range but never whitespace or absurd lengths.
    return (
        isinstance(token, str)
        and 20 <= len(token) <= 4096
        and not any(ch.isspace() for ch in token)
    )


def _load() -> dict[str, dict]:
    if not os.path.exists(APP_DEVICES_FILE):
        return {}
    try:
        with open(APP_DEVICES_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(devices: dict[str, dict]) -> None:
    tmp = f"{APP_DEVICES_FILE}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(devices, f, indent=2)
    os.replace(tmp, APP_DEVICES_FILE)   # atomic: a crash never leaves half a file


def register_device(
    token: str,
    platform: str = "unknown",
    signals: bool = True,
    app_version: str | None = None,
) -> dict | None:
    """
    Create or update a device. Returns the stored entry, or None when the
    token is malformed or the registry is full.
    """
    token = (token or "").strip()
    if not _valid_token(token):
        return None

    platform = (platform or "").strip().lower()
    if platform not in _PLATFORMS:
        platform = "unknown"

    with _lock:
        devices = _load()
        existing = devices.get(token)
        if existing is None and len(devices) >= PUSH_MAX_DEVICES:
            return None
        now = _now_iso()
        entry = {
            "token"        : token,
            "platform"     : platform,
            "signals"      : bool(signals),
            "app_version"  : (str(app_version).strip()[:32] or None) if app_version else None,
            "registered_at": (existing or {}).get("registered_at", now),
            "last_seen_at" : now,
        }
        devices[token] = entry
        _save(devices)
        return entry


def unregister_device(token: str) -> bool:
    """Remove a device. Returns True if it was registered."""
    token = (token or "").strip()
    with _lock:
        devices = _load()
        if token not in devices:
            return False
        del devices[token]
        _save(devices)
        return True


def remove_tokens(tokens: list[str]) -> int:
    """Drop several tokens at once (dead tokens reported by FCM). Returns count removed."""
    if not tokens:
        return 0
    with _lock:
        devices = _load()
        removed = 0
        for token in tokens:
            if devices.pop(token, None) is not None:
                removed += 1
        if removed:
            _save(devices)
        return removed


def list_devices(topic: str | None = None) -> list[dict]:
    """
    All registered devices. Pass topic="signals" to keep only those that have
    opted in to new-signal pushes; any other/None returns every device.
    """
    with _lock:
        devices = list(_load().values())
    if topic == "signals":
        devices = [d for d in devices if d.get("signals", True)]
    return devices


def device_count() -> int:
    with _lock:
        return len(_load())

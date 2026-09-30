"""
utils/scan_control.py
─────────────────────
Small shared helpers for safely stopping a scan and for detecting a dead
Fyers session.  No network calls, no Fyers usage.
"""

from __future__ import annotations

import threading
import time


class ScanCancelled(Exception):
    """Raised inside a scan when the user pressed Stop."""


class FyersAuthError(Exception):
    """Fyers is rejecting our token (every request would fail)."""


def check_cancel(event: threading.Event | None) -> None:
    if event is not None and event.is_set():
        raise ScanCancelled()


def sleep_cancellable(seconds: float, event: threading.Event | None) -> None:
    """Sleep in short slices so Stop takes effect within ~0.25 s."""
    end = time.monotonic() + max(0.0, seconds)
    while True:
        check_cancel(event)
        left = end - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(0.25, left))

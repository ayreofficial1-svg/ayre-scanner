"""
alerts/manual_push.py
─────────────────────
The ONE gate every manual notification goes through (Phase 4).

Nothing in the backend sends a push except an admin endpoint calling
send_manual(). Detection, scans, sweeps, timers and startup code never import
this module. A push cannot be recalled, so the guard enforces — on the server,
not only in the browser:

  • an explicit confirmation field ("confirm": true) in the request
  • a duplicate window (same type + stock within PUSH_DUPLICATE_WINDOW_SECONDS)
  • a "send again" flag to repeat something already sent
  • a daily cap on manual sends (PUSH_DAILY_MAX_MANUAL)
  • an audit-log entry for every send and every refusal

The audit log is the source of truth for the window and the cap, and the
check + log entry happen under one lock, so two simultaneous clicks cannot
both get through.
"""

import threading

from config.settings import PUSH_DAILY_MAX_MANUAL, PUSH_DUPLICATE_WINDOW_SECONDS
from data.app_devices import list_devices
from data import push_audit
from alerts import push

_guard_lock = threading.Lock()


class SendRefused(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _refuse(kind: str, key: str, admin: str, status: int, code: str, message: str):
    push_audit.append({
        "type": kind, "key": key, "admin": admin or "unknown",
        "status": "refused", "reason": message,
    })
    print(f"   🚫  Push refused ({kind} {key}) by {admin}: {message}")
    raise SendRefused(status, code, message)


def send_manual(
    *,
    kind: str,
    key: str,
    title: str,
    body: str,
    data: dict,
    topic: str | None,
    admin: str | None,
    confirm: bool,
    send_again: bool = False,
) -> dict:
    """
    Guard, log and dispatch one notification. Returns
    {"queued": True, "audience": N, "audit_id": "..."} or raises SendRefused.
    The actual FCM call runs on a background thread; its result is written
    back to the audit entry.
    """
    admin = admin or "unknown"
    key = str(key or "").strip().upper()[:80]

    if confirm is not True:
        _refuse(kind, key, admin, 400, "confirm_required", "Confirmation required")
    if not push.is_configured():
        _refuse(kind, key, admin, 503, "not_configured",
                "Push is not configured. Set FIREBASE_SERVICE_ACCOUNT_JSON "
                "(and install firebase-admin) on the server.")

    with _guard_lock:
        if push_audit.sends_today() >= PUSH_DAILY_MAX_MANUAL:
            _refuse(kind, key, admin, 429, "daily_cap",
                    f"Daily limit of {PUSH_DAILY_MAX_MANUAL} manual notifications reached. "
                    "Try again tomorrow.")

        window = int(PUSH_DUPLICATE_WINDOW_SECONDS)
        if window > 0 and not send_again:
            if push_audit.last_send(kind, key, within_seconds=window):
                _refuse(kind, key, admin, 409, "duplicate",
                        f"The same notification was sent less than {window} seconds ago. "
                        "Wait, or confirm \"send again\".")

        devices = list_devices(topic=topic) if topic else list_devices()
        audience = len(devices)
        if audience == 0:
            _refuse(kind, key, admin, 409, "no_audience",
                    "No registered phone would receive this "
                    "(none registered, or none have this alert switched on).")

        entry = push_audit.append({
            "type": kind, "key": key, "admin": admin,
            "status": "sending", "audience": audience,
            "attempted": 0, "sent": 0, "failed": 0,
        })

    def _run():
        try:
            result = push.send_to_devices(devices, title, body, data)
            push_audit.update(
                entry["id"], status="done",
                attempted=result.get("attempted", 0),
                sent=result.get("sent", 0),
                failed=result.get("failed", 0),
            )
        except Exception as e:
            push_audit.update(entry["id"], status="done", reason=f"{type(e).__name__}: {e}"[:200])
            print(f"   ⚠️   Push: unexpected error — {e}")

    threading.Thread(target=_run, daemon=True, name="manual-push").start()
    print(f"   🔔  Manual push queued: {kind} {key} → {audience} phone(s), by {admin}")
    return {"queued": True, "audience": audience, "audit_id": entry["id"]}


# ── Typed helpers (payloads identical to the existing notify_* functions) ────

def send_new_signal(signal: dict, admin, confirm, send_again=False) -> dict:
    symbol = str(signal.get("symbol") or "").strip().upper()
    title, body = push.build_new_signal_text(symbol)
    return send_manual(
        kind="signal", key=symbol, title=title, body=body,
        data={"type": "signal", "symbol": symbol, "signal_id": signal.get("id")},
        topic="signals", admin=admin, confirm=confirm, send_again=send_again,
    )


def send_revised_signal(signal: dict, admin, confirm, send_again=False) -> dict:
    symbol = str(signal.get("symbol") or "").strip().upper()
    title, body = push.build_revised_signal_text(symbol)
    return send_manual(
        kind="signal_update", key=symbol, title=title, body=body,
        data={"type": "signal_update", "symbol": symbol, "signal_id": signal.get("id")},
        topic="signals", admin=admin, confirm=confirm, send_again=send_again,
    )


def send_entry_reached(signal: dict, admin, confirm, send_again=False) -> dict:
    symbol = str(signal.get("symbol") or "").strip().upper()
    title, body = push.build_entry_reached_text(symbol)
    return send_manual(
        kind="entry_reached", key=symbol, title=title, body=body,
        data={"type": "entry_reached", "symbol": symbol, "signal_id": signal.get("id")},
        topic="signals", admin=admin, confirm=confirm, send_again=send_again,
    )


def send_exit(stock: str, profit: float, exit_price: float, admin, confirm, send_again=False) -> dict:
    title, body = push.build_exit_text(stock, profit, exit_price)
    return send_manual(
        kind="exit", key=stock, title=title, body=body,
        data={"type": "exit", "symbol": stock, "profit": profit, "exit_price": exit_price},
        topic=None, admin=admin, confirm=confirm, send_again=send_again,
    )


def send_custom(title: str, body: str, admin, confirm, send_again=False) -> dict:
    key = " ".join(title.split())[:60]
    return send_manual(
        kind="general", key=key, title=title, body=body,
        data={"type": "general"},
        topic=None, admin=admin, confirm=confirm, send_again=send_again,
    )

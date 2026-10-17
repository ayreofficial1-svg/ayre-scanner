"""
data/app_signal_payload.py
───────────────────────────
Builds the consumer-app shape of one signal for GET /api/signals.

This is an ALLOW-LIST: only the keys named below can reach the app. Any field
added to a stored signal later (tick-derived or admin bookkeeping) is dropped
by default, so it cannot leak by accident.

No live price, % change or exchange-derived value is ever included. The
entry_reached_* facts are included only while the compliance hold is lifted
(APP_ENTRY_REACHED_ENABLED).
"""

# Analyst-typed content only.
_APP_SIGNAL_KEYS = (
    "id",
    "symbol",
    "name",
    "rationale",
    "date_added",
    "entry_price",
    "entry_low",
    "entry_high",
    "exit_price",
    "exit_low",
    "exit_high",
    "stop_loss",
)

# Public entry-reached facts (tick-derived). Held by default.
_ENTRY_REACHED_KEYS = (
    "entry_reached_at",
    "entry_reached_price",
    "entry_reached_extended",
)


def to_app_signal(signal: dict, entry_reached_enabled: bool = False) -> dict:
    """Return the app-visible copy of ``signal`` (never mutates the input)."""
    out = {key: signal[key] for key in _APP_SIGNAL_KEYS if key in signal}
    if entry_reached_enabled:
        for key in _ENTRY_REACHED_KEYS:
            if key in signal:
                out[key] = signal[key]
    # No stock is featured or pinned in the app; every signal has equal weight.
    out["featured"] = False
    out["pinned"] = False
    out["display_order"] = 0
    return out

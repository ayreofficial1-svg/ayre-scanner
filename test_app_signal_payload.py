# test_app_signal_payload.py — run from project root: python test_app_signal_payload.py
# Offline, no network, no server needed.
from data.app_signal_payload import to_app_signal

FORBIDDEN_ALWAYS = [
    "last_price", "change_pct",
    "published", "published_at", "published_by", "unpublished_at",
    "notified_at", "notified_levels", "update_notified_at", "push_sent_at",
    "push_state", "added_by", "created_at", "updated_at", "active", "enabled",
    "entry_reached_level", "entry_reached_hit_id", "entry_reached_by",
    "entry_reached_published_at", "price_at_detection", "ltp", "volume",
    "some_future_tick_field",
]
ENTRY_REACHED_PUBLIC = ["entry_reached_at", "entry_reached_price", "entry_reached_extended"]

SIGNAL = {
    "id": "abc123", "symbol": "RELIANCE", "name": "Reliance Industries",
    "rationale": "Breakout.", "date_added": "2026-07-03",
    "entry_price": 2850.0, "entry_low": 2840.0, "entry_high": 2860.0,
    "exit_price": 3050.0, "exit_low": 3040.0, "exit_high": 3060.0,
    "stop_loss": 2760.0,
    "featured": True, "pinned": True, "display_order": 7,
    "last_price": 2851.5, "change_pct": 0.4,
    "published": True, "published_at": "x", "published_by": "a", "unpublished_at": None,
    "notified_at": "x", "notified_levels": {}, "update_notified_at": "x", "push_sent_at": "x",
    "added_by": "raghav", "created_at": "x", "updated_at": "x", "active": True, "enabled": True,
    "entry_reached_at": "2026-07-04T10:00:00+05:30", "entry_reached_price": 2849.9,
    "entry_reached_extended": False,
    "entry_reached_level": 2850.0, "entry_reached_hit_id": "h1",
    "entry_reached_by": "a", "entry_reached_published_at": "x",
    "price_at_detection": 2849.9, "ltp": 1.0, "volume": 5,
    "some_future_tick_field": 1,
}


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def main():
    before = dict(SIGNAL)

    off = to_app_signal(SIGNAL, False)
    for k in FORBIDDEN_ALWAYS + ENTRY_REACHED_PUBLIC:
        check(k not in off, f"hold ON: '{k}' leaked into app payload")
    for k in ("id", "symbol", "name", "rationale", "date_added", "entry_price", "entry_low",
              "entry_high", "exit_price", "exit_low", "exit_high", "stop_loss"):
        check(off.get(k) == SIGNAL[k], f"typed field '{k}' missing or changed")
    check(off["featured"] is False and off["pinned"] is False and off["display_order"] == 0,
          "neutral featured/pinned/display_order not applied")

    on = to_app_signal(SIGNAL, True)
    for k in ENTRY_REACHED_PUBLIC:
        check(k in on, f"hold lifted: '{k}' should be present")
    for k in FORBIDDEN_ALWAYS:
        check(k not in on, f"hold lifted: '{k}' leaked into app payload")

    check(SIGNAL == before, "input signal was mutated")

    minimal = to_app_signal({"symbol": "TCS", "rationale": "r"}, False)
    check(set(minimal) == {"symbol", "rationale", "featured", "pinned", "display_order"},
          f"unexpected keys on minimal signal: {sorted(minimal)}")

    print("OK: app signal payload allow-list holds")


if __name__ == "__main__":
    main()

"""
utils/logger.py

Signal logging for the simplified scanner.

Each signal is written to:
  - logs/signals_YYYYMMDD.json
  - logs/signal_master.csv

The master CSV is schema-checked on each write. If an older pre-migration
header is detected, the file is archived automatically and a new CSV is
started with the current simplified schema.
"""

from __future__ import annotations

import csv
import datetime
import json
import os

from config.settings import DISCLAIMER, RA_REGISTRATION_NUMBER, SIGNAL_LOG_DIR


def _ensure_log_dir() -> None:
    os.makedirs(SIGNAL_LOG_DIR, exist_ok=True)


def _daily_log_path(date: datetime.date | None = None) -> str:
    current = date or datetime.date.today()
    return os.path.join(SIGNAL_LOG_DIR, f"signals_{current.strftime('%Y%m%d')}.json")


def _master_csv_path() -> str:
    return os.path.join(SIGNAL_LOG_DIR, "signal_master.csv")


_CSV_FIELDS = [
    # ── Identity & timing ───────────────────────────────────────────────────
    "logged_at",
    "log_date",
    "time",
    "ra_registration",
    # ── Symbol ──────────────────────────────────────────────────────────────
    "symbol",
    "fyers_symbol",
    "date",
    # ── Price snapshot ──────────────────────────────────────────────────────
    "close",
    "sma44",
    "sma_dist_pct",
    # ── MACD ────────────────────────────────────────────────────────────────
    "macd",
    "macd_signal",
    "macd_histogram",
    "cross_type",
    "crossover_bars_ago",
    "ma_type",
    "price_interaction_type",
    "is_double_bottom",
    "weekly_rising",
    # ── Day change ──────────────────────────────────────────────────────────
    "change_pct",
    # ── Watchlist provenance ─────────────────────────────────────────────────
    "promoted_from_watchlist",
    "watchlist_since",

    # ── Compliance ──────────────────────────────────────────────────────────
    "disclaimer",
]


def _rotate_legacy_master_csv(path: str) -> None:
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    archived = os.path.join(SIGNAL_LOG_DIR, f"signal_master_legacy_{timestamp}.csv")
    counter = 1

    while os.path.exists(archived):
        archived = os.path.join(
            SIGNAL_LOG_DIR,
            f"signal_master_legacy_{timestamp}_{counter}.csv",
        )
        counter += 1

    os.replace(path, archived)
    print(f"   Archived legacy master CSV -> {archived}")


def _ensure_master_csv_schema(path: str) -> bool:
    if not os.path.exists(path):
        return True

    try:
        with open(path, newline="", encoding="utf-8") as handle:
            header = next(csv.reader(handle), [])
    except Exception:
        header = []

    if header == _CSV_FIELDS:
        return False

    _rotate_legacy_master_csv(path)
    return True


def log_signal(signal: dict) -> None:
    """
    Append a signal to the daily JSON log and the master CSV.

    Parameters
    ----------
    signal : dict
        Payload returned by scanner.conditions.evaluate(), optionally extended
        with engine-level fields such as promoted/watchlist_since.
        Trade level fields (entry, sl, t1, t2, t3, rr_t1, …) are included
        automatically when the signal status is "confirmed".
    """
    _ensure_log_dir()
    now = datetime.datetime.now()

    record = {
        # Identity & timing
        "logged_at"              : now.isoformat(),
        "log_date"               : str(datetime.date.today()),
        "time"                   : now.strftime("%H:%M:%S"),
        "ra_registration"        : RA_REGISTRATION_NUMBER,
        # Symbol
        "symbol"                 : signal.get("symbol", ""),
        "fyers_symbol"           : signal.get("fyers_symbol", ""),
        "date"                   : signal.get("date", ""),
        # Price snapshot
        "close"                  : signal.get("close", 0),
        "sma44"                  : signal.get("sma44", 0),
        "sma_dist_pct"           : signal.get("sma_dist_pct", 0),
        # MACD
        "macd"                   : signal.get("macd", 0),
        "macd_signal"            : signal.get("macd_signal", 0),
        "macd_histogram"         : signal.get("macd_histogram", 0),
        "cross_type"             : signal.get("cross_type", ""),
        "crossover_bars_ago"     : signal.get("crossover_bars_ago"),
        "ma_type"                : signal.get("ma_type", ""),
        "price_interaction_type" : signal.get("price_interaction_type", ""),
        "is_double_bottom"       : signal.get("is_double_bottom", False),
        "weekly_rising"          : signal.get("weekly_rising"),
        # Day change
        "change_pct"             : signal.get("change_pct", 0),
        # Watchlist provenance
        "promoted_from_watchlist": signal.get("promoted", False),
        "watchlist_since"        : signal.get("watchlist_since", ""),

        # Compliance
        "disclaimer"             : DISCLAIMER,
    }

    log_path = _daily_log_path()
    daily_log: list[dict] = []
    if os.path.exists(log_path):
        try:
            with open(log_path, encoding="utf-8") as handle:
                daily_log = json.load(handle)
        except Exception:
            daily_log = []
    daily_log.append(record)
    with open(log_path, "w", encoding="utf-8") as handle:
        json.dump(daily_log, handle, indent=2)

    csv_path = _master_csv_path()
    write_header = _ensure_master_csv_schema(csv_path)
    with open(csv_path, "a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_CSV_FIELDS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerow(record)


def get_log_summary() -> dict:
    """Return a count of logged days and total signals."""
    _ensure_log_dir()
    files = [
        name
        for name in os.listdir(SIGNAL_LOG_DIR)
        if name.startswith("signals_") and name.endswith(".json")
    ]

    total = 0
    for name in files:
        try:
            with open(os.path.join(SIGNAL_LOG_DIR, name), encoding="utf-8") as handle:
                total += len(json.load(handle))
        except Exception:
            pass

    return {"days_logged": len(files), "total_signals": total}

from __future__ import annotations

import datetime
import time

from config.settings import QUALITY_STOCK_WHITELIST, WEEKLY_RISING_FILTER
from data.candles import (
    _MIN_BARS,
    _NUM_WINDOWS,
    _WINDOW_DAYS,
    fetch_candles_bulk_at_date,
    weekly_candles_from_daily,
)
from scanner.debug_evaluate import evaluate_debug
from scanner.engine import _check_weekly_sma_rising

_LOOKBACK_DAYS = _NUM_WINDOWS * _WINDOW_DAYS


def _prepare_df(df):
    if df is None or df.empty:
        return None
    dates = df.index.normalize()
    df = df[~dates.duplicated(keep="last")]
    return df if len(df) >= _MIN_BARS else None


def run_historical_scan(fyers, symbols: list[str], target_date: datetime.date) -> dict:
    """
    Run the scanner against candles ending on target_date without mutating the
    live watchlist, alert log, or notification state.
    """
    started = time.time()
    print(f"\n🧪  Backtest requested for {target_date.isoformat()} ({len(symbols)} symbols)")
    candle_data, fetch_report = fetch_candles_bulk_at_date(
        fyers=fyers,
        symbols=symbols,
        range_to=target_date,
        verbose=False,
    )
    print(
        "🧪  Backtest fetch complete: "
        f"{fetch_report.get('valid', 0)} valid | "
        f"{fetch_report.get('no_data', 0)} no-data | "
        f"{fetch_report.get('failed', 0)} failed"
    )

    prepared = {}
    dropped_short = 0
    for symbol, raw_df in candle_data.items():
        ready = _prepare_df(raw_df)
        if ready is not None:
            prepared[symbol] = ready
        else:
            dropped_short += 1
    if dropped_short:
        print(f"🧪  Backtest preparation dropped {dropped_short} symbol(s) with insufficient bars")

    quality_filtered = 0
    if QUALITY_STOCK_WHITELIST:
        before_quality = len(prepared)
        prepared = {k: v for k, v in prepared.items() if k in QUALITY_STOCK_WHITELIST}
        quality_filtered = before_quality - len(prepared)
        if quality_filtered:
            print(f"🧪  Backtest quality filter removed {quality_filtered} symbol(s)")

    weekly_status: dict[str, bool | None] = {}
    weekly_filtered = 0
    if WEEKLY_RISING_FILTER and prepared:
        weekly_data, weekly_report = weekly_candles_from_daily(prepared)
        before_weekly = len(prepared)
        filtered = {}
        for symbol, df in prepared.items():
            weekly_rising = _check_weekly_sma_rising(weekly_data.get(symbol))
            weekly_status[symbol] = weekly_rising
            if weekly_rising is not False:
                filtered[symbol] = df
        prepared = filtered
        weekly_filtered = before_weekly - len(prepared)
        fetch_report["weekly_valid"] = weekly_report.get("valid", 0)
        fetch_report["weekly_filtered"] = weekly_filtered
        if weekly_filtered:
            print(f"🧪  Backtest weekly filter removed {weekly_filtered} symbol(s)")

    signals: list[dict] = []
    watchlist_items: list[dict] = []
    status_counts = {"signal": 0, "watchlist": 0, "none": 0, "error": 0}
    stage_counts: dict[str, int] = {}
    for symbol, raw in prepared.items():
        try:
            result = evaluate_debug(symbol, raw, weekly_rising=weekly_status.get(symbol))
            status = result.get("status", "none")
            stage = result.get("stage", "unknown")
            values = result.get("values", {})
            status_counts[status] = status_counts.get(status, 0) + 1
            stage_counts[stage] = stage_counts.get(stage, 0) + 1
            if status == "signal":
                signals.append(values)
            elif status == "watchlist":
                watchlist_items.append(values)
        except Exception:
            status_counts["error"] += 1
            continue

    resolved_date = None
    if prepared:
        sample = next(iter(prepared.values()))
        resolved_date = sample.index[-1].date()

    fetch_report["evaluated"] = len(prepared)
    fetch_report["dropped_short"] = dropped_short
    fetch_report["quality_filtered"] = quality_filtered
    fetch_report["weekly_filtered"] = weekly_filtered
    fetch_report["runtime_seconds"] = round(time.time() - started, 1)
    fetch_report["status_counts"] = status_counts
    fetch_report["stage_counts"] = stage_counts
    print(
        "🧪  Backtest evaluated: "
        f"{len(prepared)} evaluated | "
        f"{len(signals)} trade ready | "
        f"{len(watchlist_items)} watchlist | "
        f"{status_counts.get('none', 0)} rejected | "
        f"{status_counts.get('error', 0)} errors"
    )

    return {
        "signals": sorted(signals, key=lambda x: x.get("change_pct", 0), reverse=True),
        "watchlist_items": sorted(watchlist_items, key=lambda x: x.get("change_pct", 0), reverse=True),
        "report": fetch_report,
        "requested_date": target_date.isoformat(),
        "resolved_date": resolved_date.isoformat() if resolved_date else None,
        "window_start": (
            (resolved_date - datetime.timedelta(days=_LOOKBACK_DAYS - 1)).isoformat()
            if resolved_date else None
        ),
    }

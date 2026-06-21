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
from scanner.conditions import evaluate
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
    candle_data, fetch_report = fetch_candles_bulk_at_date(
        fyers=fyers,
        symbols=symbols,
        range_to=target_date,
        verbose=False,
    )

    prepared = {}
    for symbol, raw_df in candle_data.items():
        ready = _prepare_df(raw_df)
        if ready is not None:
            prepared[symbol] = ready

    if QUALITY_STOCK_WHITELIST:
        prepared = {k: v for k, v in prepared.items() if k in QUALITY_STOCK_WHITELIST}

    weekly_status: dict[str, bool | None] = {}
    if WEEKLY_RISING_FILTER and prepared:
        weekly_data, weekly_report = weekly_candles_from_daily(prepared)
        filtered = {}
        for symbol, df in prepared.items():
            weekly_rising = _check_weekly_sma_rising(weekly_data.get(symbol))
            weekly_status[symbol] = weekly_rising
            if weekly_rising is not False:
                filtered[symbol] = df
        prepared = filtered
        fetch_report["weekly_valid"] = weekly_report.get("valid", 0)
        fetch_report["weekly_filtered"] = fetch_report.get("valid", 0) - len(prepared)

    signals: list[dict] = []
    watchlist_items: list[dict] = []
    for symbol, raw in prepared.items():
        try:
            result = evaluate(symbol, raw, weekly_rising=weekly_status.get(symbol))
            if result["status"] == "signal":
                signals.append(result["data"])
            elif result["status"] == "watchlist":
                watchlist_items.append(result["data"])
        except Exception:
            continue

    resolved_date = None
    if prepared:
        sample = next(iter(prepared.values()))
        resolved_date = sample.index[-1].date()

    fetch_report["evaluated"] = len(prepared)
    fetch_report["runtime_seconds"] = round(time.time() - started, 1)

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

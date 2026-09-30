from __future__ import annotations

import datetime
import time
from collections import Counter
from typing import Any

import pandas as pd

from config.settings import (
    QUALITY_STOCK_WHITELIST,
    WEEKLY_FILTER_EXCLUDES,
    WEEKLY_RISING_FILTER,
)
from data.candles import (
    _MIN_BARS,
    _NUM_WINDOWS,
    _WINDOW_DAYS,
    fetch_candles_bulk_at_date,
    weekly_candles_from_daily,
)
from scanner.debug_evaluate import (
    evaluate_debug,
    no_data_result,
    save_debug_csv,
    save_debug_json,
    summary_table_detailed,
)
from scanner.engine import _check_weekly_sma_rising

_LOOKBACK_DAYS = _NUM_WINDOWS * _WINDOW_DAYS


def _format_date_ordinal(d: datetime.date) -> str:
    """Format date as ordinal, matching debug_run.py."""
    day = d.day
    if 10 <= day <= 20:
        suffix = "th"
    elif day % 10 == 1:
        suffix = "st"
    elif day % 10 == 2:
        suffix = "nd"
    elif day % 10 == 3:
        suffix = "rd"
    else:
        suffix = "th"
    return f"{day}{suffix} {d.strftime('%B')}, {d.strftime('%Y')}"


def _prepare_df(df: pd.DataFrame | None) -> pd.DataFrame | None:
    """
    Safety-net deduplication from debug_run.py: keep only the last record per
    calendar date.

    Short-history stocks (fewer than the shared candle minimum) are NOT dropped
    any more — they are still evaluated so they appear in the results with a
    "not enough history" explanation. Only a completely empty frame is unusable.
    """
    if df is None or df.empty:
        return None
    dates = df.index.normalize()
    return df[~dates.duplicated(keep="last")]


def _empty_result(
    target_date: datetime.date,
    fetch_report: dict[str, Any],
    started: float,
    message: str,
) -> dict[str, Any]:
    report = dict(fetch_report)
    report.update(
        {
            "evaluated": 0,
            "prepared": 0,
            "dropped_short": report.get("dropped_short", 0),
            "quality_filtered": report.get("quality_filtered", 0),
            "weekly_filtered": report.get("weekly_filtered", 0),
            "runtime_seconds": round(time.time() - started, 1),
            "status_counts": {"signal": 0, "watchlist": 0, "none": 0, "error": 0},
            "stage_counts": {},
            "evaluation_errors": [],
        }
    )
    return {
        "signals": [],
        "watchlist_items": [],
        "results": {},
        "report": report,
        "requested_date": target_date.isoformat(),
        "resolved_date": None,
        "window_start": None,
        "error": message,
    }


def _prepare_candle_data(
    candle_data: dict[str, pd.DataFrame],
) -> tuple[dict[str, pd.DataFrame], int, datetime.date | None]:
    """
    Returns (prepared, short_history_count, anchor_date).

    Nothing is dropped for short history; `short_history_count` is how many
    symbols have fewer than _MIN_BARS bars (they are still evaluated and
    reported as "skipped — not enough history").
    """
    if not candle_data:
        return {}, 0, None

    anchor = next(iter(candle_data.values())).index[-1].date()
    prepared: dict[str, pd.DataFrame] = {}
    short_history = 0
    for symbol, raw_df in candle_data.items():
        ready = _prepare_df(raw_df)
        if ready is None:
            continue
        if len(ready) < _MIN_BARS:
            short_history += 1
        prepared[symbol] = ready
    return prepared, short_history, anchor


def _apply_quality_filter(
    candle_data: dict[str, pd.DataFrame],
) -> tuple[dict[str, pd.DataFrame], int]:
    if not QUALITY_STOCK_WHITELIST:
        return candle_data, 0
    filtered = {k: v for k, v in candle_data.items() if k in QUALITY_STOCK_WHITELIST}
    return filtered, len(candle_data) - len(filtered)


def _apply_weekly_filter(
    candle_data: dict[str, pd.DataFrame],
) -> tuple[dict[str, pd.DataFrame], dict[str, bool | None], dict[str, Any]]:
    weekly_status: dict[str, bool | None] = {}
    if not WEEKLY_RISING_FILTER or not candle_data:
        return candle_data, weekly_status, {
            "attempted": 0,
            "valid": 0,
            "no_data": 0,
            "failed": 0,
            "source": "disabled",
            "api_calls": 0,
            "filtered": 0,
        }

    weekly_data, weekly_report = weekly_candles_from_daily(candle_data)
    filtered: dict[str, pd.DataFrame] = {}
    not_rising = 0
    for symbol, df in candle_data.items():
        weekly_rising = _check_weekly_sma_rising(weekly_data.get(symbol))
        weekly_status[symbol] = weekly_rising
        if weekly_rising is False:
            not_rising += 1
            if WEEKLY_FILTER_EXCLUDES:
                continue
        filtered[symbol] = df

    weekly_report["not_rising"] = not_rising
    weekly_report["filtered"] = len(candle_data) - len(filtered)
    return filtered, weekly_status, weekly_report


def _evaluate_all(
    candle_data: dict[str, pd.DataFrame],
    weekly_status: dict[str, bool | None],
    progress=None,
) -> tuple[dict[str, dict], list[dict], list[dict], dict[str, int], dict[str, int], list[dict]]:
    results: dict[str, dict] = {}
    signals: list[dict] = []
    watchlist_items: list[dict] = []
    status_counts: Counter[str] = Counter({"signal": 0, "watchlist": 0, "none": 0, "error": 0})
    stage_counts: Counter[str] = Counter()
    evaluation_errors: list[dict] = []

    if progress is not None:
        progress.begin_analyse(len(candle_data))

    for eval_idx, (symbol, raw) in enumerate(candle_data.items()):
        if progress is not None:
            progress.analyse_done(eval_idx)   # symbols fully evaluated so far
        try:
            res = evaluate_debug(symbol, raw, weekly_rising=weekly_status.get(symbol))
        except Exception as exc:
            tag = (
                symbol.replace("NSE:", "")
                .replace("BSE:", "")
                .replace("-EQ", "")
                .replace("-BE", "")
                .strip()
                or symbol
            )
            status_counts["error"] += 1
            stage_counts["error"] += 1
            evaluation_errors.append({"symbol": tag, "error": str(exc)})
            continue

        results[res["symbol"]] = res
        status = res.get("status", "none")
        stage = res.get("stage", "unknown")
        values = res.get("values", {})
        status_counts[status] += 1
        stage_counts[stage] += 1
        if status == "signal":
            signals.append(values)
        elif status == "watchlist":
            watchlist_items.append(values)

    if progress is not None:
        progress.analyse_done(len(candle_data))

    return (
        results,
        sorted(signals, key=lambda x: x.get("change_pct", 0), reverse=True),
        sorted(watchlist_items, key=lambda x: x.get("change_pct", 0), reverse=True),
        dict(status_counts),
        dict(stage_counts),
        evaluation_errors,
    )


def _save_debug_outputs(
    results: dict[str, dict],
    csv_output: str | None,
    json_output: str | None,
) -> dict[str, str]:
    outputs: dict[str, str] = {}

    if csv_output:
        save_debug_csv(results, csv_output)
        outputs["csv"] = csv_output
    if json_output:
        save_debug_json(results, json_output)
        outputs["json"] = json_output
    return outputs


def run_historical_scan(
    fyers,
    symbols: list[str],
    target_date: datetime.date,
    *,
    csv_output: str | None = None,
    json_output: str | None = None,
    quiet_mode: bool = True,
    progress=None,
) -> dict[str, Any]:
    """
    Run the debug_run.py historical execution flow without mutating live
    watchlist, alert log, notifications, or signal logs.
    """
    started = time.time()
    print(f"\n🧪  Backtest requested for {target_date.isoformat()} ({len(symbols)} symbols)")
    if target_date.weekday() >= 5:
        print(
            f"⚠️   {target_date.isoformat()} is a {target_date.strftime('%A')} (non-trading day).\n"
            "    Fyers will use the most recent session before it."
        )

    candle_data, fetch_report = fetch_candles_bulk_at_date(
        fyers=fyers,
        symbols=symbols,
        range_to=target_date,
        verbose=False,
        progress=progress,
    )
    print(
        "🧪  Backtest fetch complete: "
        f"{fetch_report.get('valid', 0)} valid | "
        f"{fetch_report.get('no_data', 0)} no-data | "
        f"{fetch_report.get('failed', 0)} failed | "
        f"{fetch_report.get('recovered', 0)} recovered"
    )

    if not candle_data:
        return _empty_result(
            target_date,
            fetch_report,
            started,
            "No candle data returned. Check symbols and Fyers connection.",
        )

    prepared, dropped_short, anchor = _prepare_candle_data(candle_data)
    if dropped_short:
        print(
            f"    ℹ️   {dropped_short} symbol(s) have < {_MIN_BARS} bars — kept in the scan "
            f"and reported as 'not enough history'"
        )
    if not prepared:
        fetch_report["dropped_short"] = dropped_short
        return _empty_result(
            target_date,
            fetch_report,
            started,
            "All DataFrames dropped after preparation. Check connection.",
        )

    prepared, quality_filtered = _apply_quality_filter(prepared)
    if quality_filtered:
        print(f"    Applied quality whitelist filter: skipped {quality_filtered} symbol(s)")
    if not prepared:
        fetch_report["dropped_short"] = dropped_short
        fetch_report["quality_filtered"] = quality_filtered
        return _empty_result(
            target_date,
            fetch_report,
            started,
            "No symbols remain after quality whitelist filtering.",
        )

    weekly_status: dict[str, bool | None] = {}
    weekly_report: dict[str, Any] = {
        "attempted": 0,
        "valid": 0,
        "no_data": 0,
        "failed": 0,
        "filtered": 0,
        "source": "disabled",
        "api_calls": 0,
    }
    if WEEKLY_RISING_FILTER:
        print("\n📥  Deriving weekly candle data from daily bars …")
        prepared, weekly_status, weekly_report = _apply_weekly_filter(prepared)
        print(
            f"    Weekly data: {weekly_report['valid']} valid | "
            f"{weekly_report['no_data']} unavailable | 0 extra API calls"
        )
        if weekly_report.get("filtered", 0):
            print(f"    Weekly rising filter: excluded {weekly_report['filtered']} symbol(s)")
        elif weekly_report.get("not_rising", 0):
            print(
                f"    Weekly SMA44 not rising: {weekly_report['not_rising']} symbol(s) "
                f"(informational only — full universe evaluated)"
            )
        if not prepared:
            fetch_report["dropped_short"] = dropped_short
            fetch_report["quality_filtered"] = quality_filtered
            fetch_report["weekly_valid"] = weekly_report.get("valid", 0)
            fetch_report["weekly_filtered"] = weekly_report.get("filtered", 0)
            return _empty_result(
                target_date,
                fetch_report,
                started,
                "No symbols remain after weekly rising filter.",
            )

    resolved_date = anchor
    window_start = resolved_date - datetime.timedelta(days=_LOOKBACK_DAYS - 1) if resolved_date else None
    requested_label = target_date.strftime("%d %b %Y")
    resolved_label = resolved_date.strftime("%d %b %Y") if resolved_date else None
    print(
        f"\n    {len(prepared)} stock(s) ready  |  "
        f"requested: {requested_label}  |  resolved to: {resolved_label}"
    )
    if resolved_date and resolved_date != target_date:
        print(f"    ℹ️   {requested_label} was a non-trading day — analysis reflects {resolved_label}")
    print()

    (
        results,
        signals,
        watchlist_items,
        status_counts,
        stage_counts,
        evaluation_errors,
    ) = _evaluate_all(prepared, weekly_status, progress=progress)

    # ── Symbols with no usable data: list them so the full universe is visible ─
    # They are NOT counted as evaluated and are never mistaken for rejections.
    unusable = [
        sym for sym in symbols
        if (sym not in candle_data or _prepare_df(candle_data.get(sym)) is None)
        and (not QUALITY_STOCK_WHITELIST or sym in QUALITY_STOCK_WHITELIST)
    ]
    no_data_count = 0
    for sym in unusable:
        row = no_data_result(sym)
        if row["symbol"] not in results:
            results[row["symbol"]] = row
            no_data_count += 1
    if no_data_count:
        status_counts["no_data"] = no_data_count
        stage_counts["no_data"] = no_data_count

    if quiet_mode:
        print("\n" + "=" * 70)
        print("SCAN RESULTS (Summary Only — details returned to website)")
        print("=" * 70)
        summary_table_detailed(results)

    outputs = _save_debug_outputs(
        results,
        csv_output,
        json_output,
    )

    fetch_report.update(
        {
            "daily_valid": fetch_report.get("valid", 0),
            "prepared": len(prepared),
            "evaluated": len(results) - no_data_count,
            "universe_total": len(symbols),
            "no_data_symbols": no_data_count,
            "dropped_short": dropped_short,
            "quality_filtered": quality_filtered,
            "weekly_valid": weekly_report.get("valid", 0),
            "weekly_no_data": weekly_report.get("no_data", 0),
            "weekly_filtered": weekly_report.get("filtered", 0),
            "runtime_seconds": round(time.time() - started, 1),
            "status_counts": status_counts,
            "stage_counts": stage_counts,
            "evaluation_errors": evaluation_errors,
            "debug_outputs": outputs,
        }
    )
    print(
        "🧪  Backtest evaluated: "
        f"{len(results) - no_data_count} evaluated of {len(symbols)} in universe | "
        f"{no_data_count} no-data | "
        f"{len(signals)} trade ready | "
        f"{len(watchlist_items)} watchlist | "
        f"{status_counts.get('none', 0)} rejected | "
        f"{status_counts.get('error', 0)} errors"
    )

    return {
        "signals": signals,
        "watchlist_items": watchlist_items,
        "results": results,
        "report": fetch_report,
        "requested_date": target_date.isoformat(),
        "resolved_date": resolved_date.isoformat() if resolved_date else None,
        "window_start": window_start.isoformat() if window_start else None,
        "error": None,
    }

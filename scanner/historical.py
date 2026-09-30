from __future__ import annotations

import datetime
import time
from collections import Counter
from typing import Any

import pandas as pd

from config.settings import (
    SMA_PERIOD,
    SMA_SLOPE_LOOKBACK,
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
from utils.scan_control import ScanCancelled, check_cancel

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


def _tag(symbol: str) -> str:
    """Result-row key for a Fyers symbol. Mirrors evaluate_debug()/no_data_result()."""
    return (
        symbol.replace("NSE:", "")
        .replace("BSE:", "")
        .replace("-EQ", "")
        .replace("-BE", "")
        .strip()
        or symbol
    )


def _rows_for_unanalysed(
    symbols: list[str],
    results: dict[str, dict],
    ledger: dict[str, dict],
    weekly_status: dict[str, bool | None],
    candle_data: dict[str, pd.DataFrame],
) -> int:
    """
    Guarantee: every symbol of the universe ends with a visible result row.

    Any symbol that has no row yet gets a "not analysed" row that carries the
    real reason (Fyers ledger entry, weekly filter, ...) instead of silently
    vanishing. Returns how many rows were added.
    """
    added = 0
    for sym in dict.fromkeys(symbols):
        if QUALITY_STOCK_WHITELIST and sym not in QUALITY_STOCK_WHITELIST:
            continue
        tag = _tag(sym)
        if tag in results:
            continue
        entry = ledger.get(sym.replace("NSE:", "").replace("-EQ", ""))
        if entry:
            row = no_data_result(
                sym, reason=entry["detail"], category=entry["category"],
                explanation=entry["detail"],
            )
        elif WEEKLY_FILTER_EXCLUDES and weekly_status.get(sym) is False:
            detail = ("Excluded by the weekly SMA44-rising filter "
                      "(WEEKLY_FILTER_EXCLUDES is on).")
            row = no_data_result(
                sym, reason=detail,
                category="Not analysed — excluded by weekly filter", explanation=detail,
            )
        elif _prepare_df(candle_data.get(sym)) is not None:
            detail = ("Candles were received but the stock produced no result row. "
                      "This is an internal gap, not a judgement on the chart.")
            row = no_data_result(
                sym, reason=detail,
                category="Not analysed — internal evaluation error", explanation=detail,
            )
        else:
            row = no_data_result(sym)
        results[tag] = row
        added += 1
    return added


def _universe_audit(
    symbols: list[str],
    results: dict[str, dict],
    universe_meta: dict | None,
) -> dict[str, Any]:
    """Proof that the whole universe is accounted for, plus where the list came from."""
    tags = [_tag(s) for s in dict.fromkeys(symbols)
            if not QUALITY_STOCK_WHITELIST or s in QUALITY_STOCK_WHITELIST]
    counts = Counter(tags)
    meta = universe_meta or {}
    return {
        "total": len(tags),
        "with_result_row": sum(1 for t in tags if t in results),
        "missing_result_rows": sorted(t for t in tags if t not in results),
        "tag_collisions": sorted(t for t, c in counts.items() if c > 1),
        "symbols": tags,
        "index": meta.get("index", "NIFTY 500"),
        "source": meta.get("source"),
        "complete": meta.get("complete"),
        "built_at": meta.get("built_at"),
        "list_saved_at": meta.get("list_saved_at"),
        "nifty50_source": meta.get("nifty50_source"),
        "error": meta.get("error"),
    }


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
    results: dict[str, dict] | None = None,
) -> dict[str, Any]:
    report = dict(fetch_report)
    results = results if results is not None else {}
    report.update(
        {
            "no_data_symbols": len(results),
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
        "results": results,
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

    # Most common newest-bar date across the universe. Never the first stock's
    # date: one stale or lagging stock must not decide the resolved session.
    _last_dates = Counter(
        df.index[-1].date() for df in candle_data.values() if df is not None and not df.empty
    )
    anchor = _last_dates.most_common(1)[0][0] if _last_dates else None
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
    cancel=None,
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
        check_cancel(cancel)
        if progress is not None:
            progress.analyse_done(eval_idx)   # symbols fully evaluated so far
        try:
            res = evaluate_debug(symbol, raw, weekly_rising=weekly_status.get(symbol))
        except ScanCancelled:
            raise
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
            # Never let a stock vanish from the results: show it with the reason.
            err_row = no_data_result(
                symbol,
                reason=f"Evaluation error: {type(exc).__name__}: {exc}",
                category="Not analysed — internal evaluation error",
                explanation=(f"The scanner hit an internal error while evaluating this stock "
                             f"({type(exc).__name__}: {exc}). Not a judgement on the chart."),
            )
            results[err_row["symbol"]] = err_row
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
    cancel=None,
    universe_meta: dict | None = None,
) -> dict[str, Any]:
    """
    Run the debug_run.py historical execution flow without mutating live
    watchlist, alert log, notifications, or signal logs.

    Every symbol passed in ends with a result row (evaluated, or "not analysed"
    with the real reason) — see _rows_for_unanalysed().
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
        cancel=cancel,
    )
    print(
        "🧪  Backtest fetch complete: "
        f"{fetch_report.get('valid', 0)} valid | "
        f"{fetch_report.get('no_data', 0)} no-data | "
        f"{fetch_report.get('failed', 0)} failed | "
        f"{fetch_report.get('recovered', 0)} recovered"
    )

    if not candle_data:
        _early: dict[str, dict] = {}
        _rows_for_unanalysed(symbols, _early, fetch_report.get("ledger", {}), {}, {})
        fetch_report["universe_total"] = len(symbols)
        fetch_report["universe"] = _universe_audit(symbols, _early, universe_meta)
        return _empty_result(
            target_date,
            fetch_report,
            started,
            "No candle data returned. Check symbols and Fyers connection.",
            results=_early,
        )

    prepared, dropped_short, anchor = _prepare_candle_data(candle_data)
    if dropped_short:
        print(
            f"    ℹ️   {dropped_short} symbol(s) have < {_MIN_BARS} bars — kept in the scan "
            f"and reported as 'not enough history'"
        )
    if not prepared:
        fetch_report["dropped_short"] = dropped_short
        _early = {}
        _rows_for_unanalysed(symbols, _early, fetch_report.get("ledger", {}), {}, candle_data)
        fetch_report["universe_total"] = len(symbols)
        fetch_report["universe"] = _universe_audit(symbols, _early, universe_meta)
        return _empty_result(
            target_date,
            fetch_report,
            started,
            "All DataFrames dropped after preparation. Check connection.",
            results=_early,
        )

    prepared, quality_filtered = _apply_quality_filter(prepared)
    if quality_filtered:
        print(f"    Applied quality whitelist filter: skipped {quality_filtered} symbol(s)")
    if not prepared:
        fetch_report["dropped_short"] = dropped_short
        fetch_report["quality_filtered"] = quality_filtered
        _early = {}
        _rows_for_unanalysed(symbols, _early, fetch_report.get("ledger", {}), {}, candle_data)
        fetch_report["universe_total"] = len(symbols)
        fetch_report["universe"] = _universe_audit(symbols, _early, universe_meta)
        return _empty_result(
            target_date,
            fetch_report,
            started,
            "No symbols remain after quality whitelist filtering.",
            results=_early,
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
            _early = {}
            _rows_for_unanalysed(symbols, _early, fetch_report.get("ledger", {}),
                                 weekly_status, candle_data)
            fetch_report["universe_total"] = len(symbols)
            fetch_report["universe"] = _universe_audit(symbols, _early, universe_meta)
            return _empty_result(
                target_date,
                fetch_report,
                started,
                "No symbols remain after weekly rising filter.",
                results=_early,
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
    ) = _evaluate_all(prepared, weekly_status, progress=progress, cancel=cancel)

    # ── Every universe symbol must have a row ────────────────────────────────
    # Symbols with no usable data (and anything else that has no row) get a
    # "not analysed" row carrying the real reason. They are NOT counted as
    # evaluated and are never mistaken for rejections.
    _ledger = fetch_report.get("ledger", {})
    no_data_count = _rows_for_unanalysed(symbols, results, _ledger, weekly_status, candle_data)
    if no_data_count:
        status_counts["no_data"] = no_data_count
        stage_counts["no_data"] = no_data_count

    _min_raw = SMA_PERIOD + SMA_SLOPE_LOOKBACK
    for _sym, _df in prepared.items():
        if len(_df) < _min_raw:
            _ledger.setdefault(_sym.replace("NSE:", "").replace("-EQ", ""), {
                "status": "insufficient_history", "bars": len(_df),
                "category": "Not evaluated — not enough price history",
                "detail": f"{len(_df)} daily candles; {_min_raw} are needed for the SMA44 trend check.",
            })
    for _e in evaluation_errors:
        _ledger[_e["symbol"]] = {"status": "eval_error", "category": "Not evaluated — internal error",
                                 "detail": _e["error"]}

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
            "evaluated": len(results) - no_data_count - len(evaluation_errors),
            "universe_total": len(symbols),
            "universe": _universe_audit(symbols, results, universe_meta),
            "ledger": _ledger,
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


# ── Saved-result universe top-up ─────────────────────────────────────────────
def backtest_universe_gap(
    payload: dict[str, Any],
    symbols: list[str],
) -> tuple[list[str], list[str]]:
    """
    Compare a saved backtest payload with the current Nifty 500 universe.

    Returns (missing_symbols, removed_tags):
      missing_symbols  Fyers symbols of the universe that have NO result row in
                       the saved payload (e.g. added to the index after it was saved).
      removed_tags     result rows whose stock is no longer in the universe.
    Works for old saves too (it only needs payload["backtest_results"]).
    """
    rows = payload.get("backtest_results") or []
    have = {r.get("symbol") for r in rows if isinstance(r, dict) and r.get("symbol")}
    universe = list(dict.fromkeys(symbols))
    uni_tags = {_tag(s) for s in universe}
    missing = [s for s in universe if _tag(s) not in have]
    removed = sorted(t for t in have if t not in uni_tags)
    return missing, removed


def topup_historical_scan(
    fyers,
    symbols: list[str],
    target_date: datetime.date,
    base_payload: dict[str, Any],
    *,
    universe_meta: dict | None = None,
    prune_removed: bool = True,
    progress=None,
    cancel=None,
) -> dict[str, Any]:
    """
    Bring a saved backtest in line with the current universe WITHOUT rescanning it.

    Only the symbols that have no row in the saved payload are fetched from
    Fyers (typically one or a handful). Every other row is reused untouched.
    Stocks that have left the Nifty 500 are dropped (only when prune_removed).

    Returns a dict shaped like run_historical_scan()'s result so the caller's
    normal payload/saving code works unchanged.
    """
    started = time.time()
    base_dbg = base_payload.get("debug") or {}
    by_tag: dict[str, dict] = {
        r["symbol"]: r
        for r in (base_payload.get("backtest_results") or [])
        if isinstance(r, dict) and r.get("symbol")
    }
    universe = list(dict.fromkeys(symbols))
    missing, removed = backtest_universe_gap(base_payload, universe)
    if not prune_removed:
        removed = []

    print(
        f"\n🧪  Backtest top-up for {target_date.isoformat()}: "
        f"{len(missing)} stock(s) to add, {len(removed)} to remove, "
        f"{len(by_tag) - len(removed)} reused as saved"
    )

    sub: dict[str, Any] = {}
    sub_report: dict[str, Any] = {}
    if missing:
        sub = run_historical_scan(
            fyers, missing, target_date,
            quiet_mode=True, progress=progress, cancel=cancel,
            universe_meta=universe_meta,
        )
        sub_report = sub.get("report") or {}

    for tag in removed:
        by_tag.pop(tag, None)
    added_tags = []
    for tag, row in (sub.get("results") or {}).items():
        by_tag[tag] = row
        added_tags.append(tag)

    # ── rebuild derived lists / counters from the merged rows ────────────────
    def _chg(v: dict) -> float:
        try:
            return float(v.get("change_pct", 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    signals = sorted(
        [r["values"] for r in by_tag.values()
         if r.get("status") == "signal" and isinstance(r.get("values"), dict)],
        key=_chg, reverse=True,
    )
    watchlist_items = sorted(
        [r["values"] for r in by_tag.values()
         if r.get("status") == "watchlist" and isinstance(r.get("values"), dict)],
        key=_chg, reverse=True,
    )
    status_counts: Counter[str] = Counter({"signal": 0, "watchlist": 0, "none": 0, "error": 0})
    stage_counts: Counter[str] = Counter()
    for r in by_tag.values():
        st = r.get("status", "none")
        if st == "no_data":
            if str(r.get("category", "")).startswith("Not analysed — internal"):
                status_counts["error"] += 1
                stage_counts["error"] += 1
            else:
                status_counts["no_data"] += 1
                stage_counts["no_data"] += 1
        else:
            status_counts[st] += 1
            stage_counts[r.get("stage", "unknown")] += 1
    evaluated = sum(status_counts.get(k, 0) for k in ("signal", "watchlist", "none"))

    uni_tags = {_tag(s) for s in universe}
    ledger = {k: v for k, v in (base_dbg.get("skipped") or {}).items() if k in uni_tags}
    for k in removed:
        ledger.pop(k, None)
    for tag in added_tags:
        ledger.pop(tag, None)
    ledger.update(sub_report.get("ledger") or {})

    failed_symbols = sorted(
        {t for t in (base_dbg.get("failed_symbols") or []) if t in uni_tags}
        | set(sub_report.get("failed_symbols") or [])
    )
    eval_errors = [e for e in (base_dbg.get("evaluation_errors") or [])
                   if e.get("symbol") in uni_tags and e.get("symbol") not in added_tags]
    eval_errors += list(sub_report.get("evaluation_errors") or [])

    def _n(key: str) -> int:
        return int(base_dbg.get(key) or 0) + int(sub_report.get(key) or 0)

    report: dict[str, Any] = {
        "attempted": len(universe),
        "universe_total": len(universe),
        "evaluated": evaluated,
        "valid": _n("daily_valid"),
        "daily_valid": _n("daily_valid"),
        "prepared": _n("prepared"),
        "runtime_seconds": round(time.time() - started, 1),
        "status_counts": dict(status_counts),
        "stage_counts": dict(stage_counts),
        "dropped_short": _n("dropped_short"),
        "quality_filtered": int(base_dbg.get("quality_filtered") or 0),
        "weekly_valid": _n("weekly_valid"),
        "weekly_no_data": _n("weekly_no_data"),
        "weekly_filtered": int(base_dbg.get("weekly_filtered") or 0),
        "failed": len(failed_symbols),
        "no_data": status_counts.get("no_data", 0),
        "no_data_symbols": status_counts.get("no_data", 0),
        "recovered": int(base_dbg.get("recovered") or 0) + int(sub_report.get("recovered") or 0),
        "persistent_recovered": _n("persistent_recovered"),
        "persistent_retries": _n("persistent_retries"),
        "evaluation_errors": eval_errors,
        "debug_outputs": {},
        "failed_symbols": failed_symbols,
        "short_history": sum(1 for v in ledger.values() if v.get("status") == "short_history"),
        "stale": sum(1 for v in ledger.values() if v.get("status") == "stale_data"),
        "ledger": ledger,
        "topup": {
            "added": sorted(added_tags),
            "removed": removed,
            "fyers_symbols_requested": len(missing),
        },
        "universe": _universe_audit(universe, by_tag, universe_meta),
    }
    print(
        f"🧪  Top-up done: added {sorted(added_tags)} | removed {removed} | "
        f"{len(by_tag)} rows total"
    )
    return {
        "signals": signals,
        "watchlist_items": watchlist_items,
        "results": by_tag,
        "report": report,
        "requested_date": base_dbg.get("requested_date") or target_date.isoformat(),
        "resolved_date": base_dbg.get("resolved_date"),
        "window_start": base_dbg.get("window_start"),
        "error": None,
    }

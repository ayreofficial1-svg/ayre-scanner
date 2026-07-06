"""
scanner/engine.py
─────────────────
Orchestrates the full scan pass.

Execution flow
──────────────
1. Fetch candle data for all symbols and assert completeness.
2. Prune watchlist entries where price has fallen below SMA44
   (setup invalidated — no point waiting for a crossover).
3. For each symbol, evaluate the three conditions (C1, C2, C3).
4. Dispatch alerts for new signals; update watchlist and alert log.

Return value
────────────
  (signals, watchlist_items, fetch_report)

  signals        — stocks where all conditions are met (C1–C3)
  watchlist_items— stocks where C1+C2 pass and crossover is pending
  fetch_report   — completeness report from fetch_candles_bulk:
                   {attempted, valid, no_data, failed, recovered, missing}
                   main.py writes attempted → total_attempted and
                   valid → total_scanned in _state for /api/results.

Both lists are sorted by change_pct descending.
"""

import time
import pandas as pd
from fyers_apiv3 import fyersModel

from config.settings import (
    QUALITY_STOCK_WHITELIST,
    WEEKLY_C1A_LOOKBACK,
    WEEKLY_RISING_FILTER,
)
from data.candles import fetch_candles_bulk_persistent, weekly_candles_from_daily
from indicators.technical import compute_indicators
from scanner.conditions import evaluate
from scanner.watchlist import (
    add_to_watchlist,
    remove_from_watchlist,
    save_watchlist,
    save_alert_log,
    is_already_alerted,
    mark_alerted,
)
from alerts.notify import fire_alert
from utils.logger import log_signal


# ── Weekly pre-filter helper ─────────────────────────────────────────────────
def _check_weekly_sma_rising(
    weekly_df: pd.DataFrame | None,
    lookback: int = WEEKLY_C1A_LOOKBACK,
) -> bool | None:
    """
    Check if weekly SMA44 is rising (point check: SMA44[today] > SMA44[lookback bars ago]).
    Returns None when weekly data is unavailable or insufficient.
    """
    # SMA44 warmup requires 44 bars; then we need lookback + 1 bars for comparison
    if weekly_df is None or len(weekly_df) < 44 + lookback + 1:
        return None

    try:
        df_ind = compute_indicators(weekly_df.copy())
        df_clean = df_ind.dropna(subset=["SMA44"]).copy()

        if len(df_clean) < lookback + 1:
            return None

        sma_today = float(df_clean["SMA44"].iloc[-1])
        sma_lookback_ago = float(df_clean["SMA44"].iloc[-1 - lookback])

        return bool(sma_today > sma_lookback_ago)
    except Exception:
        return None

def _cleanup_broken_structures(
    watchlist   : dict,
    candle_data : dict,
) -> list[str]:
    """
    Remove watchlist entries where the latest close is below SMA44.

    This prevents stale setups from lingering until their TTL expires when
    the price action has already broken down.  Runs once per scan, after
    candle data is fetched, before condition evaluation.

    Returns a list of removed symbols for logging.
    """
    removed = []
    for sym in list(watchlist.keys()):
        fyers_sym = f"NSE:{sym}-EQ"
        df_raw = candle_data.get(fyers_sym)
        if df_raw is None:
            df_raw = candle_data.get(sym)
        if df_raw is None or len(df_raw) < 44:
            continue
        try:
            df_ind  = compute_indicators(df_raw.copy())
            df_ok   = df_ind.dropna(subset=["SMA44"])
            if df_ok.empty:
                continue
            if float(df_ok["Close"].iloc[-1]) < float(df_ok["SMA44"].iloc[-1]):
                remove_from_watchlist(watchlist, sym)
                removed.append(sym)
        except Exception:
            pass
    return removed


# ── Main scan loop ────────────────────────────────────────────────────────────

def run_scan(
    fyers     : fyersModel.FyersModel,
    symbols   : list[str],
    interval  : str,
    watchlist : dict,
    alert_log : dict,
    verbose   : bool = False,
) -> tuple[list[dict], list[dict], dict]:
    """
    Execute a full scan across all symbols.

    Parameters
    ----------
    fyers       : authenticated FyersModel instance
    symbols     : list of Fyers-formatted symbols
    interval    : data resolution (always "D" for daily)
    watchlist   : mutable dict loaded from watchlist.json
    alert_log   : mutable dict loaded from alert_log.json
    verbose     : if True, print per-symbol results

    Returns
    -------
    (signals, watchlist_items, fetch_report)

    signals and watchlist_items are sorted by change_pct descending.
    fetch_report is the completeness dict from fetch_candles_bulk.
    """
    signals         : list[dict] = []
    watchlist_items : list[dict] = []
    promoted        : list[str]  = []
    t_start = time.time()

    # ── Step 1: Fetch daily candles once ─────────────────────────────────────
    print(f"\n⚙️   Fetching daily data for {len(symbols)} stocks …")
    if WEEKLY_RISING_FILTER:
        print("⚙️   Weekly bars will be derived from daily data (0 extra API calls) …\n")

    weekly_data: dict = {}
    weekly_report: dict = {"valid": 0, "no_data": 0, "failed": 0, "attempted": 0}

    candle_data, fetch_report = fetch_candles_bulk_persistent(
        fyers, symbols, interval, verbose
    )
    if WEEKLY_RISING_FILTER:
        weekly_data, weekly_report = weekly_candles_from_daily(candle_data)

    # ── Apply quality stock whitelist filter ──────────────────────────────────
    quality_filtered = 0
    if QUALITY_STOCK_WHITELIST:
        original_count = len(candle_data)
        candle_data = {k: v for k, v in candle_data.items() if k in QUALITY_STOCK_WHITELIST}
        quality_filtered = original_count - len(candle_data)
        if quality_filtered > 0:
            print(f"   🔍  Applied quality whitelist filter: skipped {quality_filtered} symbols\n")

    # ── Step 1b: Apply weekly pre-filter results (data already fetched above) ─
    weekly_status: dict[str, bool | None] = {}
    weekly_filtered = 0
    symbols_to_evaluate = list(candle_data.keys())
    if WEEKLY_RISING_FILTER:
        print(
            f"   📊  Weekly data: {weekly_report['valid']} valid | "
            f"{weekly_report['no_data']} skipped\n"
        )

        # Filter symbols: only keep those where weekly SMA44 is rising
        filtered_count = 0
        symbols_to_evaluate_filtered = []
        for sym in symbols_to_evaluate:
            weekly_df = weekly_data.get(sym)
            weekly_rising = _check_weekly_sma_rising(weekly_df)
            weekly_status[sym] = weekly_rising
            if weekly_rising is not False:
                symbols_to_evaluate_filtered.append(sym)
            else:
                filtered_count += 1

        if filtered_count > 0:
            print(f"   📉  Weekly rising filter: {filtered_count} symbols excluded (weekly SMA44 not rising)\n")

        symbols_to_evaluate = symbols_to_evaluate_filtered
        candle_data = {k: v for k, v in candle_data.items() if k in symbols_to_evaluate}
        weekly_filtered = filtered_count

    # ── Completeness assertion ────────────────────────────────────────────────
    # Every symbol must land in exactly one of: valid, no_data, failed.
    # If the counts don't add up, a bug in the fetcher has caused silent drops.
    attempted = fetch_report["attempted"]
    accounted = fetch_report["valid"] + fetch_report["no_data"] + fetch_report["failed"]

    print(
        f"   ✅  Fetch complete: {fetch_report['valid']} valid | "
        f"{fetch_report['no_data']} no-data | "
        f"{fetch_report['failed']} failed | "
        f"{fetch_report['recovered']} recovered\n"
    )
    fetch_report["daily_valid"] = fetch_report["valid"]
    fetch_report["quality_filtered"] = quality_filtered
    fetch_report["weekly_filtered"] = weekly_filtered
    fetch_report["evaluated"] = len(candle_data)

    if accounted != attempted:
        gap = attempted - accounted
        print(
            f"   ❌  COVERAGE GAP: {gap} of {attempted} symbols unaccounted for. "
            f"This is a bug — check fetch_candles_bulk classification logic."
        )
    elif fetch_report["failed"] > 0:
        failed_syms = fetch_report["missing"][: fetch_report["failed"]]
        print(
            f"   ⚠️   {fetch_report['failed']} symbols failed after retry "
            f"(transient / rate-limit): "
            f"{failed_syms[:5]}" + (" …" if len(failed_syms) > 5 else "")
        )

    # ── Step 2: Prune watchlist entries with broken SMA44 structure ───────────
    removed_broken = _cleanup_broken_structures(watchlist, candle_data)
    if removed_broken:
        print(
            f"   🗑️   Removed {len(removed_broken)} watchlist entries "
            f"(price below SMA44): {', '.join(removed_broken[:10])}"
            + (" …" if len(removed_broken) > 10 else "")
        )
        save_watchlist(watchlist)

    # ── Step 3: Evaluate each symbol ─────────────────────────────────────────
    print("⚙️   Evaluating conditions …")

    for symbol, raw in candle_data.items():
        try:
            result = evaluate(symbol, raw, weekly_rising=weekly_status.get(symbol))

            if result["status"] == "signal":
                d       = result["data"]
                display = d["symbol"]

                is_promoted          = display in watchlist
                d["promoted"]        = is_promoted
                d["watchlist_since"] = watchlist.get(display, {}).get("added", "")
                d["is_new_alert"]    = not is_already_alerted(display, alert_log)

                signals.append(d)

                if is_promoted:
                    promoted.append(display)
                    remove_from_watchlist(watchlist, display)

                if d["is_new_alert"]:
                    source = "🎯 Watchlist Promoted!" if is_promoted else "New Signal"
                    fire_alert(
                        symbol             = display,
                        close              = d["close"],
                        sma44              = d["sma44"],
                        change_pct         = d["change_pct"],
                        cross_type         = d["cross_type"],
                        crossover_bars_ago = d.get("crossover_bars_ago", 0),
                        source             = source,
                    )
                    mark_alerted(display, alert_log, d["close"])
                    save_alert_log(alert_log)
                    log_signal(d)

            elif result["status"] == "watchlist":
                d = result["data"]
                add_to_watchlist(
                    watchlist,
                    symbol = d["symbol"],
                    close  = d["close"],
                    sma44  = d["sma44"],
                )
                watchlist_items.append(d)

            if verbose and result["status"] != "none":
                d    = result["data"]
                flag = "✅" if result["status"] == "signal" else "👀"
                print(
                    f"   {flag} {d['symbol']:<18} "
                    f"C={d['close']:.2f}  SMA44={d['sma44']:.2f}  "
                    f"MACD={d['macd']:.4f}  Signal={d['macd_signal']:.4f}  "
                    f"[{result['status'].upper()} / {d['cross_type']}]"
                )

        except Exception as e:
            if verbose:
                print(f"   ⚠️  {symbol}: {e}")
            continue

    save_watchlist(watchlist)

    t_total = time.time() - t_start
    print(
        f"\n   ✅  {len(signals)} signal(s)  |  "
        f"👀  {len(watchlist_items)} watchlist  |  "
        f"🎯  {len(promoted)} promoted  |  "
        f"⏱   {t_total:.0f}s"
    )

    return (
        sorted(signals,         key=lambda x: x.get("change_pct", 0), reverse=True),
        sorted(watchlist_items, key=lambda x: x.get("change_pct", 0), reverse=True),
        fetch_report,
    )

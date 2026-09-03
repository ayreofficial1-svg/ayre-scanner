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

  signals        — stocks where all conditions are met (C1–C3). Each dict
                   includes "trade_ready_at" (ISO 8601, IST offset) when
                   determinable: the real date/time this symbol first
                   satisfied all conditions, reconstructed from intraday
                   market data (see _reconstruct_trade_ready_time) — never
                   the time of the current scan. Omitted, not faked, when
                   intraday data isn't available.
  watchlist_items— stocks where C1+C2 pass and crossover is pending
  fetch_report   — completeness report from fetch_candles_bulk:
                   {attempted, valid, no_data, failed, recovered, missing}
                   main.py writes attempted → total_attempted and
                   valid → total_scanned in _state for /api/results.

Both lists are sorted by change_pct descending.
"""

import datetime
import time
import pandas as pd
from fyers_apiv3 import fyersModel

from config.settings import (
    QUALITY_STOCK_WHITELIST,
    WEEKLY_C1A_LOOKBACK,
    WEEKLY_RISING_FILTER,
)
from data.candles import (
    fetch_candles_bulk_persistent,
    fetch_intraday_candles,
    weekly_candles_from_daily,
)
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


# ── Trade Ready timestamp reconstruction ────────────────────────────────────
def _reconstruct_trade_ready_time(
    fyers         : fyersModel.FyersModel,
    fyers_symbol  : str,
    daily_df      : pd.DataFrame,
    weekly_rising : bool | None,
) -> str | None:
    """
    Determine the real IST minute at which `fyers_symbol` first satisfied
    every Trade Ready condition (C1+C2+C3) today, using 1-minute intraday
    candles from Fyers — independent of when the scanner happened to run.

    How it works
    ------------
    The daily "D" bar for today (already the last row of `daily_df` during
    market hours) is a live/forming candle whose Close is just the latest
    traded price. It has no record of *when* that price was reached, so it
    alone cannot answer the question. Instead, this replays today's session
    minute by minute: at each intraday bar, it builds a provisional "today"
    daily bar (Open = day's open so far, High/Low = running extremes so far,
    Close = that minute's price) and calls the exact same `conditions.evaluate`
    used by the main scan against [prior confirmed daily bars] + [that
    provisional bar]. The first minute this returns "signal" is the true
    Trade Ready moment.

    Returns an ISO-8601 string with the +05:30 offset, accurate to the
    minute, or None if intraday data isn't available — this function never
    substitutes the scan time or any other guess.
    """
    if daily_df is None or daily_df.empty:
        return None

    intraday = fetch_intraday_candles(fyers, fyers_symbol, datetime.date.today())
    if intraday is None or intraday.empty:
        return None

    # History = all confirmed prior daily bars (drop today's still-forming
    # daily bar; it's rebuilt minute-by-minute below).
    history = daily_df.iloc[:-1] if len(daily_df) > 1 else daily_df.iloc[:0]

    running_open = running_high = running_low = None
    plain_symbol = fyers_symbol.replace("NSE:", "").replace("-EQ", "")

    for ts, bar in intraday.iterrows():
        o, h, l, c, v = (
            float(bar["Open"]), float(bar["High"]),
            float(bar["Low"]),  float(bar["Close"]), float(bar["Volume"]),
        )
        running_open = o if running_open is None else running_open
        running_high = h if running_high is None else max(running_high, h)
        running_low  = l if running_low  is None else min(running_low, l)

        provisional_today = pd.DataFrame(
            {
                "Open"  : [running_open],
                "High"  : [running_high],
                "Low"   : [running_low],
                "Close" : [c],
                "Volume": [v],
            },
            index=pd.DatetimeIndex([pd.Timestamp(datetime.date.today())]),
        )
        df_partial = pd.concat([history, provisional_today])

        try:
            result = evaluate(plain_symbol, df_partial, weekly_rising=weekly_rising)
        except Exception:
            continue

        if result["status"] == "signal":
            return ts.strftime("%Y-%m-%dT%H:%M:00+05:30")

    return None


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
                    # Reconstruct the REAL moment this stock became Trade
                    # Ready from intraday market data — not the scan time.
                    # Only done once, here, on the first alert of the day;
                    # persisted below so later scans reuse it unchanged.
                    trade_ready_at = _reconstruct_trade_ready_time(
                        fyers, symbol, raw, weekly_status.get(symbol)
                    )
                    mark_alerted(display, alert_log, d["close"], trade_ready_at)
                    save_alert_log(alert_log)
                    log_signal(d)

                # Trade Ready timestamp — the real date/time this symbol
                # first satisfied all conditions, reconstructed from
                # intraday data (see _reconstruct_trade_ready_time above).
                # Deliberately left unset (never faked with the scan time)
                # when intraday data wasn't available to determine it.
                alert_entry = alert_log.get(display, {})
                if alert_entry.get("trade_ready_at"):
                    d["trade_ready_at"] = alert_entry["trade_ready_at"]

                signals.append(d)

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

"""
scanner/debug_evaluate.py
Debug wrapper around the simplified scanner.

All pass/fail decisions come from scanner.conditions.evaluate().
This module mirrors the production logic only to surface display metrics,
failure stages, and HTML/CSV/JSON debug output.
"""

from __future__ import annotations

import csv
import datetime as _dt
import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from config.settings import (
    ATR_SLOPE_MIN,
    C1A_LOOKBACK,
    C1A_LINREG_SLOPE_MIN,
    C1A_LINREG_WINDOW,
    DOUBLE_BOTTOM_LOOKBACK,
    IMMINENT_GAP_THRESHOLD,
    IMMINENT_HIST_MIN,
    MACD_CROSSOVER_LOOKBACK,
    MIN_BARS,
    PCT_SLOPE_MAX,
    PCT_SLOPE_MIN,
    SMA44_SUPPORT_BUFFER_PCT,
    SMA_SLOPE_LOOKBACK,
)
from indicators.technical import compute_indicators
from scanner.conditions import evaluate, _RECENT_HALF_SLOPE_MIN, _slope_consistency_pass, _sma_linreg_slope


def _r(v, n: int = 4):
    if v is None:
        return None
    if isinstance(v, float) and np.isnan(v):
        return None
    try:
        return round(float(v), n)
    except (TypeError, ValueError):
        return None


def _result(symbol: str, stage: str, reason: str, values: dict, status: str = "none") -> dict:
    return {
        "symbol": symbol,
        "status": status,
        "stage": stage,
        "reason": reason,
        "values": {k: _r(v) if isinstance(v, float) else v for k, v in values.items()},
    }


_STAGE_ORDER = [
    "preflight",
    "c1_sma_rising",
    "c1_slope",
    "c2_close_vs_sma",
    "c3_macd",
    "passed",
    "passed_imminent",
]

_STAGE_LABEL = {
    "preflight"         : "PRE-FLIGHT — insufficient data",
    "c1_sma_rising"     : "C1 — SMA44 NOT RISING",
    "c1_slope"          : "C1 — SLOPE VALIDATION FAILED (pct / atr / consistency)",
    "c2_close_vs_sma"   : "C2 — SMA44 SUPPORT INTERACTION FAILED",
    "c3_macd"           : "C3 — NO RECENT MACD CROSSOVER",
    "passed"            : "PASSED — TRADE READY",
    "passed_imminent"   : "PASSED — IMMINENT CROSSOVER",
}

_STATUS_ICON = {"signal": "S", "watchlist": "W", "none": "X"}


def _count_consecutive_rises(values: np.ndarray) -> int:
    if len(values) < 2:
        return 0
    rises = 0
    for i in range(1, len(values)):
        idx = -i
        idx_prev = -(i + 1)
        if abs(idx_prev) > len(values):
            break
        if values[idx] > values[idx_prev]:
            rises += 1
        else:
            break
    return rises


def _compute_display_values(df: pd.DataFrame) -> dict:
    base = {
        "raw_bars"                  : len(df),
        "c3_lookback"               : MACD_CROSSOVER_LOOKBACK,
        "sma_slope_lookback"        : SMA_SLOPE_LOOKBACK,
        "pct_slope_min"             : _r(PCT_SLOPE_MIN * 100, 2),
        "pct_slope_max"             : _r(PCT_SLOPE_MAX * 100, 2),
        "atr_slope_min"             : _r(ATR_SLOPE_MIN),
        "imminent_gap_threshold_pct": _r(IMMINENT_GAP_THRESHOLD * 100, 2),
        "imminent_hist_min"         : IMMINENT_HIST_MIN,
        # C2 threshold (informational baseline)
        "sma44_support_buffer_pct"  : _r(SMA44_SUPPORT_BUFFER_PCT * 100, 2),
    }
    if len(df) < MIN_BARS:
        return base

    df_ind = compute_indicators(df.copy())
    df_clean = df_ind.dropna(subset=["SMA44", "MACD", "Signal", "ATR14"]).copy()
    base["valid_bars"] = len(df_clean)

    if len(df_clean) < 2:
        return base

    cur = df_clean.iloc[-1]
    prev = df_clean.iloc[-2]
    n = len(df_clean)

    close_today = float(cur["Close"])
    sma_today = float(cur["SMA44"])
    sma_yesterday = float(prev["SMA44"])
    atr14 = float(cur["ATR14"])

    sma_series = df_clean["SMA44"].values

    # C1a point check (5-bar)
    c1a_lookback_ready = len(df_clean) >= (C1A_LOOKBACK + 1)
    if c1a_lookback_ready:
        sma_n_ago       = float(sma_series[-1 - C1A_LOOKBACK])
        c1a_point_pass  = bool(sma_today > sma_n_ago)
    else:
        sma_n_ago      = None
        c1a_point_pass = False

    # C1a regression check (15-bar OLS)
    c1a_linreg_slope = _sma_linreg_slope(sma_series, C1A_LINREG_WINDOW)
    c1a_linreg_pass  = bool(c1a_linreg_slope >= C1A_LINREG_SLOPE_MIN)

    # C1a sub-test 3: slope recovering — recent 5-bar slope is less negative
    # (or more positive) than the full C1A_LINREG_WINDOW slope, meaning the
    # trend is turning upward even if still net-negative.
    # Mirrors the identical logic in conditions.py.
    _RECOVERY_WINDOW   = 5
    c1a_recovery_slope = _sma_linreg_slope(sma_series, _RECOVERY_WINDOW)
    c1a_recovering     = bool(c1a_recovery_slope > c1a_linreg_slope)

    # OR gate — matches conditions.py exactly (all three sub-tests)
    c1_rising_pass = c1a_point_pass or c1a_linreg_pass or c1a_recovering

    # Keep sma_delta for any downstream display code that still uses it
    sma_delta = sma_today - sma_yesterday
    c1_slope_ready = len(df_clean) >= (SMA_SLOPE_LOOKBACK + 1)

    pct_slope = None
    pct_slope_raw = None
    atr_slope = None
    sma44_lookback = None
    c1_slope_pass = False
    c1_slope_error = None

    if c1_slope_ready:
        sma44_lookback = float(df_clean["SMA44"].iloc[-1 - SMA_SLOPE_LOOKBACK])
        if sma44_lookback <= 0:
            c1_slope_error = "invalid_lookback_sma"
        elif not np.isfinite(atr14) or atr14 <= 0:
            c1_slope_error = "invalid_atr"
        else:
            pct_slope_raw = (sma_today / sma44_lookback) - 1
            pct_slope = pct_slope_raw * 100
            atr_slope = (sma_today - sma44_lookback) / atr14
            # PCT_SLOPE_MIN is now -0.01 (accepts flat/consolidating SMAs).
            # ATR_SLOPE_MIN is now 0.0 (ATR floor removed for flat trends).
            c1_slope_pass = bool(
                PCT_SLOPE_MIN <= pct_slope_raw <= PCT_SLOPE_MAX
                and atr_slope > ATR_SLOPE_MIN
            )

    # Slope consistency sub-windows (mirrors C1c in conditions.py)
    c1c_pass = True
    first_half_slope = None
    second_half_slope = None
    slope_recent_half_min_pct = round(_RECENT_HALF_SLOPE_MIN * 100, 2)

    if c1_slope_ready and sma44_lookback and sma44_lookback > 0:
        sma_series = df_clean["SMA44"].values
        c1c_pass, _fh, _sh = _slope_consistency_pass(sma_series, SMA_SLOPE_LOOKBACK)
        first_half_slope  = round(_fh * 100, 2)
        second_half_slope = round(_sh * 100, 2)

    c1_pass = c1_rising_pass and c1_slope_pass

    # C2 sub-conditions (today's bar)
    # NOTE: C2c (Close > Open, bullish body / doji filter) has been removed.
    # C2 now requires only C2a (low proximity) and C2b (close >= SMA44).
    low_today  = float(cur["Low"])
    c2a_pass   = bool((abs(low_today - sma_today) / sma_today) <= SMA44_SUPPORT_BUFFER_PCT) if sma_today > 0 else False
    c2b_close_pass = bool(close_today >= sma_today)
    c2_pass    = c2a_pass and c2b_close_pass
    low_vs_sma44_pct = (low_today - sma_today) / sma_today * 100 if sma_today > 0 else 0.0
    close_vs_sma_pct = (close_today - sma_today) / sma_today * 100 if sma_today > 0 else 0.0

    if c1a_point_pass and pct_slope_raw is not None and pct_slope_raw >= 0:
        ma_type = "type1"
    elif c1a_linreg_pass or c1a_recovering:
        ma_type = "type2"
    else:
        ma_type = "type3"

    price_interaction_type = (
        "crossover" if low_today < sma_today and close_today >= sma_today else "support"
    )

    is_double_bottom = False
    if len(df_clean) >= DOUBLE_BOTTOM_LOOKBACK + 1:
        prior_bars = df_clean.iloc[-(DOUBLE_BOTTOM_LOOKBACK + 1):-1]
        for _, row in prior_bars.iterrows():
            prior_low = float(row["Low"])
            prior_sma = float(row["SMA44"])
            prior_close = float(row["Close"])
            if prior_sma > 0:
                proximity = abs(prior_low - prior_sma) / prior_sma
                if proximity <= SMA44_SUPPORT_BUFFER_PCT and prior_close >= prior_sma:
                    is_double_bottom = True
                    break

    macd_arr = df_clean["MACD"].values.astype(float)
    signal_arr = df_clean["Signal"].values.astype(float)
    hist_arr = macd_arr - signal_arr

    macd_cur = float(macd_arr[-1])
    signal_cur = float(signal_arr[-1])
    macd_prev = float(macd_arr[-2]) if n >= 2 else macd_cur
    signal_prev = float(signal_arr[-2]) if n >= 2 else signal_cur
    hist_1 = float(hist_arr[-1])
    hist_2 = float(hist_arr[-2]) if n >= 2 else hist_1
    hist_3 = float(hist_arr[-3]) if n >= 3 else hist_2

    crossover_found_at = None
    if len(df_clean) >= (MACD_CROSSOVER_LOOKBACK + 2):
        for k in range(MACD_CROSSOVER_LOOKBACK):
            t = n - (k + 1)
            t_prev = t - 1
            if t_prev < 0:
                break
            if (macd_arr[t_prev] <= signal_arr[t_prev]) and (macd_arr[t] > signal_arr[t]):
                crossover_found_at = k
                break

    hist_consecutive_rising = _count_consecutive_rises(hist_arr)
    imminent_not_crossed = bool(macd_cur < signal_cur)
    denom = abs(signal_cur) if abs(signal_cur) > 1e-9 else 1e-9
    gap = (signal_cur - macd_cur) if imminent_not_crossed else 0.0
    gap_ratio = (gap / denom) if imminent_not_crossed else 0.0
    imminent_hist_ok = bool(hist_consecutive_rising >= IMMINENT_HIST_MIN)
    imminent_gap_ok = bool(gap_ratio <= IMMINENT_GAP_THRESHOLD)

    return {
        **base,
        "c1_pass"               : c1_pass,
        "c1_rising_pass"        : c1_rising_pass,
        "c1a_point_pass"        : c1a_point_pass,
        "c1a_linreg_pass"       : c1a_linreg_pass,
        "c1a_recovering_pass"   : c1a_recovering,
        "c1a_recovery_slope"    : _r(c1a_recovery_slope, 8),
        "c1a_recovery_window"   : _RECOVERY_WINDOW,
        "c1a_sma_n_ago"         : _r(sma_n_ago, 4) if sma_n_ago is not None else None,
        "c1a_lookback"          : C1A_LOOKBACK,
        "c1a_linreg_slope"      : _r(c1a_linreg_slope, 8),
        "c1a_linreg_slope_min"  : C1A_LINREG_SLOPE_MIN,
        "c1a_linreg_window"     : C1A_LINREG_WINDOW,
        "sma44_today"           : _r(sma_today),
        "sma44_yesterday"       : _r(sma_yesterday),
        "sma44_delta"           : _r(sma_delta),
        "c1_slope_pass"         : (
            c1_slope_pass and c1c_pass
            if not c1_slope_error else False
        ),
        "c1_slope_ready"        : c1_slope_ready,
        "c1_slope_error"        : c1_slope_error,
        "sma44_lookback"        : _r(sma44_lookback),
        "atr14"                 : _r(atr14),
        "pct_slope"             : _r(pct_slope, 2),
        "atr_slope"             : _r(atr_slope),
        "c1c_slope_consistency_pass"  : bool(c1c_pass),
        "slope_first_half_pct"        : first_half_slope,
        "slope_second_half_pct"       : second_half_slope,
        "slope_recent_half_min_pct"   : slope_recent_half_min_pct,
        "c2_pass"               : bool(c2_pass),
        "c2a_low_proximity_pass" : bool(c2a_pass),
        "c2b_close_above_sma_pass": bool(c2b_close_pass),
        "close"                 : _r(close_today),
        "low_today"             : _r(low_today),
        "low_vs_sma44_pct"      : _r(low_vs_sma44_pct, 2),
        "close_vs_sma_pct"      : _r(close_vs_sma_pct, 2),
        "ma_type"               : ma_type,
        "price_interaction_type": price_interaction_type,
        "is_double_bottom"      : is_double_bottom,
        "double_bottom_lookback": DOUBLE_BOTTOM_LOOKBACK,
        "crossover_found_bars_ago": crossover_found_at,
        "c3_confirmed"          : bool(crossover_found_at is not None),
        "macd_cur"              : _r(macd_cur),
        "signal_cur"            : _r(signal_cur),
        "macd_prev"             : _r(macd_prev),
        "signal_prev"           : _r(signal_prev),
        "hist_1"                : _r(hist_1),
        "hist_2"                : _r(hist_2),
        "hist_3"                : _r(hist_3),
        "hist_consecutive_rising": hist_consecutive_rising,
        "imminent_not_crossed"  : imminent_not_crossed,
        "imminent_hist_ok"      : imminent_hist_ok,
        "imminent_gap_ok"       : imminent_gap_ok,
        "imminent_gap_ratio"    : _r(gap_ratio * 100, 2),
        "is_imminent_crossover" : bool(imminent_not_crossed and imminent_hist_ok and imminent_gap_ok),
    }


def evaluate_debug(
    symbol: str,
    df: pd.DataFrame,
    weekly_rising: bool | None = None,
) -> dict:
    tag = (
        symbol.replace("NSE:", "")
        .replace("BSE:", "")
        .replace("-EQ", "")
        .replace("-BE", "")
        .strip()
        or symbol
    )

    result = evaluate(symbol, df, weekly_rising=weekly_rising)
    status = result["status"]
    payload = result.get("data", {})
    display = _compute_display_values(df)
    display["weekly_rising"] = weekly_rising
    display.update(payload)

    raw_bars = display.get("raw_bars", 0)
    valid_bars = display.get("valid_bars", 0)

    if raw_bars < MIN_BARS or valid_bars < 2:
        stage = "preflight"
        reason = (
            f"Insufficient data: {raw_bars} raw bars, {valid_bars} valid after indicator warm-up."
        )
    elif not display.get("c1_rising_pass", False):
        stage = "c1_sma_rising"
        reason = (
            f"SMA44 is not rising: today={display.get('sma44_today')}, "
            f"yesterday={display.get('sma44_yesterday')}, delta={display.get('sma44_delta')}."
        )
    elif not display.get("c1_slope_ready", False) or not display.get("c1_slope_pass", False):
        stage = "c1_slope"
        if not display.get("c1_slope_ready", False):
            reason = (
                f"Not enough valid bars for {SMA_SLOPE_LOOKBACK}-bar slope validation: "
                f"have {valid_bars}, need {SMA_SLOPE_LOOKBACK + 1}."
            )
        elif display.get("c1_slope_error") == "invalid_atr":
            reason = "ATR14 is non-positive or invalid, so ATR-normalised slope cannot be evaluated."
        elif display.get("c1_slope_error") == "invalid_lookback_sma":
            reason = "The SMA44 lookback value is invalid, so pct_slope cannot be evaluated."
        else:
            pct_ok = (
                display.get("pct_slope") is not None
                and display.get("pct_slope_min") <= display.get("pct_slope") <= display.get("pct_slope_max")
            )
            atr_ok = (
                display.get("atr_slope") is not None
                and display.get("atr_slope") > display.get("atr_slope_min")
            )
            c1c_ok = display.get("c1c_slope_consistency_pass", True)

            if not pct_ok:
                reason = (
                    f"Slope validation failed [C1b pct]: pct_slope={display.get('pct_slope')}% "
                    f"(allowed {display.get('pct_slope_min')}% to {display.get('pct_slope_max')}%)."
                )
            elif not atr_ok:
                reason = (
                    f"Slope validation failed [C1b atr]: atr_slope={display.get('atr_slope')} "
                    f"(must be > {display.get('atr_slope_min')})."
                )
            else:
                reason = (
                    f"Slope consistency failed [C1c]: recent-half slope="
                    f"{display.get('slope_second_half_pct')}% "
                    f"(must be >= {display.get('slope_recent_half_min_pct')}%). "
                    f"Older-half slope={display.get('slope_first_half_pct')}%. "
                    f"Stock was rising earlier but has gone flat or reversed recently."
                )
    elif not display.get("c2_pass", False):
        stage = "c2_close_vs_sma"
        c2a_ok = display.get('c2a_low_proximity_pass', False)
        c2b_ok = display.get('c2b_close_above_sma_pass', False)
        failures = []
        if not c2a_ok:
            failures.append(
                f"C2a: low ({display.get('low_today')}) not within "
                f"{display.get('sma44_support_buffer_pct')}% of SMA44 "
                f"({display.get('sma44_today')}); dist={display.get('low_vs_sma44_pct')}%"
            )
        if not c2b_ok:
            failures.append(
                f"C2b: close ({display.get('close')}) below SMA44 ({display.get('sma44_today')})"
            )
        reason = "SMA44 support interaction failed: " + "; ".join(failures) if failures else "C2 failed."

    elif status == "none":
        stage = "c3_macd"
        if display.get("imminent_not_crossed"):
            reason = (
                f"No bullish crossover in the last {MACD_CROSSOVER_LOOKBACK} bars. "
                f"MACD is still pending below Signal: MACD={display.get('macd_cur')}, "
                f"Signal={display.get('signal_cur')}."
            )
        else:
            reason = (
                f"No recent bullish crossover in the last {MACD_CROSSOVER_LOOKBACK} bars."
            )
    else:
        cross_type = payload.get("cross_type", "")
        if cross_type == "confirmed":
            stage = "passed"
            bars_ago = payload.get("crossover_bars_ago", 0)
            age_str = (
                "today" if bars_ago == 0 else
                "yesterday" if bars_ago == 1 else
                f"{bars_ago} bars ago"
            )
            reason = (
                f"All conditions passed (C1, C2, C3). Bullish MACD crossover confirmed {age_str}. "
                f"Stock is TRADE READY."
            )
        elif cross_type == "imminent":
            stage = "passed_imminent"
            hist_rising = display.get("hist_consecutive_rising", "?")
            gap = display.get("imminent_gap_ratio", "?")
            threshold = display.get("imminent_gap_threshold_pct", "?")
            reason = (
                f"C1 and C2 passed. MACD crossover is imminent — histogram rising for "
                f"{hist_rising} consecutive bars, gap={gap}% of |Signal| "
                f"(threshold={threshold}%). Stock is TRADE READY (imminent)."
            )
        else:
            stage = "passed"
            imminent = "yes" if display.get("is_imminent_crossover") else "no"
            reason = (
                f"C1 and C2 passed. No confirmed crossover yet, so the stock enters the "
                f"watchlist as pending. Imminent metadata: {imminent}."
            )

    return _result(tag, stage, reason, display, status=status)


def _fmt(val, decimals: int = 4) -> str:
    if val is None:
        return "-"
    if isinstance(val, bool):
        return "yes" if val else "no"
    try:
        return f"{float(val):.{decimals}f}"
    except (TypeError, ValueError):
        return str(val)


def _print_values(v: dict) -> None:
    print(
        f"   C1a Point  : today={_fmt(v.get('sma44_today'),2)}  "
        f"n_ago({v.get('c1a_lookback','?')}bars)={_fmt(v.get('c1a_sma_n_ago'),2)}  "
        f"{'pass' if v.get('c1a_point_pass') else 'fail'}"
    )
    print(
        f"   C1a Linreg : slope={_fmt(v.get('c1a_linreg_slope'),8)}  "
        f"min={v.get('c1a_linreg_slope_min')}  "
        f"window={v.get('c1a_linreg_window')}bars  "
        f"{'pass' if v.get('c1a_linreg_pass') else 'fail'}"
    )
    print(
        f"   C1a Recover: recovery_slope={_fmt(v.get('c1a_recovery_slope'),8)}  "
        f"linreg_slope={_fmt(v.get('c1a_linreg_slope'),8)}  "
        f"{'pass' if v.get('c1a_recovering_pass') else 'fail'}  "
        f"[C1a overall: {'pass' if v.get('c1_rising_pass') else 'FAIL'}]"
    )
    slope_state = (
        "waiting for history" if not v.get("c1_slope_ready") else
        "pass" if v.get("c1_slope_pass") else
        "fail"
    )
    print(
        f"   C1b Slope : pct_slope={_fmt(v.get('pct_slope'),2)}%  "
        f"atr_slope={_fmt(v.get('atr_slope'),4)}  "
        f"range={_fmt(v.get('pct_slope_min'),2)}%-{_fmt(v.get('pct_slope_max'),2)}%  "
        f"atr_min={_fmt(v.get('atr_slope_min'),4)}  {slope_state}"
    )
    c1c_ok = v.get("c1c_slope_consistency_pass", True)
    fh = v.get("slope_first_half_pct")
    sh = v.get("slope_second_half_pct")
    rh_min = v.get("slope_recent_half_min_pct")
    if fh is not None and sh is not None:
        print(
            f"   C1c Consist: first_half={_fmt(fh,2)}%  "
            f"recent_half={_fmt(sh,2)}%  "
            f"min={_fmt(rh_min,2)}%  "
            f"{'pass' if c1c_ok else 'FAIL'}"
        )
    print(
        f"   C2a Prox  : low={_fmt(v.get('low_today'),2)}  "
        f"sma44={_fmt(v.get('sma44_today') or v.get('sma44'),2)}  "
        f"dist={_fmt(v.get('low_vs_sma44_pct'),2)}%  "
        f"buf={_fmt(v.get('sma44_support_buffer_pct'),2)}%  "
        f"{'pass' if v.get('c2a_low_proximity_pass') else 'FAIL'}"
    )
    print(
        f"   C2b Close : close={_fmt(v.get('close'),2)}  "
        f"sma44={_fmt(v.get('sma44_today') or v.get('sma44'),2)}  "
        f"dist={_fmt(v.get('close_vs_sma_pct'),2)}%  "
        f"{'pass' if v.get('c2b_close_above_sma_pass') else 'FAIL'}"
    )
    found = v.get("crossover_found_bars_ago")
    if found is not None:
        print(f"   C3 MACD   : confirmed crossover at bars_ago={found}")
    else:
        print(
            f"   C3 MACD   : no recent crossover  MACD={_fmt(v.get('macd_cur'),4)}  "
            f"Signal={_fmt(v.get('signal_cur'),4)}"
        )
        print(
            f"   Pending   : hist_rising={v.get('hist_consecutive_rising')} bars  "
            f"gap={_fmt(v.get('imminent_gap_ratio'),2)}% of |Signal|  "
            f"imminent={_fmt(v.get('is_imminent_crossover'))}"
        )
    if v.get("cross_type"):
        print(
            f"   Payload   : cross_type={v.get('cross_type')}  "
            f"ma_type={v.get('ma_type')}  "
            f"interaction={v.get('price_interaction_type')}  "
            f"double_bottom={_fmt(v.get('is_double_bottom'))}  "
            f"weekly_rising={_fmt(v.get('weekly_rising'))}  "
            f"close={_fmt(v.get('close'),2)}  sma44={_fmt(v.get('sma44_today') or v.get('sma44'),2)}"
        )


def print_debug(result: dict, show_values: bool = True, explain: bool = False) -> None:
    sym = result.get("symbol", "?")
    status = result.get("status", "none")
    stage = result.get("stage", "")
    reason = result.get("reason", "")
    icon = _STATUS_ICON.get(status, "?")

    print(f"\n{icon}  {sym:<20}  [{_STAGE_LABEL.get(stage, stage)}]")
    print(f"   {reason}")

    if explain:
        print(explain_result(result))
    elif show_values:
        _print_values(result.get("values", {}))


def explain_result(result: dict) -> str:
    v = result.get("values", {})
    status = result.get("status", "none")
    stage = result.get("stage", "")
    sym = result.get("symbol", "")

    lines = [
        "",
        "  " + "=" * 68,
        f"  {sym} -> {status.upper()} [{_STAGE_LABEL.get(stage, stage)}]",
        "  " + "=" * 68,
        "",
        "  PRE-FLIGHT",
        f"    - raw_bars >= {MIN_BARS}: {v.get('raw_bars', 0)}",
        f"    - valid_bars after dropna: {v.get('valid_bars', 0)}",
        "",
        "  CONDITION 1A - SMA44 Rising (passes if ANY sub-test passes)",
        (
            f"    - overall pass={v.get('c1_rising_pass')}"
        ),
        (
            f"    - C1a-P  (point check):    today={v.get('sma44_today')}  "
            f"{C1A_LOOKBACK}bar_ago={v.get('c1a_sma_n_ago')}  "
            f"pass={v.get('c1a_point_pass')}"
        ),
        (
            f"    - C1a-R  (regression):     slope={v.get('c1a_linreg_slope')}  "
            f"min={C1A_LINREG_SLOPE_MIN}  window={C1A_LINREG_WINDOW}bars  "
            f"pass={v.get('c1a_linreg_pass')}"
        ),
        (
            f"    - C1a-Rec (recovering):    recovery_slope={v.get('c1a_recovery_slope')}  "
            f"linreg_slope={v.get('c1a_linreg_slope')}  "
            f"pass={v.get('c1a_recovering_pass')}"
        ),
        "",
        "  CONDITION 1B/C - Slope Validation",
        (
            f"    - ready={v.get('c1_slope_ready')}  pass={v.get('c1_slope_pass')}  "
            f"pct_slope={v.get('pct_slope')}%  allowed={v.get('pct_slope_min')}% to "
            f"{v.get('pct_slope_max')}%  atr_slope={v.get('atr_slope')}  "
            f"atr_min={v.get('atr_slope_min')}"
        ),
        (
            f"    - consistency (C1c): first_half={v.get('slope_first_half_pct')}%  "
            f"recent_half={v.get('slope_second_half_pct')}%  "
            f"min={v.get('slope_recent_half_min_pct')}%  "
            f"pass={v.get('c1c_slope_consistency_pass')}"
        ),
        "",
        "  CONDITION 2 - SMA44 Support Interaction (C2a + C2b)",
        (
            f"    - C2a low proximity: pass={v.get('c2a_low_proximity_pass')}  "
            f"low={v.get('low_today')}  sma44={v.get('sma44_today') or v.get('sma44')}  "
            f"dist={v.get('low_vs_sma44_pct')}%  buf={v.get('sma44_support_buffer_pct')}%"
        ),
        (
            f"    - C2b close >= SMA44: pass={v.get('c2b_close_above_sma_pass')}  "
            f"close={v.get('close')}  sma44={v.get('sma44_today') or v.get('sma44')}  "
            f"dist={v.get('close_vs_sma_pct')}%"
        ),
        (
            f"    - strategy tags: ma_type={v.get('ma_type')}  "
            f"price_interaction={v.get('price_interaction_type')}  "
            f"double_bottom={v.get('is_double_bottom')}  "
            f"weekly_rising={v.get('weekly_rising')}"
        ),
        "",
        f"  CONDITION 3 - MACD Crossover (lookback={MACD_CROSSOVER_LOOKBACK})",
        (
            f"    - confirmed={v.get('c3_confirmed')}  bars_ago={v.get('crossover_found_bars_ago')}  "
            f"MACD={v.get('macd_cur')}  Signal={v.get('signal_cur')}"
        ),
        "    - pending metadata (informational only): "
        f"hist_rising={v.get('hist_consecutive_rising')}  "
        f"gap={v.get('imminent_gap_ratio')}%  imminent={v.get('is_imminent_crossover')}",
    ]

    if status == "signal" and v.get("cross_type") == "imminent":
        lines.extend(["", "  OUTCOME",
                       "    - Trade ready (imminent): MACD crossover imminent — histogram rising, gap within threshold."])
    elif status == "signal":
        lines.extend(["", "  OUTCOME",
                       "    - Trade ready: confirmed MACD crossover found."])
    elif status == "watchlist":
        lines.extend(["", "  OUTCOME",
                       "    - Watchlist: C1 + C2 passed, crossover still pending."])
    else:
        lines.extend(["", "  OUTCOME", "    - Rejected before final selection."])

    return "\n".join(lines)


def summary_table(results: dict) -> None:
    stage_counts = Counter(r["stage"] for r in results.values())
    status_counts = Counter(r["status"] for r in results.values())

    print("\nDebug Summary")
    print(f"  Total stocks : {len(results)}")
    print(f"  Signals      : {status_counts.get('signal', 0)}")
    print(f"  Watchlist    : {status_counts.get('watchlist', 0)}")
    print(f"  Rejected     : {status_counts.get('none', 0)}")
    print("\n  Breakdown by stage:")
    for stage in _STAGE_ORDER:
        count = stage_counts.get(stage, 0)
        if count:
            bar = "#" * min(count, 40)
            print(f"    {_STAGE_LABEL.get(stage, stage):<44} {count:>4}  {bar}")


def summary_table_detailed(results: dict) -> None:
    """Enhanced summary with rejection reason distribution (for QUIET_MODE)."""
    stage_counts = Counter(r["stage"] for r in results.values())
    status_counts = Counter(r["status"] for r in results.values())
    reason_counts = Counter(r.get("reason", "unknown") for r in results.values() if r["status"] == "none")

    print("\n  📊  RESULTS BY STATUS:")
    print(f"    Signals       : {status_counts.get('signal', 0):>4}")
    print(f"    Watchlist     : {status_counts.get('watchlist', 0):>4}")
    print(f"    Rejected      : {status_counts.get('none', 0):>4}")
    print(f"    Total scanned : {len(results):>4}")

    print("\n  📍  FAILURE BREAKDOWN:")
    for stage in _STAGE_ORDER:
        count = stage_counts.get(stage, 0)
        if count:
            label = _STAGE_LABEL.get(stage, stage)
            bar = "█" * min(count // 5, 30)
            pct = (count / len(results) * 100) if results else 0
            print(f"    {label:<45} {count:>4}  ({pct:>5.1f}%)  {bar}")

    if reason_counts and status_counts.get('none', 0) > 0:
        print("\n  ❌  TOP REJECTION REASONS:")
        for reason, count in reason_counts.most_common(8):
            bar = "▓" * min(count // 2, 30)
            print(f"    {reason[:55]:<55} {count:>3}  {bar}")


HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Scanner Debug Report</title>
<style>
  :root {
    --bg: #0a0a0a;
    --bg-secondary: #1a1a1a;
    --bg-tertiary: #242424;
    --bg-hover: #2a2a2a;
    --text: #f5f5f5;
    --text-secondary: #b0b0b0;
    --text-tertiary: #808080;
    --green: #10b981;
    --green-light: #34d399;
    --green-dark: #1a3a2f;
    --yellow: #f59e0b;
    --yellow-light: #fbbf24;
    --yellow-dark: #3a2f1a;
    --red: #ef5350;
    --red-light: #ff7675;
    --red-dark: #3a1f1f;
    --blue: #3b82f6;
    --blue-light: #60a5fa;
    --accent: #00d9ff;
    --border: #333333;
    --shadow: rgba(0, 0, 0, 0.5);
  }

  * {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
  }

  html {
    scroll-behavior: smooth;
  }

  body {
    background: #0a0a0a;
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif;
    padding: 40px 24px;
    min-height: 100vh;
    line-height: 1.6;
  }

  .container {
    max-width: 1200px;
    margin: 0 auto;
  }

  header {
    margin-bottom: 44px;
    animation: slideDown 0.6s ease-out;
  }

  @keyframes slideDown {
    from { opacity: 0; transform: translateY(-24px); }
    to { opacity: 1; transform: translateY(0); }
  }

  h1 {
    font-size: 2.5rem;
    font-weight: 800;
    margin-bottom: 12px;
    background: linear-gradient(135deg, var(--blue-light) 0%, var(--accent) 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    letter-spacing: -0.8px;
  }

  .meta {
    color: var(--text-tertiary);
    font-size: 0.9rem;
    margin-bottom: 28px;
    display: flex;
    flex-wrap: wrap;
    gap: 18px;
  }

  .meta-item {
    display: flex;
    align-items: center;
    gap: 7px;
    padding: 6px 0;
  }

  .summary {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 14px;
    margin-bottom: 32px;
  }

  .pill {
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 18px 20px;
    display: flex;
    flex-direction: column;
    gap: 10px;
    transition: all 0.3s ease;
    box-shadow: 0 4px 16px var(--shadow);
  }

  .pill:hover {
    border-color: var(--blue-light);
    transform: translateY(-3px);
    box-shadow: 0 8px 24px var(--shadow);
    background: var(--bg-hover);
  }

  .pill-label {
    color: var(--text-tertiary);
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    font-weight: 600;
  }

  .pill-value {
    font-size: 2rem;
    font-weight: 800;
    background: linear-gradient(135deg, var(--blue-light) 0%, var(--accent) 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
  }

  .filters {
    display: flex;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 32px;
    padding-bottom: 22px;
    border-bottom: 1px solid var(--border);
  }

  .filter-btn {
    background: var(--bg-secondary);
    color: var(--text-secondary);
    border: 1.5px solid var(--border);
    border-radius: 22px;
    padding: 11px 20px;
    font-size: 0.9rem;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.3s ease;
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .filter-btn:hover {
    border-color: var(--text-secondary);
    color: var(--text);
    background: var(--bg-tertiary);
  }

  .filter-btn.active {
    background: linear-gradient(135deg, var(--blue) 0%, var(--blue-light) 100%);
    border-color: var(--blue-light);
    color: #000;
    font-weight: 700;
    box-shadow: 0 6px 20px rgba(59, 130, 246, 0.3);
  }

  .cards {
    display: flex;
    flex-direction: column;
    gap: 18px;
    animation: fadeIn 0.6s ease-out;
  }

  @keyframes fadeIn {
    from { opacity: 0; }
    to { opacity: 1; }
  }

  .card {
    border: 1px solid var(--border);
    border-radius: 16px;
    background: var(--bg-secondary);
    overflow: hidden;
    transition: all 0.3s ease;
    box-shadow: 0 4px 16px var(--shadow);
  }

  .card:hover {
    border-color: var(--text-secondary);
    box-shadow: 0 10px 32px var(--shadow);
    transform: translateY(-3px);
    background: var(--bg-hover);
  }

  .card.signal {
    border-left: 4px solid var(--green-light);
  }

  .card.watchlist {
    border-left: 4px solid var(--yellow-light);
  }

  .card.none {
    border-left: 4px solid var(--red-light);
  }

  .card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 20px 22px;
    cursor: pointer;
    background: var(--bg-secondary);
    transition: all 0.3s ease;
    user-select: none;
    border-bottom: 1px solid transparent;
  }

  .card-header:hover {
    background: var(--bg-tertiary);
    border-bottom-color: var(--border);
  }

  .card-header-content {
    display: flex;
    align-items: center;
    gap: 14px;
    flex-wrap: wrap;
    flex: 1;
  }

  .card-header strong {
    font-size: 1.15rem;
    font-weight: 700;
    color: var(--text);
    min-width: 140px;
  }

  .toggle-icon {
    color: var(--text-secondary);
    font-weight: 700;
    transition: transform 0.3s ease;
    font-size: 1.2rem;
  }

  .card.open .toggle-icon {
    transform: rotate(180deg);
  }

  .badge {
    border-radius: 20px;
    padding: 7px 14px;
    font-size: 0.75rem;
    font-weight: 700;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    white-space: nowrap;
  }

  .badge.signal {
    background: var(--green-dark);
    color: var(--green-light);
    border: 1px solid rgba(52, 211, 153, 0.4);
  }

  .badge.signal::before {
    content: '✓';
    font-weight: 900;
  }

  .badge.watchlist {
    background: var(--yellow-dark);
    color: var(--yellow-light);
    border: 1px solid rgba(251, 191, 36, 0.4);
  }

  .badge.watchlist::before {
    content: '◐';
  }

  .badge.none {
    background: var(--red-dark);
    color: var(--red-light);
    border: 1px solid rgba(255, 118, 117, 0.4);
  }

  .badge.none::before {
    content: '✕';
    font-weight: 900;
  }

  .stage {
    color: var(--text-tertiary);
    font-size: 0.7rem;
    border: 1px solid var(--border);
    padding: 6px 12px;
    border-radius: 20px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.6px;
    background: var(--bg-tertiary);
    transition: all 0.2s ease;
  }

  .stage:hover {
    border-color: var(--text-secondary);
    background: var(--bg-hover);
  }

  .card-body {
    display: none;
    padding: 24px;
    border-top: 1px solid var(--border);
    background: var(--bg-secondary);
    animation: expandIn 0.3s ease-out;
  }

  @keyframes expandIn {
    from { opacity: 0; max-height: 0; }
    to { opacity: 1; max-height: 3000px; }
  }

  .card.open .card-body {
    display: block;
  }

  .reason {
    color: var(--text-secondary);
    font-size: 0.95rem;
    margin-bottom: 18px;
    padding: 14px 16px;
    background: var(--bg-tertiary);
    border-radius: 12px;
    border-left: 4px solid var(--blue-light);
    font-style: italic;
    font-weight: 500;
  }

  .metrics {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 12px;
    margin-bottom: 24px;
  }

  .metric {
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    padding: 14px;
    border-radius: 12px;
    font-size: 0.85rem;
    transition: all 0.3s ease;
    display: flex;
    flex-direction: column;
    gap: 5px;
  }

  .metric:hover {
    border-color: var(--blue-light);
    background: var(--bg-hover);
    box-shadow: 0 4px 12px var(--shadow);
  }

  .metric strong {
    color: var(--text-secondary);
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.6px;
    font-weight: 700;
  }

  .section {
    margin-top: 24px;
  }

  .section-title {
    color: var(--text-secondary);
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 1.2px;
    margin-bottom: 14px;
    font-weight: 800;
    padding-bottom: 10px;
    border-bottom: 2px solid var(--blue-light);
  }

  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.85rem;
    margin-bottom: 18px;
  }

  table th {
    background: var(--bg-tertiary);
    color: var(--text-secondary);
    padding: 14px 12px;
    text-align: left;
    font-weight: 700;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.6px;
    border-bottom: 2px solid var(--blue-light);
  }

  table td {
    padding: 12px;
    border-bottom: 1px solid var(--border);
    vertical-align: middle;
    color: var(--text);
  }

  table tr:hover {
    background: var(--bg-tertiary);
  }

  table tr:last-child td {
    border-bottom: none;
  }

  table td:first-child {
    font-weight: 700;
    color: var(--blue-light);
  }

  table td:nth-child(3) {
    font-weight: 700;
    color: var(--green-light);
  }

  .verdict {
    border-radius: 14px;
    padding: 16px 18px;
    margin-top: 18px;
    font-size: 0.95rem;
    font-weight: 600;
    border-left: 4px solid;
    background-size: 100% 100%;
  }

  .verdict strong {
    font-weight: 800;
    display: block;
    margin-bottom: 6px;
    font-size: 1rem;
  }

  .verdict.signal {
    background: var(--green-dark);
    border-color: var(--green-light);
    color: var(--green-light);
  }

  .verdict.watchlist {
    background: var(--yellow-dark);
    border-color: var(--yellow-light);
    color: var(--yellow-light);
  }

  .verdict.none {
    background: var(--red-dark);
    border-color: var(--red-light);
    color: var(--red-light);
  }

  @media (max-width: 768px) {
    body {
      padding: 24px 16px;
    }

    h1 {
      font-size: 2rem;
    }

    .summary {
      grid-template-columns: 1fr 1fr;
    }

    .card-header {
      flex-direction: column;
      align-items: flex-start;
      gap: 12px;
    }

    .metrics {
      grid-template-columns: 1fr;
    }

    .filter-btn {
      flex: 1;
      justify-content: center;
    }
  }
</style>
</head>
<body>
<div class="container">
<header>
  <h1>📊 Scanner Debug Report</h1>
</header>
<script>
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.card-header').forEach(el => {
    el.addEventListener('click', () => el.closest('.card').classList.toggle('open'));
  });
  document.querySelectorAll('.filter-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.filter-btn').forEach(x => x.classList.remove('active'));
      btn.classList.add('active');
      const filter = btn.dataset.filter;
      document.querySelectorAll('.card').forEach(card => {
        card.style.display = (filter === 'all' || card.dataset.status === filter) ? '' : 'none';
      });
    });
  });
});
</script>
"""

FOOT = """</div>
</body>
</html>"""


def save_html_report(
    results: dict,
    filepath: str = "debug_report.html",
    mode: str = "live",
    requested_date: str | None = None,
    actual_date: str | None = None,
) -> None:
    def _pct(v):
        if v is None:
            return "-"
        try:
            return f"{float(v):.2f}%"
        except (TypeError, ValueError):
            return str(v)

    def _metric(label: str, value: str) -> str:
        return f'<span class="metric"><strong>{label}</strong> {value}</span>'

    def _status_badge(status: str) -> str:
        text = {
            "signal": "Trade Ready",
            "watchlist": "Watchlist",
            "none": "Rejected",
        }.get(status, status)
        return f'<span class="badge {status}">{text}</span>'

    def _card(res: dict) -> str:
        sym = res.get("symbol", "?")
        status = res.get("status", "none")
        stage = res.get("stage", "")
        reason = res.get("reason", "")
        v = res.get("values", {})

        metrics = "".join(
            [
                _metric("Close", _fmt(v.get("close"), 2)),
                _metric("SMA44", _fmt(v.get("sma44_today") or v.get("sma44"), 2)),
                _metric("Pct slope", _pct(v.get("pct_slope"))),
                _metric("ATR slope", _fmt(v.get("atr_slope"), 4)),
                _metric("MA type", str(v.get("ma_type") or "-")),
                _metric("Weekly rising", _fmt(v.get("weekly_rising"))),
                _metric("MACD", _fmt(v.get("macd_cur"), 4)),
                _metric("Signal", _fmt(v.get("signal_cur"), 4)),
            ]
        )

        rows = [
            (
                "C1a-P",
                f"SMA44 point check (today > {C1A_LOOKBACK}-bar ago)",
                str(v.get("c1a_point_pass")),
                f"today={_fmt(v.get('sma44_today'),2)} sma_{C1A_LOOKBACK}ago={_fmt(v.get('c1a_sma_n_ago'),2)}",
            ),
            (
                "C1a-R",
                f"SMA44 regression ({C1A_LINREG_WINDOW}-bar OLS slope ≥ {C1A_LINREG_SLOPE_MIN})",
                str(v.get("c1a_linreg_pass")),
                f"slope={_fmt(v.get('c1a_linreg_slope'),8)} min={v.get('c1a_linreg_slope_min')}",
            ),
            (
                "C1a-Rec",
                f"SMA44 slope recovering ({v.get('c1a_recovery_window', 5)}-bar slope > {C1A_LINREG_WINDOW}-bar slope)",
                str(v.get("c1a_recovering_pass")),
                f"recovery_slope={_fmt(v.get('c1a_recovery_slope'),8)} linreg_slope={_fmt(v.get('c1a_linreg_slope'),8)} [C1a passes if any True]",
            ),
            (
                "C1b",
                "Slope validation",
                str(v.get("c1_slope_pass")) if v.get("c1_slope_ready") else "waiting",
                f"pct_slope={_pct(v.get('pct_slope'))} allowed={_pct(v.get('pct_slope_min'))}-{_pct(v.get('pct_slope_max'))} atr_slope={_fmt(v.get('atr_slope'),4)} min={_fmt(v.get('atr_slope_min'),4)}",
            ),
            (
                "C1c",
                f"Slope consistency (recent {SMA_SLOPE_LOOKBACK // 2} bars ≥ {_fmt(v.get('slope_recent_half_min_pct'), 1)}%)",
                str(v.get("c1c_slope_consistency_pass", True)) if v.get("c1_slope_ready") else "waiting",
                (
                    f"first_half={_fmt(v.get('slope_first_half_pct'), 2)}%  "
                    f"recent_half={_fmt(v.get('slope_second_half_pct'), 2)}%  "
                    f"threshold={_fmt(v.get('slope_recent_half_min_pct'), 1)}%"
                    if v.get("slope_second_half_pct") is not None
                    else "insufficient history"
                ),
            ),
            (
                "C2a",
                f"Low proximity to SMA44 (buf={_fmt(v.get('sma44_support_buffer_pct', SMA44_SUPPORT_BUFFER_PCT * 100), 1)}%)",
                str(v.get("c2a_low_proximity_pass")),
                f"low={_fmt(v.get('low_today'),2)} sma44={_fmt(v.get('sma44_today') or v.get('sma44'),2)} dist={_pct(v.get('low_vs_sma44_pct'))}",
            ),
            (
                "C2b",
                "Close >= SMA44 (support held)",
                str(v.get("c2b_close_above_sma_pass")),
                f"close={_fmt(v.get('close'),2)} sma44={_fmt(v.get('sma44_today') or v.get('sma44'),2)} dist={_pct(v.get('close_vs_sma_pct'))}",
            ),
            (
                "Setup",
                "SMA44 strategy metadata",
                str(v.get("ma_type") or "-"),
                (
                    f"interaction={v.get('price_interaction_type') or '-'}  "
                    f"double_bottom={_fmt(v.get('is_double_bottom'))}  "
                    f"weekly_rising={_fmt(v.get('weekly_rising'))}"
                ),
            ),
            (
                "C3",
                f"Recent bullish crossover (last {v.get('c3_lookback', MACD_CROSSOVER_LOOKBACK)} bars)",
                str(v.get("c3_confirmed")),
                (
                    f"bars_ago={v.get('crossover_found_bars_ago')}"
                    if v.get("c3_confirmed")
                    else f"MACD={_fmt(v.get('macd_cur'),4)} Signal={_fmt(v.get('signal_cur'),4)}"
                ),
            ),
            (
                "Info",
                "Pending metadata",
                str(v.get("is_imminent_crossover")),
                f"hist_rising={v.get('hist_consecutive_rising')} gap={_pct(v.get('imminent_gap_ratio'))} threshold={_pct(v.get('imminent_gap_threshold_pct'))}",
            ),
        ]

        table_rows = "".join(
            f"<tr><td>{c}</td><td>{name}</td><td>{state}</td><td>{detail}</td></tr>"
            for c, name, state, detail in rows
        )

        if status == "signal" and v.get("cross_type") == "imminent":
            hist_rising = v.get("hist_consecutive_rising", "?")
            gap = v.get("imminent_gap_ratio", "?")
            threshold = v.get("imminent_gap_threshold_pct", "?")
            verdict = (
                f'<div class="verdict signal"><strong>Trade Ready — Imminent Crossover</strong><br>'
                f'MACD histogram rising for {hist_rising} consecutive bars. '
                f'Gap to Signal: {gap}% (threshold: {threshold}%). '
                f'Crossover expected imminently.</div>'
            )
        elif status == "signal":
            verdict = (
                f'<div class="verdict signal"><strong>Trade Ready</strong><br>'
                f'Confirmed bullish MACD crossover.</div>'
            )
        elif status == "watchlist":
            verdict = (
                '<div class="verdict watchlist"><strong>Watchlist Pending</strong><br>'
                'C1 and C2 passed, but there is no confirmed recent crossover yet. '
                'The stock is still added to the watchlist.</div>'
            )
        else:
            verdict = (
                f'<div class="verdict none"><strong>Rejected</strong><br>{reason}</div>'
            )

        return f"""
<div class="card {status}" data-status="{status}">
  <div class="card-header">
    <div class="card-header-content">
      <strong>{sym}</strong>
      {_status_badge(status)}
      <span class="stage">{_STAGE_LABEL.get(stage, stage)}</span>
    </div>
    <span class="toggle-icon">▼</span>
  </div>
  <div class="card-body">
    <div class="reason">{reason}</div>
    <div class="metrics">{metrics}</div>
    <div class="section">
      <div class="section-title">Condition Breakdown</div>
      <table>
        <tr><th>#</th><th>Check</th><th>State</th><th>Values</th></tr>
        {table_rows}
      </table>
    </div>
    {verdict}
  </div>
</div>"""

    status_counts = Counter(r.get("status", "none") for r in results.values())
    ts = _dt.datetime.now().strftime("%d %b %Y %H:%M:%S")

    meta_parts = [f"Generated {ts}", f"{len(results)} stocks"]
    if mode == "historical" and actual_date:
        meta_parts.append(f"Analysis date: {actual_date}")
        if requested_date and requested_date != actual_date:
            meta_parts.append(f"Requested {requested_date}, rolled back to {actual_date}")

    meta_html = '<div class="meta">'
    for part in meta_parts:
        meta_html += f'<span class="meta-item">{part}</span>'
    meta_html += '</div>'

    summary_html = (
        '<div class="summary">'
        f'<div class="pill"><div class="pill-label">Scanned</div><div class="pill-value">{len(results)}</div></div>'
        f'<div class="pill"><div class="pill-label">Trade Ready</div><div class="pill-value">{status_counts.get("signal", 0)}</div></div>'
        f'<div class="pill"><div class="pill-label">Watchlist</div><div class="pill-value">{status_counts.get("watchlist", 0)}</div></div>'
        f'<div class="pill"><div class="pill-label">Rejected</div><div class="pill-value">{status_counts.get("none", 0)}</div></div>'
        '</div>'
    )
    filters_html = (
        '<div class="filters">'
        '<button class="filter-btn active" data-filter="all">All Results</button>'
        '<button class="filter-btn" data-filter="signal">Trade Ready</button>'
        '<button class="filter-btn" data-filter="watchlist">Watchlist</button>'
        '<button class="filter-btn" data-filter="none">Rejected</button>'
        '</div>'
    )

    order = {"signal": 0, "watchlist": 1, "none": 2}
    cards_html = "\n".join(
        _card(res)
        for res in sorted(
            results.values(),
            key=lambda r: (order.get(r.get("status", "none"), 3), r.get("symbol", "")),
        )
    )

    html = (
        HEAD
        + meta_html
        + summary_html
        + filters_html
        + f'<div class="cards">{cards_html}</div>'
        + FOOT
    )

    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as handle:
        handle.write(html)
    print(f"\nHTML report saved -> {filepath}  ({len(results)} stocks)")


def save_debug_csv(results: dict, filepath: str = "debug_out.csv") -> None:
    rows = []
    for sym, res in results.items():
        row = {
            "symbol": res.get("symbol", sym),
            "status": res.get("status"),
            "stage": res.get("stage"),
            "reason": res.get("reason"),
        }
        for key, value in res.get("values", {}).items():
            if isinstance(value, (list, dict)):
                continue
            row[key] = value
        rows.append(row)

    if not rows:
        print("No results to save.")
        return

    all_keys = list(dict.fromkeys(key for row in rows for key in row))
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=all_keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nDebug CSV saved -> {filepath}  ({len(rows)} stocks)")


def save_debug_json(results: dict, filepath: str = "debug_out.json") -> None:
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=2, default=str)
    print(f"\nDebug JSON saved -> {filepath}  ({len(results)} stocks)")

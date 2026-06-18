"""
scanner/conditions.py
Authoritative condition logic for the simplified scanner.

Condition definitions
---------------------
C1  SMA44 rising + slope validation
    1. SMA44[today] > SMA44[yesterday]
    2. SMA44 growth over the last SMA_SLOPE_LOOKBACK valid bars is between
       PCT_SLOPE_MIN and PCT_SLOPE_MAX
    3. The same growth, normalised by ATR14, is > ATR_SLOPE_MIN

C2  SMA44 support interaction (two sub-conditions on today's bar)
    C2a: abs(Low - SMA44) / SMA44 <= SMA44_SUPPORT_BUFFER_PCT  (low touches SMA44)
    C2b: Close >= SMA44  (support held; price reclaimed the average)

    NOTE: C2c (Close > Open, bullish body / doji filter) has been removed.
    Bars that touch and close at or above SMA44 are accepted regardless of
    candle body direction.

C3  Bullish MACD crossover within MACD_CROSSOVER_LOOKBACK trading days
    A crossover event at bar t is:
        MACD[t-1] <= Signal[t-1]  and  MACD[t] > Signal[t]

    Imminent-signal path (promoted from watchlist):
    When no confirmed crossover is found but all three imminent conditions
    hold simultaneously, the stock is promoted to "signal" with
    cross_type="imminent":
        1. MACD < Signal  (not yet crossed)
        2. Histogram has risen consecutively for >= IMMINENT_HIST_MIN bars
        3. Gap between Signal and MACD is <= IMMINENT_GAP_THRESHOLD of |Signal|

Watchlist path
--------------
If C1 and C2 pass but no confirmed crossover is found AND the imminent
criteria are not met, the stock enters the watchlist (MACD < Signal only).
"""

from __future__ import annotations

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

_NONE: dict = {"status": "none", "data": {}}

# Slope consistency: the second (recent) half of the lookback window must
# not be declining beyond this tolerance.  A value of -0.005 allows a
# trivial pullback (−0.5% over 22 bars) without flagging a genuine uptrend,
# while still rejecting stocks that have been flat or falling recently.
_RECENT_HALF_SLOPE_MIN: float = -0.005


def _build_payload(
    symbol: str,
    bar: pd.Series,
    cross_type: str,
    weekly_rising: bool | None = None,
    crossover_bars_ago: int | None = None,
    extra_metrics: dict | None = None,
) -> dict:
    """Construct the output payload from the evaluated bar."""
    close = float(bar["Close"])
    open_ = float(bar["Open"])
    sma44 = float(bar["SMA44"])
    macd = float(bar["MACD"])
    sig = float(bar["Signal"])
    hist = macd - sig

    date_str = (
        bar.name.strftime("%d %b %Y")
        if hasattr(bar.name, "strftime")
        else str(bar.name)
    )

    payload: dict = {
        "symbol": symbol.replace("NSE:", "").replace("-EQ", ""),
        "fyers_symbol": symbol,
        "date": date_str,
        "close": round(close, 2),
        "sma44": round(sma44, 2),
        "sma_dist_pct": round((close - sma44) / sma44 * 100, 2) if sma44 > 0 else 0.0,
        "macd": round(macd, 4),
        "macd_signal": round(sig, 4),
        "macd_histogram": round(hist, 4),
        "cross_type": cross_type,
        "weekly_rising": weekly_rising,
        "change_pct": round((close - open_) / open_ * 100, 2) if open_ > 0 else 0.0,
    }
    if crossover_bars_ago is not None:
        payload["crossover_bars_ago"] = crossover_bars_ago
    if extra_metrics:
        payload.update(extra_metrics)

    return payload


def _count_consecutive_rising_histogram(hist_vals: np.ndarray) -> int:
    """Count consecutive rising histogram bars ending at the latest bar."""
    if len(hist_vals) < 2:
        return 0

    rises = 0
    for i in range(1, len(hist_vals)):
        idx = -i
        idx_prev = -(i + 1)
        if abs(idx_prev) > len(hist_vals):
            break
        if hist_vals[idx] > hist_vals[idx_prev]:
            rises += 1
        else:
            break
    return rises


def _consecutive_rising_histogram(hist_vals: np.ndarray, min_bars: int) -> bool:
    """True when the histogram has risen for at least `min_bars` bars."""
    return _count_consecutive_rising_histogram(hist_vals) >= min_bars


def _slope_consistency_pass(
    sma_values: np.ndarray,
    lookback: int,
) -> tuple[bool, float, float]:
    """
    Check that the recent half of the slope window is not declining.

    Splits the last `lookback` bars of SMA44 into two equal halves.
    The first half (older) is used only for context; the test is on
    the second half (recent): (sma[-1] / sma[-half]) - 1 >= _RECENT_HALF_SLOPE_MIN.

    Returns
    -------
    (passed, first_half_slope, second_half_slope)
      - passed            : True if the recent half is not declining beyond threshold
      - first_half_slope  : pct growth of the older half (informational)
      - second_half_slope : pct growth of the recent half (the gating value)
    """
    half = lookback // 2  # e.g. 44 // 2 = 22

    # We need at least lookback + 1 bars in the window so that
    # iloc[-1 - lookback] and iloc[-1 - half] are both valid.
    if len(sma_values) < lookback + 1:
        return True, 0.0, 0.0  # not enough history — do not gate

    sma_recent     = sma_values[-1]          # today
    sma_half_ago   = sma_values[-(half + 1)] # midpoint bar
    sma_full_ago   = sma_values[-(lookback + 1)]  # start of window

    if sma_half_ago <= 0 or sma_full_ago <= 0:
        return True, 0.0, 0.0  # guard against bad data

    first_half_slope  = (sma_half_ago / sma_full_ago) - 1
    second_half_slope = (sma_recent   / sma_half_ago) - 1

    passed = bool(second_half_slope >= _RECENT_HALF_SLOPE_MIN)
    return passed, first_half_slope, second_half_slope


def _sma_linreg_slope(sma_values: np.ndarray, window: int) -> float:
    """
    Return the OLS linear regression slope of the last `window` SMA44 values,
    normalised by the window mean so the result is scale-independent.

    A positive return value means the SMA44 is trending upward over the window.
    A value of 0.001 means the trend rises ~0.1% of the SMA's own level per bar.

    Normalisation ensures the threshold C1A_LINREG_SLOPE_MIN means the same
    thing for a ₹100 stock and a ₹1000 stock.

    Returns 0.0 if there is insufficient data or the mean is zero.
    """
    if len(sma_values) < window:
        return 0.0
    y = sma_values[-window:].astype(float)
    x = np.arange(window, dtype=float)
    x_mean = x.mean()
    y_mean = y.mean()
    if y_mean <= 0:
        return 0.0
    denominator = np.sum((x - x_mean) ** 2)
    if denominator == 0:
        return 0.0
    slope = np.sum((x - x_mean) * (y - y_mean)) / denominator
    return slope / y_mean


def _pending_metadata(macd_arr: np.ndarray, signal_arr: np.ndarray) -> dict:
    """Return informational MACD-pending metadata for watchlist/debug surfaces."""
    hist_arr = macd_arr - signal_arr
    macd_cur = float(macd_arr[-1])
    signal_cur = float(signal_arr[-1])
    imminent_not_crossed = bool(macd_cur < signal_cur)

    hist_consecutive_rising = _count_consecutive_rising_histogram(hist_arr)
    imminent_hist_ok = bool(hist_consecutive_rising >= IMMINENT_HIST_MIN)

    denom = abs(signal_cur) if abs(signal_cur) > 1e-9 else 1e-9
    gap = (signal_cur - macd_cur) if imminent_not_crossed else 0.0
    gap_ratio = (gap / denom) if imminent_not_crossed else 0.0
    imminent_gap_ok = bool(gap_ratio <= IMMINENT_GAP_THRESHOLD)

    return {
        "hist_consecutive_rising": hist_consecutive_rising,
        "imminent_not_crossed": imminent_not_crossed,
        "imminent_hist_ok": imminent_hist_ok,
        "imminent_gap_ok": imminent_gap_ok,
        "imminent_gap_ratio": round(gap_ratio * 100, 2),
        "imminent_gap_threshold_pct": round(IMMINENT_GAP_THRESHOLD * 100, 2),
        "imminent_hist_min": IMMINENT_HIST_MIN,
        "is_imminent_crossover": bool(
            imminent_not_crossed and imminent_hist_ok and imminent_gap_ok
        ),
    }


def evaluate(symbol: str, df: pd.DataFrame, weekly_rising: bool | None = None) -> dict:
    """Evaluate one symbol against the scanner conditions."""
    if len(df) < MIN_BARS:
        return _NONE

    df_ind = compute_indicators(df.copy())
    df_clean = df_ind.dropna(subset=["SMA44", "MACD", "Signal", "ATR14"]).copy()

    if len(df_clean) < 2:
        return _NONE

    cur = df_clean.iloc[-1]
    prev = df_clean.iloc[-2]

    # ── C1a: SMA44 rising — point check + regression + slope-recovery ────────
    #
    # Three independent sub-tests; the stock passes C1a if ANY ONE passes
    # (OR logic).  This prevents healthy pullback-to-support setups (exactly
    # what C2 is designed to catch) from being rejected by an overly tight
    # short-window filter.
    #
    # Sub-test 1 — Point check: SMA44[today] > SMA44[N bars ago]
    #   Uses C1A_LOOKBACK (default 10 bars / 2 weeks) so that a one-week dip
    #   does not cause a false rejection.
    #
    # Sub-test 2 — Regression: OLS slope of last C1A_LINREG_WINDOW SMA44 bars
    #   >= C1A_LINREG_SLOPE_MIN (-0.00015).  Slope is normalised by the SMA's
    #   own level so the threshold is scale-free across all price levels.
    #   The loosened floor (-0.00015 vs old -0.00005) accommodates normal
    #   pullback depth without accepting genuinely declining averages.
    #
    # Sub-test 3 — Slope recovering: the recent-half slope > full-window slope.
    #   Even when the SMA is still net-negative, if the tilt is improving
    #   (recent 5 bars sloping less negatively than the full 15-bar window) the
    #   pullback is bottoming — a bullish condition that old logic would reject.
    #   This sub-test costs nothing in false positives because C1b and C1c
    #   still gate on 44-bar growth bounds and recent-22-bar consistency.

    sma_series = df_clean["SMA44"].values

    # Guard: need enough bars for the point lookback
    if len(df_clean) < C1A_LOOKBACK + 1:
        return _NONE

    sma_today   = float(sma_series[-1])
    sma_n_ago   = float(sma_series[-1 - C1A_LOOKBACK])

    # Sub-test 1: point check (2-week window)
    c1a_point   = bool(sma_today > sma_n_ago)

    # Sub-test 2: full-window regression slope vs floor
    c1a_linreg_slope = _sma_linreg_slope(sma_series, C1A_LINREG_WINDOW)
    c1a_linreg  = bool(c1a_linreg_slope >= C1A_LINREG_SLOPE_MIN)

    # Sub-test 3: slope recovering — recent 5-bar slope is less negative (or
    # more positive) than the full C1A_LINREG_WINDOW slope, meaning the trend
    # is turning upward even if still net-negative.
    _RECOVERY_WINDOW = 5   # half of the 10-bar sub-window; hardcoded, no need to expose
    c1a_recovery_slope = _sma_linreg_slope(sma_series, _RECOVERY_WINDOW)
    c1a_recovering     = bool(c1a_recovery_slope > c1a_linreg_slope)   # trend is improving

    c1_rising = c1a_point or c1a_linreg or c1a_recovering
    if not c1_rising:
        return _NONE

    # Store for payload
    c1a_metrics = {
        "c1a_sma_today"          : round(sma_today, 4),
        "c1a_sma_n_ago"          : round(sma_n_ago, 4),
        "c1a_lookback"           : C1A_LOOKBACK,
        "c1a_point_pass"         : c1a_point,
        "c1a_linreg_slope"       : round(c1a_linreg_slope, 8),
        "c1a_linreg_slope_min"   : C1A_LINREG_SLOPE_MIN,
        "c1a_linreg_window"      : C1A_LINREG_WINDOW,
        "c1a_linreg_pass"        : c1a_linreg,
        # Sub-test 3 fields
        "c1a_recovery_slope"     : round(c1a_recovery_slope, 8),
        "c1a_recovering_pass"    : c1a_recovering,
        "c1a_recovery_window"    : _RECOVERY_WINDOW,
    }

    # ── C1b: SMA44 slope validation ───────────────────────────────────────────
    if len(df_clean) < SMA_SLOPE_LOOKBACK + 1:
        return _NONE

    sma_now = float(df_clean["SMA44"].iloc[-1])
    sma_44_ago = float(df_clean["SMA44"].iloc[-1 - SMA_SLOPE_LOOKBACK])
    atr = float(cur["ATR14"])

    if sma_44_ago <= 0 or not np.isfinite(atr) or atr <= 0:
        return _NONE

    pct_slope_raw = (sma_now / sma_44_ago) - 1
    atr_slope = (sma_now - sma_44_ago) / atr

    if not (PCT_SLOPE_MIN <= pct_slope_raw <= PCT_SLOPE_MAX):
        return _NONE
    if atr_slope <= ATR_SLOPE_MIN: # changed this, from <= to <
        return _NONE

    # ── C1c: Slope consistency — recent half of the window must not be declining ──
    sma_series = df_clean["SMA44"].values
    c1c_pass, first_half_slope, second_half_slope = _slope_consistency_pass(
        sma_series, SMA_SLOPE_LOOKBACK
    )
    if not c1c_pass:
        return _NONE

    slope_metrics = {
        "pct_slope": round(pct_slope_raw * 100, 2),
        "pct_slope_min": round(PCT_SLOPE_MIN * 100, 2),
        "pct_slope_max": round(PCT_SLOPE_MAX * 100, 2),
        "atr_slope": round(atr_slope, 4),
        "atr_slope_min": round(ATR_SLOPE_MIN, 4),
        # Slope consistency sub-window values (surfaced in payload for debug)
        "slope_first_half_pct": round(first_half_slope * 100, 2),
        "slope_second_half_pct": round(second_half_slope * 100, 2),
        "slope_recent_half_min_pct": round(_RECENT_HALF_SLOPE_MIN * 100, 2),
        "c1c_slope_consistency_pass": c1c_pass,
        **c1a_metrics,   # all C1a sub-test values
    }

    # ── C2: SMA44 Support Interaction ────────────────────────────────────────
    # Two sub-conditions must both pass on today's bar.
    close_today = float(cur["Close"])
    low_today   = float(cur["Low"])

    # C2a: Low is within SMA44_SUPPORT_BUFFER_PCT of SMA44 (both directions)
    #      abs(low - sma44) / sma44 <= buffer  ->  candle genuinely touched SMA44
    c2a_pass = bool((abs(low_today - sma_now) / sma_now) <= SMA44_SUPPORT_BUFFER_PCT) if sma_now > 0 else False

    # C2b: Close >= SMA44  ->  support held; price reclaimed the average
    c2b_close_pass = bool(close_today >= sma_now)

    # NOTE: C2c (Close > Open, bullish body / doji filter) has been removed.
    # All bars that touch SMA44 and close at or above it are accepted,
    # regardless of whether the candle body is bullish, doji, or otherwise.

    if not (c2a_pass and c2b_close_pass):
        return _NONE

    # Pack C2 metrics for payload so debug / frontend can surface them
    c2_metrics = {
        "c2a_low_proximity_pass"  : c2a_pass,
        "c2b_close_above_sma_pass": c2b_close_pass,
        "low_today"               : round(low_today, 2),
        "low_vs_sma44_pct"        : round((low_today - sma_now) / sma_now * 100, 2) if sma_now > 0 else 0.0,
        "sma44_support_buffer_pct": round(SMA44_SUPPORT_BUFFER_PCT * 100, 2),
    }

    # ── MA Type Classification: Determine type based on C1a sub-tests ──────────
    # Type 1: point check passes AND pct_slope >= 0 (cleanly rising MA)
    # Type 2: only regression or recovery sub-test passes (sideways/recovering MA)
    # Type 3: reserved for falling-MA setups (not currently reachable)
    if c1a_point and pct_slope_raw >= 0:
        ma_type = "type1"
    elif c1a_linreg or c1a_recovering:
        ma_type = "type2"
    else:
        ma_type = "type3"

    # ── Price Interaction Type: Support vs Crossover ──────────────────────────
    # Crossover: price dipped below SMA44 intrabar (Low < SMA44) but closed above
    # Support: price touched SMA44 from above (Low >= SMA44) and closed above
    if low_today < sma_now and close_today >= sma_now:
        price_interaction_type = "crossover"
    else:
        price_interaction_type = "support"

    # ── Double Bottom Detection ────────────────────────────────────────────────
    # Check if a prior SMA44 support touch exists within the last DOUBLE_BOTTOM_LOOKBACK bars
    is_double_bottom = False
    if len(df_clean) >= DOUBLE_BOTTOM_LOOKBACK + 1:
        prior_bars = df_clean.iloc[-(DOUBLE_BOTTOM_LOOKBACK + 1):-1]  # exclude today
        for _, row in prior_bars.iterrows():
            prior_low = float(row["Low"])
            prior_sma = float(row["SMA44"])
            prior_close = float(row["Close"])
            if prior_sma > 0:
                proximity = abs(prior_low - prior_sma) / prior_sma
                if proximity <= SMA44_SUPPORT_BUFFER_PCT and prior_close >= prior_sma:
                    is_double_bottom = True
                    break

    # ── C3: Bullish MACD crossover ────────────────────────────────────────────
    required = MACD_CROSSOVER_LOOKBACK + 2
    if len(df_clean) < required:
        return _NONE

    macd_arr = df_clean["MACD"].values.astype(float)
    signal_arr = df_clean["Signal"].values.astype(float)
    n = len(df_clean)

    crossover_bars_ago: int | None = None
    for k in range(MACD_CROSSOVER_LOOKBACK):
        t = n - (k + 1)
        t_prev = t - 1
        if t_prev < 0:
            break

        macd_t = macd_arr[t]
        signal_t = signal_arr[t]
        macd_t_prev = macd_arr[t_prev]
        signal_t_prev = signal_arr[t_prev]

        bullish_cross = (macd_t_prev <= signal_t_prev) and (macd_t > signal_t)
        if bullish_cross:
            crossover_bars_ago = k
            break

    # ── Confirmed crossover path ──────────────────────────────────────────────
    if crossover_bars_ago is not None:
        payload = _build_payload(
            symbol=symbol,
            bar=cur,
            cross_type="confirmed",
            weekly_rising=weekly_rising,
            crossover_bars_ago=crossover_bars_ago,
            extra_metrics={
                **slope_metrics,
                **c2_metrics,
                "ma_type": ma_type,
                "is_double_bottom": is_double_bottom,
                "price_interaction_type": price_interaction_type,
            },
        )
        return {"status": "signal", "data": payload}

    # ── Watchlist / pending path ──────────────────────────────────────────────
    # No confirmed crossover yet.  Stock enters the watchlist if MACD < Signal.
    macd_cur = float(cur["MACD"])
    signal_cur = float(cur["Signal"])
    if macd_cur >= signal_cur:
        return _NONE

    pending_meta = _pending_metadata(macd_arr, signal_arr)

    # ── Imminent-signal promotion ─────────────────────────────────────────────
    # When all three imminent conditions hold together, the crossover is one
    # bar away at most.  Promote directly to "signal" so the stock appears as
    # TRADE READY rather than sitting silently on the watchlist.
    #   1. MACD < Signal                       (not yet crossed — checked above)
    #   2. Histogram rising >= IMMINENT_HIST_MIN consecutive bars
    #   3. Gap <= IMMINENT_GAP_THRESHOLD of |Signal|
    if pending_meta.get("is_imminent_crossover"):
        payload = _build_payload(
            symbol=symbol,
            bar=cur,
            cross_type="imminent",
            weekly_rising=weekly_rising,
            extra_metrics={
                **slope_metrics,
                **c2_metrics,
                **pending_meta,
                "ma_type": ma_type,
                "is_double_bottom": is_double_bottom,
                "price_interaction_type": price_interaction_type,
            },
        )
        return {"status": "signal", "data": payload}

    # ── Plain watchlist ───────────────────────────────────────────────────────
    payload = _build_payload(
        symbol=symbol,
        bar=cur,
        cross_type="pending",
        weekly_rising=weekly_rising,
        extra_metrics={
            **slope_metrics,
            **c2_metrics,
            **pending_meta,
            "ma_type": ma_type,
            "is_double_bottom": is_double_bottom,
            "price_interaction_type": price_interaction_type,
        },
    )
    return {"status": "watchlist", "data": payload} 

"""
Legacy pivot helpers.

The live scanner no longer depends on pivot-derived entries, stops, or targets,
but this module is kept importable for older analysis scripts and ad hoc use.
It is intentionally self-contained so backend imports do not fail when the main
scanner settings are simplified.
"""

import pandas as pd

DEFAULT_PIVOT_MODE = "fibonacci"
FIB_RATIOS = {
    "R1": 0.382,
    "R2": 0.618,
    "R3": 1.000,
    "S1": 0.382,
    "S2": 0.618,
    "S3": 1.000,
}
PIVOT_MIN_RANGE_RATIO = 0.005
PIVOT_MAX_RANGE_RATIO = 0.15
EXIT_T1_FRACTION = 0.40
EXIT_T2_FRACTION = 0.35
EXIT_T3_FRACTION = 1.00
CONFLUENCE_THRESHOLD = 0.005
CONFLUENCE_SIZE_BOOST = 1.20


def compute_pivots(high: float, low: float, close: float, mode: str = DEFAULT_PIVOT_MODE) -> dict:
    """Compute standard or Fibonacci pivots from a completed period."""
    pivot_mode = (mode or DEFAULT_PIVOT_MODE).strip().lower()
    pivot = (high + low + close) / 3.0
    price_range = high - low

    if pivot_mode == "fibonacci":
        return {
            "P": pivot,
            "R1": pivot + FIB_RATIOS["R1"] * price_range,
            "R2": pivot + FIB_RATIOS["R2"] * price_range,
            "R3": pivot + FIB_RATIOS["R3"] * price_range,
            "S1": pivot - FIB_RATIOS["S1"] * price_range,
            "S2": pivot - FIB_RATIOS["S2"] * price_range,
            "S3": pivot - FIB_RATIOS["S3"] * price_range,
        }

    return {
        "P": pivot,
        "R1": (2.0 * pivot) - low,
        "R2": pivot + price_range,
        "R3": high + 2.0 * (pivot - low),
        "S1": (2.0 * pivot) - high,
        "S2": pivot - price_range,
        "S3": low - 2.0 * (high - pivot),
    }


def pivot_levels_from_df(
    df_comp: pd.DataFrame,
    n_bars: int = 1,
    mode: str | None = None,
) -> dict:
    """Compute pivots from the last completed bar(s) of a DataFrame."""
    if df_comp.empty:
        raise ValueError("Cannot compute pivots from an empty DataFrame.")

    lookback = df_comp.iloc[-n_bars:] if n_bars > 1 else df_comp.iloc[-1:]
    return compute_pivots(
        high=float(lookback["High"].max()),
        low=float(lookback["Low"].min()),
        close=float(lookback["Close"].iloc[-1]),
        mode=mode or DEFAULT_PIVOT_MODE,
    )


def build_exit_targets(
    entry: float,
    stop_loss: float,
    pivots: dict,
    resistance_20: float,
    ltp: float,
) -> dict:
    """
    Build a tiered exit plan from pivots.

    This is retained for legacy consumers only; the live simplified scanner does
    not call it anymore.
    """
    if ltp <= 0:
        ltp = entry

    pivot = float(pivots["P"])
    r1 = float(pivots["R1"])
    r2 = float(pivots["R2"])
    r3 = float(pivots["R3"])
    s1 = float(pivots["S1"])

    prior_range = max(r3 - pivot, 0.0)
    range_ratio = prior_range / ltp if ltp > 0 else 0.0
    risk = max(entry - stop_loss, 0.0)

    if range_ratio < PIVOT_MIN_RANGE_RATIO:
        t1 = round(resistance_20, 2)
        t2 = None
        t3 = round(resistance_20, 2)
        pivot_mode_used = "compressed"
    elif range_ratio > PIVOT_MAX_RANGE_RATIO:
        t1 = round(r1 if r1 > entry else entry + risk, 2)
        t2 = None
        capped_target = min(resistance_20, r1 * 1.10)
        t3 = round(max(capped_target, entry + risk), 2)
        pivot_mode_used = "wide"
    else:
        t1 = round(r1 if r1 > entry else entry + risk, 2)
        t2 = round(r2, 2) if r2 > entry else None
        t3_raw = min(r3, resistance_20) if resistance_20 > 0 else r3
        t3 = round(t3_raw if t3_raw > entry else resistance_20, 2)
        pivot_mode_used = "normal"

    confluence = (
        resistance_20 > 0
        and abs(r1 - resistance_20) / resistance_20 <= CONFLUENCE_THRESHOLD
    )

    return {
        "pivot_pp": round(pivot, 2),
        "pivot_r1": round(r1, 2),
        "pivot_r2": round(r2, 2),
        "pivot_r3": round(r3, 2),
        "pivot_s1": round(s1, 2),
        "pivot_s2": round(float(pivots["S2"]), 2),
        "pivot_s3": round(float(pivots["S3"]), 2),
        "t1": t1,
        "t2": t2,
        "t3": t3,
        "t1_fraction": EXIT_T1_FRACTION,
        "t2_fraction": EXIT_T2_FRACTION,
        "t3_fraction": EXIT_T3_FRACTION,
        "stop_above_s1": bool(stop_loss > s1),
        "high_confidence": confluence,
        "size_boost": CONFLUENCE_SIZE_BOOST if confluence else 1.00,
        "pivot_mode_used": pivot_mode_used,
    }

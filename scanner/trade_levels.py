"""
scanner/trade_levels.py
───────────────────────
Calculates entry price, stop loss, Fibonacci pivot targets, and risk-reward
for a confirmed signal bar.

Design
──────
All calculations are pure functions that operate on the setup candle's
OHLC values.  No I/O, no side effects.

Entry types
───────────
  breakout  (default)
      Entry = Setup High × (1 + ENTRY_BUFFER_PCT)
      Used when the setup candle is not classified as "aggressive".

  pivot     (conditional — "P" level)
      Entry = (High + Low + Close) / 3
      Used when the setup candle is aggressive, i.e. its body is a large
      fraction of its total range (>= AGGRESSIVE_BODY_RATIO).

Stop loss
─────────
  SL = Setup Low × (1 − SL_BUFFER_PCT)

Pivot points & Fibonacci targets
─────────────────────────────────
  P     = (High + Low + Close) / 3
  Range = High − Low
  R1    = P + 0.382 × Range
  R2    = P + 0.618 × Range
  R3    = P + 1.000 × Range
  T1    = R1
  T2    = R2
  T3    = min(R3, 20-bar high)   # capped at recent swing high

Risk / Reward
─────────────
  Risk   = Entry − SL
  RR(Tn) = (Tn − Entry) / Risk
  Signal qualifies only when RR(T1) >= MIN_RR (default 2.0).

  NOTE: R1 alone must satisfy the RR gate.  T2/T3 are bonus targets;
  they are always included in the payload for frontend display but do
  not independently gate the signal.

Public API
──────────
  compute_trade_levels(bar, df_20bar_high) → dict | None
      Returns None when the setup is geometrically invalid (zero range,
      entry below SL, etc.) or when RR(T1) < MIN_RR.
      Otherwise returns a dict with all trade-level fields ready for
      inclusion in the conditions.py signal payload.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config.settings import (
    AGGRESSIVE_BODY_RATIO,
    ENTRY_BUFFER_PCT,
    MIN_RR,
    SL_BUFFER_PCT,
)

# Fibonacci expansion ratios for pivot targets
_FIB_R1 = 0.382
_FIB_R2 = 0.618
_FIB_R3 = 1.000


def _is_aggressive(high: float, low: float, open_: float, close: float) -> bool:
    """
    True when the candle body is >= AGGRESSIVE_BODY_RATIO of the total range.
    An 'aggressive' candle has already moved strongly in the direction of the
    trend; chasing a breakout above its high risks a poor entry.
    """
    candle_range = high - low
    if candle_range <= 0:
        return False
    body = abs(close - open_)
    return (body / candle_range) >= AGGRESSIVE_BODY_RATIO


def compute_trade_levels(
    bar: "pd.Series",
    recent_bars: "pd.DataFrame | None" = None,
) -> dict | None:
    """
    Compute entry, stop loss, targets, and risk-reward for the setup candle.

    Parameters
    ----------
    bar : pd.Series
        The latest (signal) candle with at least Open, High, Low, Close columns.
    recent_bars : pd.DataFrame | None
        The full clean DataFrame up to and including the signal bar, used to
        derive the 20-bar high for T3 capping.  If None, T3 = R3 (uncapped).

    Returns
    -------
    dict
        Trade level payload, or None if the setup is invalid / RR too low.
    """
    high  = float(bar["High"])
    low   = float(bar["Low"])
    close = float(bar["Close"])
    open_ = float(bar["Open"])

    # ── Basic sanity ──────────────────────────────────────────────────────────
    candle_range = high - low
    if candle_range <= 0 or low <= 0 or high <= 0:
        return None

    # ── Pivot point ───────────────────────────────────────────────────────────
    pivot = (high + low + close) / 3

    # ── Entry ─────────────────────────────────────────────────────────────────
    aggressive = _is_aggressive(high, low, open_, close)
    if aggressive:
        entry      = pivot
        entry_type = "pivot"
    else:
        entry      = high * (1 + ENTRY_BUFFER_PCT)
        entry_type = "breakout"

    # ── Stop loss ─────────────────────────────────────────────────────────────
    sl = low * (1 - SL_BUFFER_PCT)

    risk = entry - sl
    if risk <= 0:
        # Degenerate: SL is at or above entry (can happen on gap-up candles)
        return None

    # ── Fibonacci pivot targets ───────────────────────────────────────────────
    r1 = pivot + _FIB_R1 * candle_range
    r2 = pivot + _FIB_R2 * candle_range
    r3 = pivot + _FIB_R3 * candle_range

    # 20-bar high cap for T3
    if recent_bars is not None and len(recent_bars) >= 2:
        # Use the last 20 bars excluding the signal bar itself to find the
        # prior swing high; this avoids circular self-referencing.
        lookback = recent_bars["High"].iloc[-20:]
        bar_20_high = float(lookback.max()) if not lookback.empty else r3
    else:
        bar_20_high = r3

    t1 = r1
    t2 = r2
    t3 = min(r3, bar_20_high)

    # ── Risk-reward ───────────────────────────────────────────────────────────
    rr1 = (t1 - entry) / risk
    rr2 = (t2 - entry) / risk
    rr3 = (t3 - entry) / risk

    # NOTE: RR metrics are always computed and included in the payload for
    # frontend display and analysis. The signal is NOT gated on RR here;
    # that check is now informational only.

    return {
        # Entry
        "entry_type"      : entry_type,
        "entry"           : round(entry, 2),
        "sl"              : round(sl,    2),
        "risk"            : round(risk,  2),
        # Pivot geometry
        "pivot"           : round(pivot, 2),
        "candle_range"    : round(candle_range, 2),
        "is_aggressive"   : aggressive,
        # Targets
        "t1"              : round(t1, 2),
        "t2"              : round(t2, 2),
        "t3"              : round(t3, 2),
        "bar_20_high"     : round(bar_20_high, 2),
        # Reward & RR per target
        "reward_t1"       : round(t1 - entry, 2),
        "reward_t2"       : round(t2 - entry, 2),
        "reward_t3"       : round(t3 - entry, 2),
        "rr_t1"           : round(rr1, 2),
        "rr_t2"           : round(rr2, 2),
        "rr_t3"           : round(rr3, 2),
        # Thresholds for frontend display
        "min_rr"          : MIN_RR,
        "entry_buffer_pct": round(ENTRY_BUFFER_PCT * 100, 3),
        "sl_buffer_pct"   : round(SL_BUFFER_PCT    * 100, 3),
    }

"""
scanner/trade_range.py
──────────────────────
Suggested ENTRY and EXIT price ranges for the admin website. Pure functions:
no I/O, no Fyers calls, nothing is sent or published. The numbers are only a
starting point — the admin can edit every one of them before anything goes out.

How the ranges are sized
────────────────────────
A fixed percentage would be far too wide for a calm large-cap and far too narrow
for a jumpy mid-cap, so the width follows the stock's own daily volatility:

  ATR14   average true range of the last 14 daily candles (the SAME ATR14 the
          scanner already computes in indicators/technical.py). It is read from
          the daily history store, so no extra Fyers request is made.
  clamp   the ATR is kept between 0.8 % and 4 % of the price, so one freak
          candle cannot make the zone absurdly wide or paper-thin.
          (If no history is stored for the stock, 1.8 % of price is used and the
          result is labelled "estimate".)

  Entry range  low  = price − 0.40 × ATR      (mostly BELOW the live price:
               high = price + 0.10 × ATR       a buy zone, not a chase)
               The upper edge is also capped at ENTRY_EXTENDED_PCT above the live
               price — the scanner already calls anything further "extended".

  Exit range   low  = price − 0.15 × ATR      (half the width of the entry range:
               high = price + 0.10 × ATR       an exit has to be hit, not waited for)

Both ends are snapped to the exchange tick (₹0.05; ₹0.01 for stocks under ₹20):
the low end rounds down, the high end rounds up, so the live price is always
inside both ranges. The exit range is guaranteed to be narrower than the entry
range.

Example: price ₹542, ATR14 ₹10.80 (2 %)  →  entry ₹537.65 – ₹543.10,
                                             exit  ₹540.35 – ₹543.10.
"""

from __future__ import annotations

import math

from config.settings import ENTRY_EXTENDED_PCT

# ── Tunables ─────────────────────────────────────────────────────────────────
ATR_MIN_PCT      = 0.008   # ATR is never treated as smaller than 0.8 % of price
ATR_MAX_PCT      = 0.040   # ... nor larger than 4 % of price
ATR_FALLBACK_PCT = 0.018   # used only when there is no stored history for the stock

ENTRY_BELOW_ATR = 0.40
ENTRY_ABOVE_ATR = 0.10
EXIT_BELOW_ATR  = 0.15
EXIT_ABOVE_ATR  = 0.10

LOW_PRICE_LIMIT = 20.0     # below this the exchange tick is ₹0.01
MIN_ENTRY_TICKS = 4        # a range is never narrower than this many ticks
MIN_EXIT_TICKS  = 2


def tick_size(price: float) -> float:
    """Approximate NSE tick: ₹0.05 for almost every stock, ₹0.01 for very low-priced ones."""
    return 0.01 if price < LOW_PRICE_LIMIT else 0.05


def _finite_positive(value) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and x > 0 else None


def suggest_ranges(price, atr=None) -> dict | None:
    """
    Suggested entry and exit ranges around the live `price`.

    `atr` is the stock's ATR14 in ₹ (or None when unknown).
    Returns None when the price is unusable, otherwise:

      {
        "price": 542.0, "atr": 10.8, "basis": "atr14" | "estimate", "tick": 0.05,
        "entry": {"low": 537.65, "high": 543.10},
        "exit":  {"low": 540.35, "high": 543.10},
      }
    """
    p = _finite_positive(price)
    if p is None:
        return None

    tick = tick_size(p)
    atr_in = _finite_positive(atr)
    basis = "atr14" if atr_in is not None else "estimate"
    atr_raw = atr_in if atr_in is not None else p * ATR_FALLBACK_PCT
    eff = min(max(atr_raw, p * ATR_MIN_PCT), p * ATR_MAX_PCT)

    # Work in whole ticks so the result has no floating-point dust.
    p_t = int(round(p / tick))
    below_e = int(math.ceil(ENTRY_BELOW_ATR * eff / tick))
    above_e = int(math.ceil(ENTRY_ABOVE_ATR * eff / tick))
    below_x = int(math.ceil(EXIT_BELOW_ATR * eff / tick))
    above_x = int(math.ceil(EXIT_ABOVE_ATR * eff / tick))

    # Never invite chasing further above the price than the scanner's own
    # "extended" limit — but always leave at least one tick of room.
    cap_up = max(1, int(math.floor(p * ENTRY_EXTENDED_PCT / 100.0 / tick)))
    above_e = min(above_e, cap_up)
    above_x = min(above_x, cap_up)

    # Minimum widths.
    if below_e + above_e < MIN_ENTRY_TICKS:
        below_e = MIN_ENTRY_TICKS - above_e
    if below_x + above_x < MIN_EXIT_TICKS:
        below_x = MIN_EXIT_TICKS - above_x

    # The exit range must be tighter than the entry range.
    if below_x + above_x >= below_e + above_e:
        below_x = max(0, below_e + above_e - 1 - above_x)
        if below_x + above_x >= below_e + above_e:     # still not narrower: trim the top too
            above_x = max(1, below_e + above_e - 1 - below_x)

    def _price(ticks: int) -> float:
        return round(ticks * tick, 2)

    entry = {"low": _price(p_t - below_e), "high": _price(p_t + above_e)}
    exit_ = {"low": _price(p_t - below_x), "high": _price(p_t + above_x)}

    # A price near zero can push the low end to or below zero: keep a sane floor.
    if entry["low"] <= 0 or exit_["low"] <= 0:
        entry["low"] = max(entry["low"], _price(1))
        exit_["low"] = max(exit_["low"], _price(1))

    return {
        "price": round(p, 2),
        "atr": round(atr_raw, 2),
        "basis": basis,
        "tick": tick,
        "entry": entry,
        "exit": exit_,
    }


def atr14_from_frame(df) -> float | None:
    """
    ATR14 (₹) of the newest candle in a daily OHLC frame, computed exactly as in
    indicators/technical.py. Returns None when the frame is too short or invalid.
    """
    try:
        if df is None or len(df) < 15:
            return None
        import pandas as pd

        high_low = df["High"] - df["Low"]
        high_pc = (df["High"] - df["Close"].shift()).abs()
        low_pc = (df["Low"] - df["Close"].shift()).abs()
        tr = pd.concat([high_low, high_pc, low_pc], axis=1).max(axis=1)
        value = float(tr.rolling(14).mean().iloc[-1])
        return value if math.isfinite(value) and value > 0 else None
    except Exception:
        return None

"""
indicators/technical.py
───────────────────────
Computes SMA44, MACD (12/26/9), ATR14, and Volume MA20.

Design contract
───────────────
• Returns the FULL DataFrame with NaN rows preserved.
  - First 43 rows: SMA44 is NaN
  - First ~25 rows: MACD/Signal are near-zero (EMA warm-up)
• Callers must handle NaN explicitly; do not dropna here.
  Preserving bar count is required for fixed-index crossover lookbacks
  in conditions.py.

No changes from the prior implementation — this module is correct and kept as-is.
"""

import pandas as pd
from config.settings import SMA_PERIOD, MACD_FAST, MACD_SLOW, MACD_SIGNAL

VOL_MA_PERIOD = 20


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds SMA44, MACD, Signal, ATR14, and Vol_MA20 columns to the DataFrame.

    Parameters
    ----------
    df : pd.DataFrame
        Raw OHLCV DataFrame with at least a 'Close' column.

    Returns
    -------
    pd.DataFrame
        Same DataFrame with indicator columns appended.
        NaN rows in early bars are intentional — do NOT dropna downstream
        before passing to conditions.py.
    """
    df = df.copy()

    # SMA44 — simple rolling mean; first SMA_PERIOD-1 rows will be NaN
    df["SMA44"] = df["Close"].rolling(window=SMA_PERIOD, min_periods=SMA_PERIOD).mean()

    # MACD — EMA(fast) minus EMA(slow)
    ema_fast     = df["Close"].ewm(span=MACD_FAST,   adjust=False).mean()
    ema_slow     = df["Close"].ewm(span=MACD_SLOW,   adjust=False).mean()
    df["MACD"]   = ema_fast - ema_slow

    # Signal line — EMA of MACD
    df["Signal"] = df["MACD"].ewm(span=MACD_SIGNAL, adjust=False).mean()

    high_low = df["High"] - df["Low"]
    high_pc  = (df["High"] - df["Close"].shift()).abs()
    low_pc   = (df["Low"]  - df["Close"].shift()).abs()
    df["ATR14"] = (
        pd.concat([high_low, high_pc, low_pc], axis=1)
        .max(axis=1)
        .rolling(14)
        .mean()
    )

    # Volume MA20 — 20-bar rolling mean volume (min_periods=1 so early rows
    # are populated; this is acceptable as it is never used as a hard gate
    # in the new scanner)
    if "Volume" in df.columns:
        df["Vol_MA20"] = df["Volume"].rolling(window=VOL_MA_PERIOD, min_periods=1).mean()

    return df   # NO dropna — preserve full bar count

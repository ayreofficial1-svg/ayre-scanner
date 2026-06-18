"""
data/candles.py
───────────────
Fetches daily OHLCV candle data from Fyers API v3.

Key facts about Fyers daily data:
  - Hard cap: 366 calendar days per request
  - Returns: ~249 trading days per window
  - We make TWO sequential requests (windows W1 + W2) to build a
    ~500-bar history, then concatenate and deduplicate before returning.

Why two windows?
  MACD is built from three EMAs (12, 26, and 9-bar Signal).  EMA is
  recursive — every value depends on every prior value.  With only 249
  bars, EMA(26) still carries ~0.15% seed drift from its initialisation
  point.  That sounds small, but the MACD histogram is the *difference*
  of two EMAs, so errors compound: a stock near a crossover (histogram
  near zero) can show MACD above or below Signal depending entirely on
  how many warmup bars were provided.  This causes false misses and
  false triggers on near-cross detection.

  With ~500 bars (two 366-day windows), EMA(26) seed influence decays
  to < 0.001% — negligible for any practical decision.  MACD values
  then match TradingView to within normal rounding differences.

Fetch layout (calendar days, counting back from today / range_to):
  W2  days 0   → 366   (recent window — contains today / target date)
  W1  days 366 → 732   (older window — pure EMA warmup)

  The two windows are concatenated, sorted, and deduplicated.  The
  combined DataFrame is what conditions.py receives.  The older bars
  (W1) will have valid SMA44 and MACD columns after compute_indicators
  runs, ensuring the EMA is fully warmed up by the time the scanner
  evaluates the recent bars.

Other fetch details:
  - Sequential, one symbol at a time, 0.12 s sleep per request
    (2 requests per symbol → 0.24 s effective rate per symbol)
  - Single retry for failed symbols after 3 s pause
  - Alternate suffixes tried if -EQ fails (-BE, -BZ, -SM, -ST)
  - ~94 stocks in Nifty 500 have no Fyers data — skipped silently
  - Minimum bars returned: 80 (handles newly listed stocks)
    SMA200 will be NaN for stocks with < 200 bars (handled gracefully)

Completeness guarantee:
  fetch_candles_bulk() returns a (results, report) tuple.  Every symbol
  in the input list lands in exactly one of three buckets:
    valid    — usable DataFrame returned
    no_data  — Fyers has no history (invalid symbol / newly listed / suspended)
    failed   — transient error that survived both passes
  valid + no_data + failed == attempted (always).  The engine uses this
  report to assert and log full coverage on every run.
"""

import datetime
import time
import pandas as pd
from fyers_apiv3 import fyersModel

# ── Constants ─────────────────────────────────────────────────────────────────
#
# Fyers hard cap per request for 1D resolution.
# We issue two consecutive requests to reach ~500 trading bars total.
_WINDOW_DAYS   = 366          # calendar days per Fyers request (hard cap)
_NUM_WINDOWS   = 2            # number of sequential windows to fetch
_MIN_BARS      = 80           # minimum usable bars (handles newly listed stocks)
_SLEEP         = 0.12         # 120 ms between requests (~8 req/s; Fyers limit: 10)
_RETRY_PAUSE   = 3.0          # seconds before retrying failed symbols (first retry)
_ALT_SUFFIXES  = ["-BE", "-BZ", "-SM", "-ST"]   # fallback suffixes for -EQ
_RATE_LIMIT_RETRIES = 3
_RATE_LIMIT_PAUSE = 1.5
_INVALID_SYMBOL_CODE = -300
_RATE_LIMIT_CODE = 429

# ── Persistent retry configuration ────────────────────────────────────────────
_PERSISTENT_RETRY_ENABLED = True       # Enable persistent retry for failed symbols
_PERSISTENT_MAX_RETRIES = 2            # Max retry attempts per symbol (0 = infinite)
_PERSISTENT_RETRY_INTERVAL = 5.0       # Seconds between persistent retry attempts
_PERSISTENT_BACKOFF_MULTIPLIER = 1.5   # Exponential backoff: interval *= multiplier


def _response_code(resp: dict | None) -> int | None:
    try:
        return int(resp.get("code")) if resp and resp.get("code") is not None else None
    except (TypeError, ValueError):
        return None


def _request_history_window(
    fyers: fyersModel.FyersModel,
    symbol: str,
    range_from: datetime.date,
    range_to: datetime.date,
) -> tuple[pd.DataFrame | None, str]:
    """
    Fetch one window of daily candles.

    Returns (dataframe, status) where status is one of:
    - "ok"
    - "empty"
    - "invalid_symbol"
    - "failed"
    """
    for attempt in range(_RATE_LIMIT_RETRIES + 1):
        try:
            resp = fyers.history(
                data={
                    "symbol": symbol,
                    "resolution": "D",
                    "date_format": "1",
                    "range_from": range_from.strftime("%Y-%m-%d"),
                    "range_to": range_to.strftime("%Y-%m-%d"),
                    "cont_flag": "1",
                }
            )
        except Exception:
            resp = None

        time.sleep(_SLEEP)

        if resp and resp.get("s") == "ok":
            candles = resp.get("candles", [])
            if not candles:
                return None, "empty"

            df = pd.DataFrame(
                candles,
                columns=["Timestamp", "Open", "High", "Low", "Close", "Volume"],
            )
            df["Timestamp"] = pd.to_datetime(df["Timestamp"], unit="s")
            df.set_index("Timestamp", inplace=True)
            df = df[["Open", "High", "Low", "Close", "Volume"]].astype(float)
            return df, "ok"

        code = _response_code(resp)
        if code == _INVALID_SYMBOL_CODE:
            return None, "invalid_symbol"

        if code == _RATE_LIMIT_CODE and attempt < _RATE_LIMIT_RETRIES:
            time.sleep(_RATE_LIMIT_PAUSE * (attempt + 1))
            continue

        return None, "failed"

    return None, "failed"


def _fetch_windows(
    fyers     : fyersModel.FyersModel,
    symbol    : str,
    range_to  : datetime.date,
) -> pd.DataFrame | None:
    """
    Fetch _NUM_WINDOWS consecutive 366-day windows ending on range_to
    and return a single cleaned, concatenated DataFrame.

    Windows are non-overlapping and laid out like this (for 2 windows):
      W1: [range_to − 732d, range_to − 366d)   — older, EMA warmup
      W2: [range_to − 366d, range_to]           — recent, evaluation target

    Each window is fetched with a separate Fyers API call.  Failed
    windows are skipped silently; as long as the combined DataFrame has
    ≥ _MIN_BARS rows the result is usable (the EMA will be less well
    warmed up but still functional).

    Parameters
    ----------
    range_to : datetime.date
        The end date of the most recent window.
        Live mode  → today.
        Historical → the target backtest date.
    """
    frames: list[pd.DataFrame] = []

    for w in range(_NUM_WINDOWS - 1, -1, -1):
        # w = 1 (oldest) … 0 (most recent)
        w_to   = range_to  - datetime.timedelta(days=w * _WINDOW_DAYS)
        w_from = w_to      - datetime.timedelta(days=_WINDOW_DAYS)
        df, status = _request_history_window(fyers, symbol, w_from, w_to)
        if status == "invalid_symbol":
            return None
        if df is not None:
            frames.append(df)

    if not frames:
        return None

    combined = pd.concat(frames)
    combined.sort_index(inplace=True)

    # ── Last record per calendar date ─────────────────────────────────────────
    # Fyers occasionally returns more than one row for the same date (e.g. a
    # partial intraday bar alongside the completed EOD bar).  Keep only the
    # final row per date so every bar represents a fully formed session.
    dates    = combined.index.normalize()
    combined = combined[~dates.duplicated(keep="last")]

    combined.dropna(inplace=True)

    # ── Rolling-window clip: [range_to − _LOOKBACK_DAYS + 1 … range_to] ──────
    # Trims the combined DataFrame to exactly _NUM_WINDOWS × _WINDOW_DAYS
    # calendar days ending on range_to.  This prevents future bars from leaking
    # in when Fyers rounds a window boundary forward, and ensures production and
    # debug see identical data for the same date.  MACD accuracy is unaffected:
    # EMA(26) converges within ~200 bars; the clipped window still contains
    # ~500 trading bars — well past the convergence point.
    _lookback    = _NUM_WINDOWS * _WINDOW_DAYS
    cutoff_start = pd.Timestamp(range_to) - pd.Timedelta(days=_lookback - 1)
    cutoff_end   = pd.Timestamp(range_to) + pd.Timedelta(days=1)   # exclusive
    combined     = combined[(combined.index >= cutoff_start) & (combined.index < cutoff_end)]

    return combined if len(combined) >= _MIN_BARS else None


def _fetch_one(fyers: fyersModel.FyersModel, symbol: str) -> pd.DataFrame | None:
    """
    Fetch daily candles for one symbol ending today (live mode).

    Calls _fetch_windows with range_to = today.  The returned DataFrame
    spans approximately two calendar years (~500 trading bars), which
    provides sufficient EMA warmup for MACD to match TradingView values.
    """
    return _fetch_windows(fyers, symbol, range_to=datetime.date.today())


def fetch_candles(fyers: fyersModel.FyersModel, symbol: str) -> pd.DataFrame | None:
    """
    Fetch daily candles for one symbol (live mode).
    Tries alternate suffixes if the primary -EQ symbol fails.
    """
    df = _fetch_one(fyers, symbol)
    if df is not None:
        return df

    base = symbol.replace("-EQ", "")
    for suffix in _ALT_SUFFIXES:
        df = _fetch_one(fyers, base + suffix)
        if df is not None:
            return df

    return None


def _request_history_window_weekly(
    fyers: fyersModel.FyersModel,
    symbol: str,
    range_from: datetime.date,
    range_to: datetime.date,
) -> tuple[pd.DataFrame | None, str]:
    """
    Fetch one window of weekly candles.
    Same structure as _request_history_window but uses "W" resolution.

    Returns (dataframe, status) where status is one of:
    - "ok"
    - "empty"
    - "invalid_symbol"
    - "failed"
    """
    for attempt in range(_RATE_LIMIT_RETRIES + 1):
        try:
            resp = fyers.history(
                data={
                    "symbol": symbol,
                    "resolution": "W",
                    "date_format": "1",
                    "range_from": range_from.strftime("%Y-%m-%d"),
                    "range_to": range_to.strftime("%Y-%m-%d"),
                    "cont_flag": "1",
                }
            )
        except Exception:
            resp = None

        time.sleep(_SLEEP)

        if resp and resp.get("s") == "ok":
            candles = resp.get("candles", [])
            if not candles:
                return None, "empty"

            df = pd.DataFrame(
                candles,
                columns=["Timestamp", "Open", "High", "Low", "Close", "Volume"],
            )
            df["Timestamp"] = pd.to_datetime(df["Timestamp"], unit="s")
            df.set_index("Timestamp", inplace=True)
            df = df[["Open", "High", "Low", "Close", "Volume"]].astype(float)
            return df, "ok"

        code = _response_code(resp)
        if code == _INVALID_SYMBOL_CODE:
            return None, "invalid_symbol"

        if code == _RATE_LIMIT_CODE and attempt < _RATE_LIMIT_RETRIES:
            time.sleep(_RATE_LIMIT_PAUSE * (attempt + 1))
            continue

        return None, "failed"

    return None, "failed"


def _fetch_weekly_candles(
    fyers: fyersModel.FyersModel,
    symbol: str,
    range_to: datetime.date | None = None,
) -> pd.DataFrame | None:
    """
    Fetch weekly candles for one symbol using two consecutive 366-day windows.
    This ensures ~104 bars regardless of Fyers' per-request cap, providing
    sufficient data for SMA44 warmup (44 bars) plus lookback (10 bars).
    """
    if range_to is None:
        range_to = datetime.date.today()

    frames: list[pd.DataFrame] = []

    # Two consecutive 366-day windows (W1=older, W2=recent) to guarantee ~104 bars
    for w in range(1, -1, -1):  # w=1 (older), w=0 (recent)
        w_to   = range_to  - datetime.timedelta(days=w * _WINDOW_DAYS)
        w_from = w_to      - datetime.timedelta(days=_WINDOW_DAYS)
        df, status = _request_history_window_weekly(fyers, symbol, w_from, w_to)
        if status == "invalid_symbol":
            return None
        if df is not None:
            frames.append(df)

    if not frames:
        # Try alternate suffixes on the recent window only
        base = symbol.replace("-EQ", "")
        for suffix in _ALT_SUFFIXES:
            w_to   = range_to
            w_from = range_to - datetime.timedelta(days=_WINDOW_DAYS)
            df, status = _request_history_window_weekly(fyers, base + suffix, w_from, w_to)
            if df is not None:
                frames.append(df)
                break

    if not frames:
        return None

    combined = pd.concat(frames)
    combined.sort_index(inplace=True)
    dates = combined.index.normalize()
    combined = combined[~dates.duplicated(keep="last")]
    combined.dropna(inplace=True)

    # Need at least 54 bars: 44 SMA warmup + 10 lookback + 1 for comparison
    return combined if len(combined) >= 54 else None


def fetch_weekly_candles_bulk(
    fyers: fyersModel.FyersModel,
    symbols: list[str],
    range_to: datetime.date | None = None,
    verbose: bool = False,
) -> tuple[dict[str, pd.DataFrame], dict]:
    """
    Fetch weekly candles for all symbols for the weekly rising pre-filter.

    Returns
    -------
    (results, report)

    results : dict[symbol → DataFrame]
        Only symbols with usable weekly data.

    report : dict with keys:
        attempted  int   — len(symbols)
        valid      int   — symbols with usable weekly data
        no_data    int   — symbols with no weekly data
        failed     int   — symbols that errored
    """
    results: dict[str, pd.DataFrame] = {}
    no_data: list[str] = []
    failed: list[str] = []
    total = len(symbols)
    if range_to is None:
        range_to = datetime.date.today()

    # Fyers allows ~10 req/s. Each weekly symbol makes 2 requests with a 0.12s
    # sleep between them, so each thread sustains ~4.2 req/s.
    # 2 workers × 4.2 req/s ≈ 8.4 req/s — safely within the Fyers limit.
    # Do not raise above 2 without also increasing _SLEEP in _request_history_window_weekly.
    _WEEKLY_WORKERS = 2
    completed = 0

    import concurrent.futures as _cf

    def _fetch_one_weekly(sym: str) -> tuple[str, pd.DataFrame | None]:
        return sym, _fetch_weekly_candles(fyers, sym, range_to)

    with _cf.ThreadPoolExecutor(max_workers=_WEEKLY_WORKERS) as pool:
        futures = {pool.submit(_fetch_one_weekly, sym): sym for sym in symbols}
        for future in _cf.as_completed(futures):
            sym, df = future.result()
            completed += 1
            if df is not None:
                results[sym] = df
            else:
                no_data.append(sym)
            if completed % 50 == 0 or completed == total:
                print(
                    f"   📥  Weekly: {completed}/{total} processed — "
                    f"{len(results)} valid, {len(no_data)} skipped …",
                    end="\r",
                )
    print()

    report = {
        "attempted": total,
        "valid": len(results),
        "no_data": len(no_data),
        "failed": len(failed),
    }

    return results, report


def fetch_weekly_candles(
    fyers: fyersModel.FyersModel,
    symbols: list[str],
    range_to: datetime.date | None = None,
    verbose: bool = False,
) -> tuple[dict[str, pd.DataFrame], dict]:
    """Backward-compatible public wrapper for weekly candle bulk fetches."""
    return fetch_weekly_candles_bulk(fyers, symbols, range_to=range_to, verbose=verbose)


def weekly_candles_from_daily(
    daily_data: dict[str, pd.DataFrame],
) -> tuple[dict[str, pd.DataFrame], dict]:
    """
    Build FYERS-equivalent weekly OHLCV bars from daily candles already fetched.

    The scanner's daily fetch covers two calendar years, which is the same
    history span previously requested again at weekly resolution. Resampling
    locally removes two FYERS history calls per symbol without changing the
    weekly SMA44 filter input or decision.
    """
    results: dict[str, pd.DataFrame] = {}
    no_data: list[str] = []

    aggregation = {
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        "Volume": "sum",
    }

    for symbol, daily_df in daily_data.items():
        if daily_df is None or daily_df.empty:
            no_data.append(symbol)
            continue

        weekly_df = (
            daily_df.sort_index()
            .resample("W-FRI", label="right", closed="right")
            .agg(aggregation)
            .dropna(subset=["Open", "High", "Low", "Close"])
        )
        if len(weekly_df) >= 54:
            results[symbol] = weekly_df
        else:
            no_data.append(symbol)

    report = {
        "attempted": len(daily_data),
        "valid": len(results),
        "no_data": len(no_data),
        "failed": 0,
        "source": "resampled_daily",
        "api_calls": 0,
    }
    return results, report


def _classify_symbol(
    fyers: fyersModel.FyersModel,
    symbol: str,
    range_to: datetime.date | None = None,
) -> str:
    """
    Probe Fyers with a single recent window to classify why a symbol failed.

    Returns "no_data" if Fyers has no history for this symbol (invalid,
    suspended, newly listed), or "failed" if the error was transient.

    This is called only for symbols that returned None from fetch_candles,
    so the extra API call is limited to the failure set (~94 expected).
    """
    if range_to is None:
        range_to = datetime.date.today()

    _, status = _request_history_window(
        fyers,
        symbol,
        range_from=range_to - datetime.timedelta(days=_WINDOW_DAYS),
        range_to=range_to,
    )
    return "no_data" if status in ("invalid_symbol", "empty") else "failed"


def fetch_candles_bulk(
    fyers   : fyersModel.FyersModel,
    symbols : list[str],
    interval: str = "D",
    verbose : bool = False,
) -> tuple[dict[str, pd.DataFrame], dict]:
    """
    Fetch daily candles for all symbols sequentially (live mode).

    Returns
    -------
    (results, report)

    results : dict[symbol → DataFrame]
        Only symbols with usable data (≥ _MIN_BARS rows).

    report : dict with keys:
        attempted  int   — len(symbols) — the immutable ground truth
        valid      int   — symbols with a usable DataFrame
        no_data    int   — symbols Fyers has no history for (expected ~94)
        failed     int   — symbols that errored and were not recovered
        recovered  int   — symbols that failed pass-1 but succeeded on retry
        missing    list  — symbols in `symbols` but absent from `results`
                           (no_data + failed combined; for completeness logging)

    Completeness guarantee
    ----------------------
    After both passes, every symbol in `symbols` falls into exactly one
    bucket: valid, no_data, or failed.
      valid + no_data + failed == attempted   (always true)

    The caller (engine.py) uses `report` to assert this invariant and log
    any symbols that fall through the cracks.

    Classification logic
    --------------------
    Pass 1 — fetch all symbols.  Failures are tentatively labelled as
    needing retry.  No classification probe is fired yet (saves API calls
    for the expected ~94 no-data symbols that will be consistently empty).

    Pass 2 — retry tentative failures after _RETRY_PAUSE seconds.  For
    each symbol that still returns None we fire a lightweight single-window
    probe to determine whether the failure is structural (no_data) or
    transient (failed).  This means the classification probe runs only for
    the true failure set, not all 498 symbols.
    """
    results      : dict[str, pd.DataFrame] = {}
    no_data      : list[str] = []   # Fyers has no history — expected, not retried
    to_retry     : list[str] = []   # transient failures from pass-1
    total        = len(symbols)

    # ── Pass 1 ────────────────────────────────────────────────────────────────
    for i, sym in enumerate(symbols, 1):
        df = fetch_candles(fyers, sym)
        if df is not None:
            results[sym] = df
        else:
            to_retry.append(sym)   # classify after pass-2

        if i % 50 == 0 or i == total:
            print(
                f"   📥  {i}/{total} processed — "
                f"{len(results)} valid, {len(to_retry)} to retry …",
                end="\r",
            )
    print()

    # ── Pass 2 — retry once, then classify each remaining failure ─────────────
    recovered_count  = 0
    still_failed     : list[str] = []

    if to_retry:
        print(f"   🔄  Retrying {len(to_retry)} failed symbols …")
        time.sleep(_RETRY_PAUSE)

        for sym in to_retry:
            df = fetch_candles(fyers, sym)
            if df is not None:
                results[sym] = df
                recovered_count += 1
            else:
                # Classify: structural missing vs transient error
                bucket = _classify_symbol(fyers, sym)
                if bucket == "no_data":
                    no_data.append(sym)
                else:
                    still_failed.append(sym)

        if recovered_count:
            print(f"   ✅  Recovered {recovered_count} on retry")
        reclassified = len(to_retry) - recovered_count - len(still_failed)
        if reclassified:
            print(f"   ℹ️   {reclassified} reclassified as no-data after retry")
        if still_failed:
            print(
                f"   ⚠️   {len(still_failed)} symbols failed both passes: "
                f"{still_failed[:5]}" + (" …" if len(still_failed) > 5 else "")
            )

    if no_data:
        print(f"   ℹ️   {len(no_data)} symbols skipped (no Fyers history — expected)")

    # ── Completeness report ───────────────────────────────────────────────────
    missing   = [s for s in symbols if s not in results]
    accounted = len(results) + len(no_data) + len(still_failed)

    if accounted != total:
        # Defensive: catch any symbol that slipped through classification.
        # This should never happen, but if it does we surface it clearly.
        unaccounted = [
            s for s in symbols
            if s not in results and s not in no_data and s not in still_failed
        ]
        print(
            f"   ❌  COMPLETENESS BUG: {total - accounted} symbols unaccounted for "
            f"after both passes: {unaccounted[:10]}"
            + (" …" if len(unaccounted) > 10 else "")
        )
        # Force them into still_failed so the report is always consistent
        still_failed.extend(unaccounted)

    report = {
        "attempted" : total,
        "valid"     : len(results),
        "no_data"   : len(no_data),
        "failed"    : len(still_failed),
        "recovered" : recovered_count,
        "missing"   : missing,   # valid + no_data + failed == attempted
    }

    return results, report


def fetch_candles_bulk_persistent(
    fyers   : fyersModel.FyersModel,
    symbols : list[str],
    interval: str = "D",
    verbose : bool = False,
) -> tuple[dict[str, pd.DataFrame], dict]:
    """
    Fetch candles with persistent retry logic for failed symbols.

    This wrapper calls fetch_candles_bulk() and then keeps retrying any
    symbols that failed due to transient errors (not no_data symbols).
    Uses exponential backoff between attempts.

    Parameters
    ----------
    fyers   : authenticated FyersModel instance
    symbols : list of Fyers-formatted symbols
    interval: data resolution (always "D" for daily)
    verbose : if True, print per-symbol results

    Returns
    -------
    (results, report) — same format as fetch_candles_bulk()
        report additionally includes:
        "persistent_retries" : int — number of persistent retry attempts made
        "persistent_recovered" : int — symbols recovered during persistent retries

    Configuration
    ──────────────
    Controlled by module-level constants:
        _PERSISTENT_RETRY_ENABLED      — Enable/disable this feature
        _PERSISTENT_MAX_RETRIES        — Max retries per symbol (0 = unlimited)
        _PERSISTENT_RETRY_INTERVAL     — Wait time between retry waves (seconds)
        _PERSISTENT_BACKOFF_MULTIPLIER — Exponential backoff factor (1.0 = no backoff)

    Behavior
    ────────
    1. Run standard fetch_candles_bulk() — two-pass fetch with classification
    2. If failed count > 0 and persistent retry is enabled:
       a. Extract failed symbol list
       b. Retry each failed symbol with exponential backoff
       c. After each retry wave, re-classify any remaining failures
       d. Stop when: failed count = 0, max retries reached, or user interruption
    3. Merge results from persistent retries into final report
    """
    if not _PERSISTENT_RETRY_ENABLED:
        # Persistent retry disabled — just run standard fetch
        return fetch_candles_bulk(fyers, symbols, interval, verbose)

    # ── Step 1: Standard two-pass fetch and classification ──────────────────
    results, report = fetch_candles_bulk(fyers, symbols, interval, verbose)

    # ── Step 2: Persistent retry loop for failed symbols ────────────────────
    failed_symbols = report["missing"].copy()  # Start with no_data + failed
    no_data_set = set()
    retry_attempt = 0
    retry_interval = _PERSISTENT_RETRY_INTERVAL
    persistent_recovered = 0
    persistent_retries = 0

    while failed_symbols and (
        _PERSISTENT_MAX_RETRIES == 0 or retry_attempt < _PERSISTENT_MAX_RETRIES
    ):
        retry_attempt += 1
        failed_count = len(failed_symbols)

        print(
            f"   🔄  Persistent retry attempt {retry_attempt}/{_PERSISTENT_MAX_RETRIES if _PERSISTENT_MAX_RETRIES > 0 else '∞'} "
            f"({failed_count} symbols) — waiting {retry_interval:.1f}s …"
        )
        time.sleep(retry_interval)

        # Retry all currently-failed symbols
        newly_recovered = []
        still_failed = []

        for sym in failed_symbols:
            df = fetch_candles(fyers, sym)
            if df is not None:
                results[sym] = df
                newly_recovered.append(sym)
                persistent_recovered += 1
            else:
                # Classify this symbol to decide if it's no_data or still failing
                bucket = _classify_symbol(fyers, sym)
                if bucket == "no_data":
                    no_data_set.add(sym)
                else:
                    still_failed.append(sym)

        persistent_retries += 1

        if newly_recovered:
            print(
                f"   ✅  Persistent retry: recovered {len(newly_recovered)} symbols"
            )

        # Update state for next iteration
        failed_symbols = still_failed
        retry_interval *= _PERSISTENT_BACKOFF_MULTIPLIER

        if not failed_symbols:
            print(f"   ✅  All symbols recovered after {retry_attempt} persistent retries!")
            break
        else:
            print(f"   ⏳  {len(failed_symbols)} symbols still failing, will retry …")

    # ── Step 3: Generate updated report ────────────────────────────────────
    # Update the original report with persistent retry results
    report["failed"] = len(failed_symbols)
    report["no_data"] += len(no_data_set)
    report["persistent_retries"] = persistent_retries
    report["persistent_recovered"] = persistent_recovered
    report["missing"] = [s for s in symbols if s not in results]

    if persistent_recovered > 0:
        print(
            f"\n   📊  Persistent retry summary: "
            f"{persistent_recovered} recovered across {persistent_retries} retry attempt(s)"
        )

    return results, report


def fetch_candles_bulk_at_date(
    fyers   : fyersModel.FyersModel,
    symbols : list[str],
    range_to: datetime.date | None = None,
    verbose : bool = False,
) -> tuple[dict[str, pd.DataFrame], dict]:
    """
    Fetch candles for all symbols at a specific date (or today if range_to=None).

    This is a unified interface for both live and historical fetches.
    Uses the same persistent retry logic as fetch_candles_bulk_persistent().

    Parameters
    ----------
    fyers   : authenticated FyersModel instance
    symbols : list of Fyers-formatted symbols
    range_to: target date (None = today for live mode)
    verbose : if True, print per-symbol results

    Returns
    -------
    (results, report) — same format as fetch_candles_bulk()
        - "attempted"  : total symbols requested
        - "valid"      : symbols with usable data
        - "no_data"    : symbols with no Fyers history
        - "failed"     : symbols still failing after retries
        - "recovered"  : symbols recovered in standard 2-pass
        - "persistent_recovered" : additional recovered during persistent retries
        - "persistent_retries"   : number of persistent retry attempts
        - "missing"    : symbols not in results (no_data + failed)

    Usage
    ─────
    Live mode (today):
        results, report = fetch_candles_bulk_at_date(fyers, symbols)

    Historical mode (specific date):
        results, report = fetch_candles_bulk_at_date(fyers, symbols, range_to=datetime.date(2024, 11, 15))
    """
    if range_to is None:
        range_to = datetime.date.today()

    results      : dict[str, pd.DataFrame] = {}
    no_data      : list[str] = []
    to_retry     : list[str] = []
    total        = len(symbols)

    # ── Pass 1 ────────────────────────────────────────────────────────────────
    for i, sym in enumerate(symbols, 1):
        df = _fetch_windows(fyers, sym, range_to)
        if df is not None:
            results[sym] = df
        else:
            to_retry.append(sym)

        if i % 50 == 0 or i == total:
            print(
                f"   📥  {i}/{total} processed — "
                f"{len(results)} valid, {len(to_retry)} to retry …",
                end="\r",
            )
    print()

    # ── Pass 2 — retry once, then classify each remaining failure ─────────────
    recovered_count  = 0
    still_failed     : list[str] = []

    if to_retry:
        print(f"   🔄  Retrying {len(to_retry)} failed symbols …")
        time.sleep(_RETRY_PAUSE)

        for sym in to_retry:
            df = _fetch_windows(fyers, sym, range_to)
            if df is not None:
                results[sym] = df
                recovered_count += 1
            else:
                # Classify: structural missing vs transient error
                bucket = _classify_symbol(fyers, sym, range_to)
                if bucket == "no_data":
                    no_data.append(sym)
                else:
                    still_failed.append(sym)

        if recovered_count:
            print(f"   ✅  Recovered {recovered_count} on retry")
        reclassified = len(to_retry) - recovered_count - len(still_failed)
        if reclassified:
            print(f"   ℹ️   {reclassified} reclassified as no-data after retry")
        if still_failed:
            print(
                f"   ⚠️   {len(still_failed)} symbols failed both passes: "
                f"{still_failed[:5]}" + (" …" if len(still_failed) > 5 else "")
            )

    if no_data:
        print(f"   ℹ️   {len(no_data)} symbols skipped (no Fyers history — expected)")

    # ── Completeness report ───────────────────────────────────────────────────
    missing   = [s for s in symbols if s not in results]
    accounted = len(results) + len(no_data) + len(still_failed)

    if accounted != total:
        unaccounted = [
            s for s in symbols
            if s not in results and s not in no_data and s not in still_failed
        ]
        print(
            f"   ❌  COMPLETENESS BUG: {total - accounted} symbols unaccounted for "
            f"after both passes: {unaccounted[:10]}"
            + (" …" if len(unaccounted) > 10 else "")
        )
        still_failed.extend(unaccounted)

    # ── Step 2: Persistent retry loop for failed symbols ────────────────────
    failed_symbols = still_failed.copy()
    no_data_set = set(no_data)
    retry_attempt = 0
    retry_interval = _PERSISTENT_RETRY_INTERVAL
    persistent_recovered = 0
    persistent_retries = 0

    while failed_symbols and _PERSISTENT_RETRY_ENABLED and (
        _PERSISTENT_MAX_RETRIES == 0 or retry_attempt < _PERSISTENT_MAX_RETRIES
    ):
        retry_attempt += 1
        failed_count = len(failed_symbols)

        print(
            f"   🔄  Persistent retry attempt {retry_attempt}/{_PERSISTENT_MAX_RETRIES if _PERSISTENT_MAX_RETRIES > 0 else '∞'} "
            f"({failed_count} symbols) — waiting {retry_interval:.1f}s …"
        )
        time.sleep(retry_interval)

        newly_recovered = []
        still_failed_after = []

        for sym in failed_symbols:
            df = _fetch_windows(fyers, sym, range_to)
            if df is not None:
                results[sym] = df
                newly_recovered.append(sym)
                persistent_recovered += 1
            else:
                bucket = _classify_symbol(fyers, sym, range_to)
                if bucket == "no_data":
                    no_data_set.add(sym)
                else:
                    still_failed_after.append(sym)

        persistent_retries += 1

        if newly_recovered:
            print(
                f"   ✅  Persistent retry: recovered {len(newly_recovered)} symbols"
            )

        failed_symbols = still_failed_after
        retry_interval *= _PERSISTENT_BACKOFF_MULTIPLIER

        if not failed_symbols:
            print(f"   ✅  All symbols recovered after {retry_attempt} persistent retries!")
            break
        else:
            print(f"   ⏳  {len(failed_symbols)} symbols still failing, will retry …")

    # ── Final report ──────────────────────────────────────────────────────────
    report = {
        "attempted" : total,
        "valid"     : len(results),
        "no_data"   : len(no_data_set),
        "failed"    : len(failed_symbols),
        "recovered" : recovered_count,
        "persistent_recovered": persistent_recovered,
        "persistent_retries": persistent_retries,
        "missing"   : [s for s in symbols if s not in results],
    }

    if persistent_recovered > 0:
        print(
            f"\n   📊  Persistent retry summary: "
            f"{persistent_recovered} recovered across {persistent_retries} retry attempt(s)"
        )

    return results, report

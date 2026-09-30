"""
data/candles.py
───────────────
Fetches daily OHLCV candle data from Fyers API v3.

Key facts about Fyers daily data:
  - Hard cap: 366 calendar days per request (~249 trading days).
  - Two windows per symbol (W2 = recent, W1 = older warm-up) give ~500 bars so
    EMA/MACD values match TradingView.

Completeness rules (why a stock can be left out — and ONLY these reasons)
─────────────────────────────────────────────────────────────────────────
Every symbol of the universe ends in exactly one bucket, with a recorded reason:

    valid          usable candles were received (includes short-history stocks,
                   which are evaluated and reported as "not enough history")
    no_data        Fyers confirmed it has no history: every series
                   (-EQ/-BE/-BZ/-SM/-ST) answered "invalid symbol", or the
                   requested period is empty; OR the newest candle is older
                   than STALE_BAR_MAX_DAYS (suspended / halted stock)
    failed         Fyers kept returning errors (rate limit / 5xx / network /
                   auth) through every retry wave.  The Fyers code + message are
                   recorded per symbol as evidence that the failure is Fyers-side.

valid + no_data + failed == attempted (always).

What changed vs. the old pipeline (all of it REDUCES Fyers calls)
──────────────────────────────────────────────────────────────────
  * The recent window (W2) is fetched FIRST.  A stock is never evaluated on
    year-old data because W2 failed silently.
  * W1 is skipped when it cannot contain data (stock listed inside W2, or W2 empty).
  * Alternate suffixes (-BE/-BZ/-SM/-ST) are probed only after Fyers DEFINITIVELY
    said the symbol is invalid/empty — never after a transient error.
  * A transient failure keeps its already-fetched W2 frame, so a retry re-requests
    only the window that failed.
  * Unified retry waves with growing pauses; a global pacer slows down after a
    429 instead of hammering the same limit.
  * Short-history stocks are kept and evaluated instead of being retried.
  * Live scans may skip a symbol already CONFIRMED invalid within the last day
    (symbol_cache.py); backtests never do.
"""

import datetime
import time
import threading

import pandas as pd
from fyers_apiv3 import fyersModel

from config.settings import STALE_BAR_MAX_DAYS
from data.symbol_cache import SYMBOL_CACHE
from utils.scan_control import (
    FyersAuthError, ScanCancelled, check_cancel, sleep_cancellable,
)

# ── Constants ─────────────────────────────────────────────────────────────────
_WINDOW_DAYS   = 366          # calendar days per Fyers request (hard cap)
_NUM_WINDOWS   = 2            # kept for callers that size their look-back from it
_MIN_BARS      = 80           # below this a stock is "short history" (still evaluated)
_SLEEP         = 0.12         # minimum pause between requests (~8 req/s; Fyers limit: 10)
_ALT_SUFFIXES  = ["-BE", "-BZ", "-SM", "-ST"]   # fallback suffixes for -EQ
_RATE_LIMIT_RETRIES = 6       # in-request retries on HTTP 429 only
_RATE_LIMIT_PAUSE = 2.0       # grows linearly: 2, 4, 6 … seconds
_INVALID_SYMBOL_CODE = -300
_RATE_LIMIT_CODE = 429
_AUTH_CODES = {-8, -15, -16, -17, 401, 403}
_AUTH_ABORT_AFTER = 8         # consecutive auth rejections before the scan aborts

# Retry waves for transient failures (Fyers errors / rate limits).
_PERSISTENT_MAX_RETRIES = 6
_PERSISTENT_RETRY_INTERVAL = 3.0       # pause before wave 1, then × multiplier
_PERSISTENT_BACKOFF_MULTIPLIER = 1.6


def _response_code(resp: dict | None) -> int | None:
    try:
        return int(resp.get("code")) if resp and resp.get("code") is not None else None
    except (TypeError, ValueError):
        return None


# ── Pacing (adaptive, global) ─────────────────────────────────────────────────
class _Pacer:
    """Spaces requests; widens the gap after a 429 and relaxes again when calm."""

    def __init__(self, base: float):
        self._base = base
        self._interval = base
        self._next = 0.0
        self._streak = 0
        self._lock = threading.Lock()

    def before(self, cancel=None) -> None:
        with self._lock:
            wait = self._next - time.monotonic()
        if wait > 0:
            sleep_cancellable(wait, cancel)

    def after(self) -> None:
        with self._lock:
            self._next = max(self._next, time.monotonic() + self._interval)

    def rate_limited(self, pause: float) -> None:
        with self._lock:
            self._interval = min(self._interval * 1.5, 1.0)
            self._next = max(self._next, time.monotonic() + pause)
            self._streak = 0

    def ok(self) -> None:
        with self._lock:
            self._streak += 1
            if self._streak >= 25 and self._interval > self._base:
                self._interval = max(self._base, self._interval * 0.9)
                self._streak = 0


_PACER = _Pacer(_SLEEP)
_auth_fail_streak = 0


class _Win:
    __slots__ = ("df", "status", "code", "message")

    def __init__(self, df=None, status="failed", code=None, message=""):
        self.df, self.status, self.code, self.message = df, status, code, message


def _request_window(
    fyers: fyersModel.FyersModel,
    symbol: str,
    range_from: datetime.date,
    range_to: datetime.date,
    cancel: threading.Event | None = None,
) -> _Win:
    """
    One Fyers history request.  status: ok | empty | invalid_symbol | failed.
    Retries only on HTTP 429.  Raises ScanCancelled / FyersAuthError.
    """
    global _auth_fail_streak
    payload = {
        "symbol": symbol,
        "resolution": "1D",
        "date_format": "1",
        "range_from": range_from.strftime("%Y-%m-%d"),
        "range_to": range_to.strftime("%Y-%m-%d"),
        "cont_flag": "1",
    }
    last = _Win(status="failed", message="no response")

    for attempt in range(_RATE_LIMIT_RETRIES + 1):
        check_cancel(cancel)
        _PACER.before(cancel)
        try:
            resp = fyers.history(data=payload)
        except Exception as exc:          # network / SDK failure
            _PACER.after()
            return _Win(status="failed", message=f"{type(exc).__name__}: {exc}"[:200])
        _PACER.after()

        code = _response_code(resp)
        msg = str((resp or {}).get("message", "") or "")[:200] if isinstance(resp, dict) else "bad response"

        if code == _RATE_LIMIT_CODE:
            _PACER.rate_limited(_RATE_LIMIT_PAUSE * (attempt + 1))
            last = _Win(status="failed", code=code, message=msg or "rate limited (429)")
            continue

        if code == _INVALID_SYMBOL_CODE:
            _auth_fail_streak = 0
            return _Win(status="invalid_symbol", code=code, message=msg)

        if code in _AUTH_CODES:
            _auth_fail_streak += 1
            if _auth_fail_streak >= _AUTH_ABORT_AFTER:
                raise FyersAuthError(f"Fyers rejected the session (code {code}: {msg})")
            return _Win(status="failed", code=code, message=msg or "auth rejected")
        _auth_fail_streak = 0

        if not isinstance(resp, dict):
            return _Win(status="failed", message="bad response")

        status_txt = resp.get("s")
        if status_txt == "no_data":
            _PACER.ok()
            return _Win(status="empty", code=code, message=msg)
        if status_txt != "ok":
            return _Win(status="failed", code=code, message=msg or str(resp)[:160])

        candles = resp.get("candles")
        _PACER.ok()
        if not candles:
            return _Win(status="empty", code=code, message=msg)

        df = pd.DataFrame(candles, columns=["Timestamp", "Open", "High", "Low", "Close", "Volume"])
        df["Timestamp"] = pd.to_datetime(df["Timestamp"], unit="s", utc=True)
        df["Timestamp"] = df["Timestamp"].dt.tz_convert("Asia/Kolkata").dt.tz_localize(None).dt.normalize()
        df.set_index("Timestamp", inplace=True)
        df = df[~df.index.duplicated(keep="last")].sort_index()
        return _Win(df=df, status="ok", code=code, message=msg)

    return last


def _request_history_window(fyers, symbol, range_from, range_to):
    """Compatibility wrapper → (df | None, status)."""
    w = _request_window(fyers, symbol, range_from, range_to)
    return w.df, w.status


# ── One symbol, one suffix ────────────────────────────────────────────────────
class _Fetch:
    __slots__ = ("status", "df", "code", "message", "last_bar")

    def __init__(self, status, df=None, code=None, message="", last_bar=None):
        self.status, self.df, self.code, self.message, self.last_bar = status, df, code, message, last_bar


def _fetch_history(fyers, symbol, range_to, partial, cancel, allow_w2_only=False) -> _Fetch:
    """
    status: ok | empty | invalid | stale | failed
    `partial` maps symbol → already-fetched W2 frame (kept across retries).
    With allow_w2_only a failed W1 no longer fails the stock (last-resort wave).
    """
    w2_from = range_to - datetime.timedelta(days=_WINDOW_DAYS)
    w2df = partial.get(symbol)
    if w2df is None:
        w2 = _request_window(fyers, symbol, w2_from, range_to, cancel)
        if w2.status == "invalid_symbol":
            return _Fetch("invalid", code=w2.code, message=w2.message)
        if w2.status == "failed":
            return _Fetch("failed", code=w2.code, message=w2.message)
        if w2.status == "empty":
            return _Fetch("empty", code=w2.code, message=w2.message)
        w2df = w2.df
        partial[symbol] = w2df

    frames = [w2df]
    # W1 can only hold data if W2 already reaches back to (almost) its own start.
    needs_w1 = w2df.index.min() <= pd.Timestamp(w2_from) + pd.Timedelta(days=10)
    if needs_w1 and _NUM_WINDOWS > 1:
        w1_to = w2_from
        w1_from = w1_to - datetime.timedelta(days=_WINDOW_DAYS)
        w1 = _request_window(fyers, symbol, w1_from, w1_to, cancel)
        if w1.status == "ok":
            frames.append(w1.df)
        elif w1.status == "failed" and not allow_w2_only:
            return _Fetch("failed", code=w1.code, message=f"older window: {w1.message}")
        # invalid/empty W1 → no older history exists; W2 alone is all there is.

    df = pd.concat(frames).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    df = df[df.index >= pd.Timestamp(range_to - datetime.timedelta(days=_WINDOW_DAYS * _NUM_WINDOWS))]
    df = df[df.index <= pd.Timestamp(range_to)]
    if df.empty:
        return _Fetch("empty")

    last_bar = df.index[-1].date()
    if (range_to - last_bar).days > STALE_BAR_MAX_DAYS:
        return _Fetch("stale", last_bar=last_bar,
                      message=f"newest candle is {last_bar.isoformat()}")
    return _Fetch("ok", df=df, last_bar=last_bar)


# ── One symbol, all suffixes ─────────────────────────────────────────────────
class _Outcome:
    __slots__ = ("status", "df", "code", "message", "last_bar", "resolved", "cached", "degraded")

    def __init__(self, status, df=None, code=None, message="", last_bar=None,
                 resolved=None, cached=False, degraded=False):
        self.status, self.df, self.code, self.message = status, df, code, message
        self.last_bar, self.resolved, self.cached, self.degraded = last_bar, resolved, cached, degraded


def _candidates(symbol: str) -> list[str]:
    if symbol.endswith("-EQ"):
        base = symbol[:-3]
        cands = [symbol] + [base + s for s in _ALT_SUFFIXES]
    else:
        cands = [symbol]
    hint = SYMBOL_CACHE.hint(symbol)
    if hint and hint in cands:
        cands.remove(hint)
        cands.insert(0, hint)
    return cands


def _fetch_symbol(fyers, symbol, range_to, partial, cancel, live, allow_w2_only=False) -> _Outcome:
    """status: ok | no_data | stale | failed"""
    if live:
        cached = SYMBOL_CACHE.no_data_entry(symbol)
        if cached:
            return _Outcome("no_data", cached=True,
                            message=f"Fyers confirmed no data within the last day ({cached.get('detail', '')})")

    cands = _candidates(symbol)
    invalid = 0
    stale: _Fetch | None = None
    for cand in cands:
        r = _fetch_history(fyers, cand, range_to, partial, cancel, allow_w2_only)
        if r.status == "ok":
            SYMBOL_CACHE.record_resolved(symbol, cand)
            return _Outcome("ok", df=r.df, last_bar=r.last_bar, resolved=cand)
        if r.status == "failed":
            # Transient Fyers problem: never probe alternates, retry later.
            return _Outcome("failed", code=r.code, message=r.message)
        if r.status == "invalid":
            invalid += 1
        elif r.status == "stale" and stale is None:
            stale = r

    if stale is not None:
        return _Outcome("stale", last_bar=stale.last_bar, message=stale.message)
    if invalid == len(cands):
        detail = f"code {_INVALID_SYMBOL_CODE} on {', '.join(c.split('-')[-1] for c in cands)}"
        if live:
            SYMBOL_CACHE.record_no_data(symbol, detail)
        return _Outcome("no_data", message=f"Fyers reports this symbol as invalid ({detail})")
    return _Outcome("no_data", message="Fyers returned no candles for the requested period")


# ── Whole universe ───────────────────────────────────────────────────────────
def _bare(symbol: str) -> str:
    return symbol.replace("NSE:", "").replace("-EQ", "")


def _fetch_universe(
    fyers, symbols, range_to, *, live, progress=None, cancel=None, verbose=False,
) -> tuple[dict[str, pd.DataFrame], dict]:
    global _auth_fail_streak
    _auth_fail_streak = 0

    unique = list(dict.fromkeys(symbols))
    total = len(unique)
    results: dict[str, pd.DataFrame] = {}
    no_data: dict[str, _Outcome] = {}
    failed: dict[str, _Outcome] = {}
    partial: dict[str, pd.DataFrame] = {}
    degraded: list[str] = []
    ledger: dict[str, dict] = {}
    recovered = 0
    waves = 0

    if progress:
        try:
            progress.set_total(total)
        except Exception:
            pass

    def _settle(sym: str, out: _Outcome) -> str:
        if out.status == "ok":
            results[sym] = out.df
            failed.pop(sym, None)
            partial.pop(out.resolved, None)
            return "valid"
        if out.status in ("no_data", "stale"):
            no_data[sym] = out
            failed.pop(sym, None)
            return "no_data"
        failed[sym] = out
        return "failed"

    # ── pass 1 ────────────────────────────────────────────────────────────────
    for i, sym in enumerate(unique, 1):
        check_cancel(cancel)
        out = _fetch_symbol(fyers, sym, range_to, partial, cancel, live)
        bucket = _settle(sym, out)
        if progress:
            try:
                progress.fetch_result(sym, bucket == "valid", outcome=bucket)
            except Exception:
                pass
        if verbose and i % 50 == 0:
            print(f"  📥  {i}/{total} processed — {len(results)} valid, {len(failed)} to retry")

    # ── retry waves (transient Fyers failures only) ──────────────────────────
    interval = _PERSISTENT_RETRY_INTERVAL
    while failed and waves < _PERSISTENT_MAX_RETRIES:
        waves += 1
        pending = list(failed)
        if progress:
            try:
                progress.begin_retry(len(pending), waves)
            except Exception:
                pass
        if verbose:
            print(f"  🔁  Retry wave {waves}: {len(pending)} symbols (pause {interval:.0f}s)")
        sleep_cancellable(interval, cancel)
        last_wave = waves == _PERSISTENT_MAX_RETRIES
        for sym in pending:
            check_cancel(cancel)
            out = _fetch_symbol(fyers, sym, range_to, partial, cancel, live, allow_w2_only=last_wave)
            bucket = _settle(sym, out)
            if bucket == "valid":
                recovered += 1
            if progress:
                try:
                    progress.retry_result(sym, bucket)
                except Exception:
                    pass
        interval *= _PERSISTENT_BACKOFF_MULTIPLIER

    # ── ledger ────────────────────────────────────────────────────────────────
    for sym, df in results.items():
        bars = len(df)
        if bars < _MIN_BARS:
            ledger[_bare(sym)] = {
                "status": "short_history", "bars": bars,
                "category": "Not evaluated — not enough price history",
                "detail": f"Only {bars} daily candles exist (need at least {_MIN_BARS}); recently listed.",
            }
    for sym, out in no_data.items():
        if out.status == "stale":
            ledger[_bare(sym)] = {
                "status": "stale_data", "last_bar": out.last_bar.isoformat() if out.last_bar else None,
                "category": "Not analysed — price data is stale",
                "detail": (f"Newest candle is {out.last_bar.isoformat() if out.last_bar else 'unknown'}, more than "
                           f"{STALE_BAR_MAX_DAYS} days before the scan date (suspended or halted). "
                           "Not evaluated so old prices cannot produce a false signal."),
            }
        else:
            ledger[_bare(sym)] = {
                "status": "no_data",
                "category": "Not analysed — no price data from Fyers",
                "detail": out.message + ". Not a judgement on the stock's chart.",
            }
    for sym, out in failed.items():
        ledger[_bare(sym)] = {
            "status": "fetch_failed", "code": out.code,
            "category": "Not analysed — Fyers did not return data",
            "detail": (f"Fyers kept failing after {1 + waves} attempts "
                       f"(code {out.code}: {out.message or 'no message'}). Fyers-side failure; "
                       "press Rescan to try again."),
        }

    SYMBOL_CACHE.save()

    report = {
        "attempted": total,
        "duplicates_removed": len(symbols) - total,
        "valid": len(results),
        "no_data": len(no_data),
        "failed": len(failed),
        "recovered": recovered,
        "persistent_retries": waves,
        "persistent_recovered": recovered,
        "missing": sorted(_bare(s) for s in list(no_data) + list(failed)),
        "failed_symbols": sorted(_bare(s) for s in failed),
        "short_history": sum(1 for v in ledger.values() if v["status"] == "short_history"),
        "stale": sum(1 for v in ledger.values() if v["status"] == "stale_data"),
        "cached_no_data": sum(1 for o in no_data.values() if o.cached),
        "ledger": ledger,
    }
    assert report["valid"] + report["no_data"] + report["failed"] == total
    return results, report


# ── Public API ────────────────────────────────────────────────────────────────
def fetch_candles_bulk_persistent(fyers, symbols, interval="1D", verbose=False, progress=None, cancel=None):
    """Live scan fetch (range_to = today).  Returns (results, report)."""
    return _fetch_universe(
        fyers, symbols, datetime.date.today(),
        live=True, progress=progress, cancel=cancel, verbose=verbose,
    )


def fetch_candles_bulk(fyers, symbols, interval="1D", verbose=False, progress=None, cancel=None):
    return fetch_candles_bulk_persistent(fyers, symbols, interval, verbose, progress, cancel)


def fetch_candles_bulk_at_date(
    fyers, symbols, range_to, interval="1D", verbose=False, progress=None, cancel=None,
):
    """Backtest fetch for a past date.  Never reads/writes the no-data cache."""
    return _fetch_universe(
        fyers, symbols, range_to,
        live=False, progress=progress, cancel=cancel, verbose=verbose,
    )


def fetch_candles(fyers: fyersModel.FyersModel, symbol: str) -> pd.DataFrame | None:
    """Single-symbol convenience wrapper (used by ad-hoc tools)."""
    out = _fetch_symbol(fyers, symbol, datetime.date.today(), {}, None, live=False)
    return out.df if out.status == "ok" else None


def fetch_intraday_candles(
    fyers: fyersModel.FyersModel,
    symbol: str,
    target_date: datetime.date,
    resolution: str = "1",
) -> pd.DataFrame | None:
    """
    Fetch intraday candles for one symbol on a single calendar day.

    Used only to reconstruct the exact IST time a stock became Trade Ready
    (see scanner/engine.py::_reconstruct_trade_ready_time) — NOT used by the
    main daily scan, which stays entirely on "D" resolution as before.

    resolution="1" (1-minute bars) so the reconstructed timestamp can be
    accurate to the minute, per the requirement.

    Returns a DataFrame indexed by naive IST wall-clock timestamps (matching
    the app's fixed +5:30 offset convention — no pytz/zoneinfo dependency),
    or None if no intraday data is available (e.g. request made before the
    exchange has produced any bars, or a transient/invalid-symbol failure).
    """
    for attempt in range(_RATE_LIMIT_RETRIES + 1):
        try:
            resp = fyers.history(
                data={
                    "symbol": symbol,
                    "resolution": resolution,
                    "date_format": "1",
                    "range_from": target_date.strftime("%Y-%m-%d"),
                    "range_to": target_date.strftime("%Y-%m-%d"),
                    "cont_flag": "1",
                }
            )
        except Exception:
            resp = None

        time.sleep(_SLEEP)

        if resp and resp.get("s") == "ok":
            candles = resp.get("candles", [])
            if not candles:
                return None

            df = pd.DataFrame(
                candles,
                columns=["Timestamp", "Open", "High", "Low", "Close", "Volume"],
            )
            # Fyers intraday timestamps are Unix epoch seconds (UTC).
            # Convert to naive IST wall-clock time via the fixed +5:30
            # offset, same convention used throughout the rest of the app.
            df["Timestamp"] = (
                pd.to_datetime(df["Timestamp"], unit="s", utc=True)
                + pd.Timedelta(hours=5, minutes=30)
            ).dt.tz_localize(None)
            df.set_index("Timestamp", inplace=True)
            df = df[["Open", "High", "Low", "Close", "Volume"]].astype(float)
            df.sort_index(inplace=True)
            return df

        code = _response_code(resp)
        if code == _INVALID_SYMBOL_CODE:
            return None
        if code == _RATE_LIMIT_CODE and attempt < _RATE_LIMIT_RETRIES:
            time.sleep(_RATE_LIMIT_PAUSE * (attempt + 1))
            continue

        return None

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


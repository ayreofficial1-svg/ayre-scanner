"""
data/quotes.py
──────────────
Bulk live-price (LTP) fetcher for a small set of symbols.

Used by main.py to attach current prices to signal and watchlist results
via the /api/quotes endpoint, independently of the main scan cycle.

Design
──────
- Fyers bulk quotes accept up to 50 symbols per call (comma-separated).
  Signals + watchlist combined are well under this limit, so this is
  always a single API call regardless of how many stocks are active.

- A minimum interval guard (QUOTES_MIN_INTERVAL) prevents the background
  poller and any direct calls from firing more than one Fyers request
  per interval, regardless of how often the frontend polls /api/quotes.

- Returns {fyers_symbol: ltp} — caller maps these back to display symbols.
  Symbols missing from the response get None so callers can handle gracefully.

- Failures are fully isolated: a failed quote fetch returns an empty dict
  rather than raising, so the rest of the application is unaffected.

- Constituent index pages use fetch_constituents_quotes_bulk: separate rate-limit
  and cache from fetch_ltp_bulk so /api/quotes for the scanner is never starved.
  Supports up to 100 symbols via two batched calls (50 + 50) with a short
  pause between batches.
"""

import time
import threading
import logging
import requests
from fyers_apiv3 import fyersModel

log = logging.getLogger(__name__)

# Minimum seconds between actual Fyers API calls.
# Background poller runs every 15s; this guard ensures rapid /api/quotes
# hits from the frontend never trigger more than one real fetch per interval.
QUOTES_MIN_INTERVAL = 15.0

_lock          = threading.Lock()
_last_fetch_at = 0.0
_last_result   : dict[str, float | None] = {}

# Constituents / index composition — isolated from scanner LTP polling.
_CQ_MIN_INTERVAL   = 4.0
_cq_lock           = threading.Lock()
_cq_last_at        = 0.0
_cq_last_frozen    : frozenset | None = None
_cq_last_quotes    : dict[str, dict[str, float | None]] = {}


def fetch_ltp_bulk(
    fyers        : fyersModel.FyersModel,
    fyers_symbols: list[str],
) -> dict[str, float | None]:
    """
    Fetch the latest traded price for each symbol in a single Fyers quotes call.

    Parameters
    ----------
    fyers         : authenticated FyersModel instance
    fyers_symbols : list of full Fyers symbol strings, e.g. ["NSE:ONGC-EQ"]
                    Maximum 50 symbols (Fyers hard limit per call).

    Returns
    -------
    dict mapping fyers_symbol → LTP (float) or None if symbol was missing
    from the response. Returns empty dict on total failure.
    """
    global _last_fetch_at, _last_result

    if not fyers_symbols:
        return {}

    now = time.time()

    with _lock:
        # Rate limit guard — return cached result if called too soon
        if now - _last_fetch_at < QUOTES_MIN_INTERVAL and _last_result:
            return dict(_last_result)

        symbols_str = ",".join(fyers_symbols[:50])   # Fyers hard cap

        try:
            resp = fyers.quotes(data={"symbols": symbols_str})
        except Exception as e:
            log.warning(f"quotes.fetch_ltp_bulk: Fyers API error: {e}")
            # Reset the fetch timestamp so the next call actually retries
            # rather than being blocked by the interval guard indefinitely.
            _last_fetch_at = 0.0
            return dict(_last_result) if _last_result else {}

        if not resp or resp.get("s") != "ok":
            log.warning(f"quotes.fetch_ltp_bulk: bad response: {resp}")
            # Detect rate-limit responses (code -50 or message containing "limit")
            # and reset so recovery happens on the very next poller tick.
            msg = str(resp.get("message") or resp.get("errmsg") or "").lower()
            code = resp.get("code", 0)
            if code == -50 or "limit" in msg or "429" in msg:
                log.warning("quotes.fetch_ltp_bulk: rate limit detected — resetting fetch timer for recovery")
                _last_fetch_at = 0.0
            return dict(_last_result) if _last_result else {}

        result: dict[str, float | None] = {s: None for s in fyers_symbols}

        for item in resp.get("d", []):
            # Fyers returns symbol under "n" in quotes response
            sym = str(item.get("n") or item.get("symbol") or "")
            v   = item.get("v") or item
            ltp = _pick_ltp(v)
            if sym in result and ltp is not None:
                result[sym] = ltp

        _last_fetch_at = time.time()
        _last_result   = result
        return dict(result)


def _pick_ltp(values: dict) -> float | None:
    """Extract LTP from a Fyers quote values dict, trying multiple field names."""
    for key in ("lp", "ltp", "last_price", "close_price"):
        raw = values.get(key)
        if raw is None:
            continue
        try:
            return round(float(raw), 2)
        except (TypeError, ValueError):
            continue
    return None


def fetch_constituents_quotes_bulk(
    fyers: fyersModel.FyersModel,
    nse_symbols: list[str],
) -> dict[str, dict[str, float | None]]:
    """
    OHLC + change for many NSE equities in few Fyers calls (50 symbols max each).
    Falls back to Yahoo Finance if Fyers returns no usable prices.

    Parameters
    ----------
    nse_symbols : bare symbols, e.g. ["RELIANCE", "TCS"]

    Returns
    -------
    Uppercase NSE symbol → {last_price, high, low, open, change_pct,
    change_points, volume} (values may be None).
    """
    global _cq_last_at, _cq_last_frozen, _cq_last_quotes

    if not nse_symbols:
        return {}

    norm = sorted({s.strip().upper() for s in nse_symbols if s and str(s).strip()})
    if not norm:
        return {}

    fs  = frozenset(norm)
    now = time.time()
    with _cq_lock:
        if fs == _cq_last_frozen and now - _cq_last_at < _CQ_MIN_INTERVAL and _cq_last_quotes:
            return {k: dict(v) for k, v in _cq_last_quotes.items()}

    merged: dict[str, dict[str, float | None]] = {s: {} for s in norm}
    fyers_hit = False

    # ── Primary: Fyers bulk quotes ────────────────────────────────────────────
    if fyers is not None:
        batches = [norm[i : i + 50] for i in range(0, len(norm), 50)]
        try:
            for bi, batch in enumerate(batches):
                if bi:
                    time.sleep(0.35)
                fyers_syms  = [f"NSE:{s}-EQ" for s in batch]
                symbols_str = ",".join(fyers_syms)
                resp = fyers.quotes(data={"symbols": symbols_str})
                if not resp or resp.get("s") != "ok":
                    log.warning("quotes.fetch_constituents_quotes_bulk: bad batch response")
                    continue

                for item in resp.get("d", []):
                    sym_full = str(item.get("n") or item.get("symbol") or "")
                    v        = item.get("v") or item
                    if not isinstance(v, dict):
                        continue
                    bare = _fyers_symbol_to_nse(sym_full)
                    if bare not in merged:
                        continue
                    row = _parse_fyers_quote_row(v)
                    if row.get("last_price") is not None:
                        merged[bare] = row
                        fyers_hit = True
        except Exception as e:
            log.warning(f"quotes.fetch_constituents_quotes_bulk Fyers error: {e}")

    # ── Fallback: Yahoo Finance for any symbol still missing a price ──────────
    # Covers symbols Fyers rejected or when Fyers is unavailable entirely.
    missing = [s for s in norm if not merged.get(s, {}).get("last_price")]
    if missing:
        yahoo_result = _fetch_yahoo_quotes(missing)
        for sym, row in yahoo_result.items():
            if row.get("last_price") is not None:
                merged[sym] = row

    with _cq_lock:
        _cq_last_at     = time.time()
        _cq_last_frozen = fs
        _cq_last_quotes = merged

    return {k: dict(v) for k, v in merged.items()}


def _fetch_yahoo_quotes(nse_symbols: list[str]) -> dict[str, dict[str, float | None]]:
    """
    Fetch OHLC data from Yahoo Finance for a list of NSE symbols.
    Yahoo uses '.NS' suffix for NSE equities (e.g. RELIANCE.NS).
    Batches into groups of 50 to stay within URL length limits.
    Returns {} on total failure — never raises.
    """
    result: dict[str, dict[str, float | None]] = {}
    if not nse_symbols:
        return result

    batches = [nse_symbols[i: i + 50] for i in range(0, len(nse_symbols), 50)]
    for batch in batches:
        yahoo_syms = [f"{s}.NS" for s in batch]
        try:
            resp = requests.get(
                "https://query1.finance.yahoo.com/v7/finance/quote",
                params={"symbols": ",".join(yahoo_syms)},
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            resp.raise_for_status()
            items = resp.json().get("quoteResponse", {}).get("result", [])
            for item in items:
                yahoo_sym = str(item.get("symbol") or "")
                if not yahoo_sym.endswith(".NS"):
                    continue
                bare = yahoo_sym[:-3].upper()   # strip .NS suffix
                lp   = item.get("regularMarketPrice")
                result[bare] = {
                    "last_price"    : round(float(lp), 2) if lp is not None else None,
                    "high"          : _safe_float(item.get("regularMarketDayHigh")),
                    "low"           : _safe_float(item.get("regularMarketDayLow")),
                    "open"          : _safe_float(item.get("regularMarketOpen")),
                    "change_pct"    : _safe_float(item.get("regularMarketChangePercent")),
                    "change_points" : _safe_float(item.get("regularMarketChange")),
                    "volume"        : _safe_float(item.get("regularMarketVolume")),
                }
        except Exception as e:
            log.warning(f"quotes._fetch_yahoo_quotes: {e}")
            continue

    return result


def _safe_float(v) -> float | None:
    if v is None:
        return None
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _fyers_symbol_to_nse(sym_full: str) -> str:
    s = sym_full.upper().strip()
    if ":" in s:
        s = s.split(":")[-1]
    if s.endswith("-EQ"):
        s = s[:-3]
    return s.strip()


def _parse_fyers_quote_row(v: dict) -> dict[str, float | None]:
    return {
        "last_price"    : _pick_num(v, "lp", "ltp", "last_price"),
        "high"          : _pick_num(v, "high_price", "high", "day_high"),
        "low"           : _pick_num(v, "low_price", "low", "day_low"),
        "open"          : _pick_num(v, "open_price", "open"),
        "change_pct"    : _pick_num(v, "chp", "change_percent", "change_pct"),
        "change_points" : _pick_num(v, "ch", "change", "chn"),
        "volume"        : _pick_num(v, "volume", "vol_traded_today", "vol"),
    }


def _pick_num(values: dict, *keys: str) -> float | None:
    for key in keys:
        raw = values.get(key)
        if raw is None:
            continue
        try:
            return round(float(str(raw).replace(",", "")), 2)
        except (TypeError, ValueError):
            continue
    return None
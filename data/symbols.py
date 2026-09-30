"""
data/symbols.py
───────────────
Fetches stock lists from NSE India.

Returns symbols in priority order:
  1. Nifty 50  (50 stocks)  — most liquid, highest priority
  2. Nifty 500 remainder    (450 stocks) — scanned after Nifty 50

This ensures the most important stocks are always analysed first,
even if the scan is interrupted or rate-limited midway.

All symbols returned in Fyers format: NSE:SYMBOL-EQ
"""

import datetime
import io
import json
import os
import threading

import requests
import pandas as pd

NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

# NSE API endpoints
_URL_NIFTY50     = "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%2050"
_URL_NIFTY500    = "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%20500"
_URL_NIFTY_BANK  = "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%20BANK"
_URL_NIFTY_NEXT50 = "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%20NEXT%2050"

# NSE CSV fallbacks
_CSV_NIFTY50      = "https://archives.nseindia.com/content/indices/ind_nifty50list.csv"
_CSV_NIFTY500     = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
_CSV_NIFTY_BANK   = "https://archives.nseindia.com/content/indices/ind_niftybanklist.csv"
_CSV_NIFTY_NEXT50 = "https://archives.nseindia.com/content/indices/ind_niftynext50list.csv"

# Hardcoded Nifty 50 fallback (always up to date enough for daily swing scanning)
_NIFTY50_FALLBACK = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
    "HINDUNILVR", "SBIN", "BHARTIARTL", "ITC", "KOTAKBANK",
    "LT", "AXISBANK", "ASIANPAINT", "MARUTI", "TITAN",
    "SUNPHARMA", "ULTRACEMCO", "BAJFINANCE", "WIPRO", "HCLTECH",
    "POWERGRID", "ONGC", "NTPC", "TECHM", "BAJAJFINSV",
    "NESTLEIND", "JSWSTEEL", "TATAMOTORS", "ADANIENT", "INDUSINDBK",
    "DRREDDY", "CIPLA", "DIVISLAB", "GRASIM", "BPCL",
    "COALINDIA", "BRITANNIA", "HEROMOTOCO", "SHREECEM", "TATACONSUM",
    "ADANIPORTS", "HINDALCO", "EICHERMOT", "M&M", "BAJAJ-AUTO",
    "APOLLOHOSP", "DABUR", "PIDILITIND", "SBILIFE", "HDFCLIFE",
]

# Bank Nifty basket (12) — used when API/CSV are unavailable.
_NIFTY_BANK_FALLBACK = [
    "HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK", "INDUSINDBK",
    "BANKBARODA", "FEDERALBNK", "IDFCFIRSTB", "PNB", "AUBANK", "BANDHANBNK",
]

# Nifty Next 50 — kept only as a generic large-cap NSE basket; NOT used for
# Sensex any more (see §4.1 fix below). Retained in case another feature
# wants a Next-50 style universe later.
_NIFTY_NEXT50_FALLBACK = [
    "ABB", "ADANIGREEN", "ADANITRANS", "AMBUJACEM", "APOLLOHOSP",
    "AUROPHARMA", "BAJAJHLDNG", "BERGEPAINT", "BIOCON", "BOSCHLTD",
    "CHOLAFIN", "COLPAL", "CONCOR", "DALBHARAT", "DEEPAKNTR",
    "DIVISLAB", "DLF", "DRREDDY", "GAIL", "GLENMARK",
    "GODREJCP", "HAVELLS", "HDFCAMC", "HDFCLIFE", "ICICIGI",
    "ICICIPRULI", "IOC", "LICHSGFIN", "LTIM", "MARICO",
    "MCDOWELL-N", "MRF", "NMDC", "PETRONET", "PIIND",
    "POLYCAB", "RECLTD", "SIEMENS", "SRF", "SUNTV",
    "TORNTPHARM", "TRENT", "TVSMOTOR", "UBL", "VEDL",
    "VOLTAS", "ZEEL", "MPHASIS", "PAGEIND", "DMART",
]

# ── §4.1 fix ──────────────────────────────────────────────────────────────
# The real BSE Sensex 30 basket. Previously "sensex" silently reused the
# NSE "NIFTY NEXT 50" list (main.py's MARKETS table + this file's old spec
# entry), which is a completely different set of stocks — a copy-paste bug,
# not a deliberate proxy. Sensex has no free NSE JSON/CSV endpoint (it's a
# BSE index), so unlike Nifty 50 / Bank Nifty this list has no live
# "official" source to poll — it is the single source of truth and only
# needs updating when BSE rebalances the index (typically twice a year).
# All symbols below are the NSE-listed trading symbol for the same company
# (every Sensex constituent is dual-listed on NSE), so they work directly
# with Fyers' "NSE:<SYMBOL>-EQ" format used everywhere else in this file.
# Verified against BSE Sensex's published constituent weightage, Sep 2026.
SENSEX30 = [
    "RELIANCE", "BHARTIARTL", "HDFCBANK", "ICICIBANK", "SBIN",
    "TCS", "BAJFINANCE", "LT", "HINDUNILVR", "SUNPHARMA",
    "TITAN", "INFY", "KOTAKBANK", "ADANIPORTS", "AXISBANK",
    "MARUTI", "M&M", "HCLTECH", "ITC", "NTPC",
    "ULTRACEMCO", "ETERNAL", "BAJAJFINSV", "BEL", "POWERGRID",
    "ASIANPAINT", "TATASTEEL", "INDIGO", "TECHM", "TRENT",
]


def _to_fyers(symbol: str) -> str:
    return f"NSE:{symbol.strip()}-EQ"


_INDEX_ROW_NAMES = {"NIFTY 50", "NIFTY 500", "NIFTY50", "NIFTY500"}


def _clean_symbols(raw) -> list[str]:
    """Strip, drop blanks / index rows / NSE dummy placeholders, de-duplicate (order kept)."""
    out: list[str] = []
    seen: set[str] = set()
    for item in raw or []:
        if item is None:
            continue
        sym = str(item).strip()
        up = sym.upper()
        if not sym or up in ("NAN", "NONE"):
            continue
        # Index rows of the NSE JSON ("NIFTY 500") always contain a space; equities never do.
        if " " in sym or up in _INDEX_ROW_NAMES:
            continue
        # NSE index CSVs pad some lists with DUMMY placeholder rows — not real stocks.
        if up.startswith("DUMMY"):
            continue
        if sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
    return out


def _fetch_from_api(url: str, session: requests.Session) -> list[str]:
    """Fetches symbols from NSE API. Returns [] on failure."""
    try:
        resp = session.get(url, headers=NSE_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return _clean_symbols(d.get("symbol") for d in data.get("data", []))
    except Exception:
        return []


def _fetch_from_csv(url: str, session: requests.Session | None = None) -> list[str]:
    """
    Fetches symbols from the NSE archive CSV. Returns [] on failure.

    The request is sent WITH the NSE browser headers: archives.nseindia.com
    rejects the bare urllib user-agent that pd.read_csv(url) uses, which made
    this fallback silently return [] before.
    """
    try:
        if session is None:
            session = requests.Session()
        resp = session.get(url, headers=NSE_HEADERS, timeout=20)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))
        col = next(c for c in df.columns if "symbol" in str(c).lower())
        return _clean_symbols(df[col].dropna().astype(str).tolist())
    except Exception:
        try:
            df = pd.read_csv(url)
            col = next(c for c in df.columns if "symbol" in str(c).lower())
            return _clean_symbols(df[col].dropna().astype(str).tolist())
        except Exception:
            return []


def plain_constituents_for_market(
    market_key: str,
    session: requests.Session | None = None,
) -> list[str]:
    """
    Return bare NSE symbols (e.g. RELIANCE) for a market basket.
    Tries live NSE JSON, then archives CSV, then hardcoded fallbacks.

    Parameters
    ----------
    market_key : "nifty" | "sensex" | "bank_nifty"
    session    : Optional pre-warmed requests.Session. When provided (passed
                 from main.py's _nse_session), avoids creating a redundant
                 session that may be blocked by NSE IP rate-limiting.
                 A new session is created only when none is supplied.
    """
    # §4.1 fix: "sensex" no longer maps to any NSE index endpoint (it used
    # to wrongly reuse NIFTY NEXT 50 — see the SENSEX30 comment above).
    # BSE has no free public JSON/CSV constituents API, so the hardcoded
    # SENSEX30 list *is* the source of truth for this basket; live prices
    # for it still come from Fyers, same as every other market.
    if market_key == "sensex":
        return list(SENSEX30)[:50]

    spec = {
        "nifty"     : (_URL_NIFTY50,     _CSV_NIFTY50,     _NIFTY50_FALLBACK),
        "bank_nifty": (_URL_NIFTY_BANK,  _CSV_NIFTY_BANK,  _NIFTY_BANK_FALLBACK),
    }
    pack = spec.get(market_key)
    if not pack:
        return []

    api_url, csv_url, hard = pack

    # Reuse the caller's session if provided; otherwise warm a new one.
    if session is None:
        session = requests.Session()
        try:
            session.get("https://www.nseindia.com/", headers=NSE_HEADERS, timeout=10)
        except Exception:
            pass

    symbols = _fetch_from_api(api_url, session)
    if len(symbols) >= (8 if market_key == "bank_nifty" else 40):
        return symbols[:50]

    csv_syms = _fetch_from_csv(csv_url, session)
    if len(csv_syms) >= (8 if market_key == "bank_nifty" else 40):
        return csv_syms[:50]

    return list(hard)[:50]


def fetch_nifty50() -> list[str]:
    """Returns Nifty 50 symbols in Fyers format."""
    session = requests.Session()
    try:
        session.get("https://www.nseindia.com/", headers=NSE_HEADERS, timeout=10)
    except Exception:
        pass

    symbols = _fetch_from_api(_URL_NIFTY50, session)
    if len(symbols) >= 45:
        return [_to_fyers(s) for s in symbols]

    symbols = _fetch_from_csv(_CSV_NIFTY50, session)
    if len(symbols) >= 45:
        return [_to_fyers(s) for s in symbols]

    return [_to_fyers(s) for s in _NIFTY50_FALLBACK]


# ── Nifty 500 universe ───────────────────────────────────────────────────────
NIFTY500_CACHE_FILE = "nifty500_universe.json"   # last successfully fetched official list
_N500_MIN_USABLE = 450          # below this the fetched list is treated as truncated
_N500_COMPLETE_RANGE = (490, 510)
_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
_meta_lock = threading.Lock()
_UNIVERSE_META: dict = {}


def get_universe_meta() -> dict:
    """Provenance of the most recently built Nifty 500 universe (copy)."""
    with _meta_lock:
        return dict(_UNIVERSE_META)


def _save_universe_cache(symbols: list[str], source: str) -> None:
    try:
        tmp = NIFTY500_CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({
                "saved_at": datetime.datetime.now(_IST).isoformat(),
                "source": source,
                "symbols": symbols,
            }, f)
        os.replace(tmp, NIFTY500_CACHE_FILE)
    except Exception:
        pass


def _load_universe_cache() -> tuple[list[str], str | None]:
    try:
        with open(NIFTY500_CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        syms = _clean_symbols(data.get("symbols"))
        return syms, data.get("saved_at")
    except Exception:
        return [], None


def fetch_nifty500_with_meta() -> tuple[list[str], dict]:
    """
    Build the Nifty 500 universe (Fyers format) and report exactly where it came from.

    Source order (all NSE / local — none of this touches Fyers):
        1. NSE live JSON  (equity-stockIndices?index=NIFTY 500)
        2. NSE archive CSV (ind_nifty500list.csv, fetched with NSE headers)
        3. last successfully fetched list stored on disk
    The Nifty-50-only list is used ONLY when all three fail, and the returned
    meta then says complete=False so callers can warn instead of silently
    scanning a truncated universe.

    Order: Nifty 50 first (only members that are really in the Nifty 500 list),
    then the remainder. Nothing outside the Nifty 500 list is ever added.
    """
    session = requests.Session()
    try:
        session.get("https://www.nseindia.com/", headers=NSE_HEADERS, timeout=10)
    except Exception:
        pass

    print("\n📡  Fetching Nifty 50 …")
    n50_source = "nse_api"
    n50 = _fetch_from_api(_URL_NIFTY50, session)
    if len(n50) < 45:
        n50_source = "nse_csv"
        n50 = _fetch_from_csv(_CSV_NIFTY50, session)
    if len(n50) < 45:
        n50_source = "hardcoded_fallback"
        n50 = list(_NIFTY50_FALLBACK)
    print(f"   ✅  {len(n50)} Nifty 50 symbols loaded ({n50_source})")

    print("📡  Fetching Nifty 500 …")
    source = "nse_api"
    n500 = _fetch_from_api(_URL_NIFTY500, session)
    if len(n500) < _N500_MIN_USABLE:
        source = "nse_csv"
        n500 = _fetch_from_csv(_CSV_NIFTY500, session)
    cache_saved_at = None
    if len(n500) < _N500_MIN_USABLE:
        cached, cache_saved_at = _load_universe_cache()
        if len(cached) >= _N500_MIN_USABLE:
            source = "last_good_cache"
            n500 = cached
            print(f"   ⚠️   NSE unreachable — using last good Nifty 500 list saved {cache_saved_at}")

    meta: dict = {
        "index": "NIFTY 500",
        "built_at": datetime.datetime.now(_IST).isoformat(),
        "nifty50_source": n50_source,
    }

    if len(n500) >= _N500_MIN_USABLE:
        if source != "last_good_cache":
            _save_universe_cache(n500, source)
        n500_set = set(n500)
        n50_in = [s for s in n50 if s in n500_set]
        n50_set = set(n50_in)
        remainder = [s for s in n500 if s not in n50_set]
        ordered = n50_in + remainder
        lo, hi = _N500_COMPLETE_RANGE
        meta.update({
            "source": source,
            "count": len(ordered),
            "complete": lo <= len(ordered) <= hi,
            "list_saved_at": cache_saved_at,
            "nifty50_not_in_nifty500": sorted(s for s in n50 if s not in n500_set),
            "error": None,
        })
        print(f"   ✅  {len(n500)} Nifty 500 symbols loaded ({source})")
        print(f"   📊  Scan order: {len(n50_in)} Nifty 50 → {len(remainder)} remainder")
        result = ordered
    else:
        print("   ⚠️   Nifty 500 list unavailable from every source. Universe is Nifty 50 ONLY.")
        meta.update({
            "source": "nifty50_only_fallback",
            "count": len(n50),
            "complete": False,
            "list_saved_at": None,
            "nifty50_not_in_nifty500": [],
            "error": "Nifty 500 constituents could not be fetched from NSE (API, CSV) "
                     "and no saved copy exists; the universe is incomplete.",
        })
        result = list(n50)

    with _meta_lock:
        _UNIVERSE_META.clear()
        _UNIVERSE_META.update(meta)
    return [_to_fyers(s) for s in result], meta


def fetch_nifty500() -> list[str]:
    """
    Returns all Nifty 500 symbols in Fyers format, Nifty 50 first.
    Provenance of the list is available from get_universe_meta().
    """
    symbols, _meta = fetch_nifty500_with_meta()
    return symbols

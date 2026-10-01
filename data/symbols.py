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
# Third, independent host for the same official Nifty 500 CSV (NSE Indices' own site).
_CSV_NIFTY500_ALT = "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"

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


def _clean_pairs(pairs) -> dict[str, str | None]:
    """{symbol: isin|None} after the same cleaning rules as _clean_symbols (order kept)."""
    raw: dict[str, str | None] = {}
    for sym, isin in pairs or []:
        if sym is None:
            continue
        key = str(sym).strip()
        isin = str(isin).strip().upper() if isin else None
        if isin in ("", "NAN", "NONE"):
            isin = None
        if key not in raw or (raw[key] is None and isin):
            raw[key] = isin
    return {s: raw[s] for s in _clean_symbols(list(raw))}


def _fetch_rows_from_api(url: str, session: requests.Session) -> dict[str, str | None]:
    """NSE JSON → {symbol: isin|None}. Empty dict on failure."""
    try:
        resp = session.get(url, headers=NSE_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        pairs = []
        for d in data.get("data", []):
            meta = d.get("meta") if isinstance(d.get("meta"), dict) else {}
            pairs.append((d.get("symbol"), meta.get("isin")))
        return _clean_pairs(pairs)
    except Exception:
        return {}


def _fetch_rows_from_csv(url: str, session: requests.Session | None = None) -> dict[str, str | None]:
    """
    Index CSV → {symbol: isin|None}. Empty dict on failure.

    The request is sent WITH the NSE browser headers: archives.nseindia.com
    rejects the bare urllib user-agent that pd.read_csv(url) uses.
    """
    def _parse(df: pd.DataFrame) -> dict[str, str | None]:
        col = next(c for c in df.columns if "symbol" in str(c).lower())
        icol = next((c for c in df.columns if "isin" in str(c).lower()), None)
        syms = df[col].astype(str).tolist()
        isins = df[icol].astype(str).tolist() if icol is not None else [None] * len(syms)
        keep = [(a, b) for a, b in zip(syms, isins) if str(a).strip().upper() not in ("NAN", "NONE", "")]
        return _clean_pairs(keep)

    try:
        if session is None:
            session = requests.Session()
        resp = session.get(url, headers=NSE_HEADERS, timeout=20)
        resp.raise_for_status()
        return _parse(pd.read_csv(io.StringIO(resp.text)))
    except Exception:
        try:
            return _parse(pd.read_csv(url))
        except Exception:
            return {}


def _fetch_from_api(url: str, session: requests.Session) -> list[str]:
    """Fetches symbols from NSE API. Returns [] on failure."""
    return list(_fetch_rows_from_api(url, session))


def _fetch_from_csv(url: str, session: requests.Session | None = None) -> list[str]:
    """Fetches symbols from an NSE index CSV. Returns [] on failure."""
    return list(_fetch_rows_from_csv(url, session))


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
NIFTY500_CACHE_FILE = "nifty500_universe.json"   # last COMPLETE official list
_N500_MIN_USABLE = 450          # below this a fetched list is ignored entirely
_N500_COMPLETE_RANGE = (490, 510)
_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
_meta_lock = threading.Lock()
_UNIVERSE_META: dict = {}


def get_universe_meta() -> dict:
    """Provenance of the most recently built Nifty 500 universe (copy)."""
    with _meta_lock:
        return dict(_UNIVERSE_META)


def _is_complete(n: int) -> bool:
    lo, hi = _N500_COMPLETE_RANGE
    return lo <= n <= hi


def _save_universe_cache(rows: dict[str, str | None], source: str) -> None:
    """Only ever called with a COMPLETE list, so a truncated fetch can never poison the fallback."""
    try:
        tmp = NIFTY500_CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({
                "saved_at": datetime.datetime.now(_IST).isoformat(),
                "source": source,
                "symbols": list(rows),
                "isins": {k: v for k, v in rows.items() if v},
            }, f)
        os.replace(tmp, NIFTY500_CACHE_FILE)
    except Exception:
        pass


def _load_universe_cache() -> tuple[dict[str, str | None], str | None]:
    try:
        with open(NIFTY500_CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        isins = data.get("isins") or {}
        syms = _clean_symbols(data.get("symbols"))
        return {s: isins.get(s) for s in syms}, data.get("saved_at")
    except Exception:
        return {}, None


def _fetch_n500_rows(session: requests.Session) -> tuple[dict[str, str | None], str, list[dict], str | None]:
    """
    Fetch the Nifty 500 from the official sources and pick the most complete result.

    Sources are tried in order and the first COMPLETE answer (490–510 symbols) is
    used immediately, so a healthy NSE costs exactly one request.  Only when no
    source is complete are the others fetched and combined — a list that NSE
    truncated can no longer be accepted as if it were the whole index.

    Returns (rows, source, tried, cache_saved_at).
    """
    sources = [
        ("nse_api", lambda: _fetch_rows_from_api(_URL_NIFTY500, session)),
        ("nse_csv", lambda: _fetch_rows_from_csv(_CSV_NIFTY500, session)),
        ("niftyindices_csv", lambda: _fetch_rows_from_csv(_CSV_NIFTY500_ALT, session)),
    ]
    tried: list[dict] = []
    got: list[tuple[str, dict[str, str | None]]] = []
    for name, fn in sources:
        rows = fn()
        tried.append({"source": name, "count": len(rows)})
        if rows:
            got.append((name, rows))
        if _is_complete(len(rows)):
            return rows, name, tried, None

    # Nobody was complete. Add the last good copy to the pool and merge.
    cached, cache_saved_at = _load_universe_cache()
    if cached:
        tried.append({"source": "last_good_cache", "count": len(cached)})
        got.append(("last_good_cache", cached))
    if not got:
        return {}, "none", tried, cache_saved_at

    merged: dict[str, str | None] = {}
    for _name, rows in got:
        for sym, isin in rows.items():
            if sym not in merged or (merged[sym] is None and isin):
                merged[sym] = isin
    names = "+".join(n for n, _ in got)
    if _is_complete(len(merged)):
        return merged, f"merged({names})", tried, cache_saved_at

    # Merge overshot or is still short: prefer a complete cached list, else the
    # largest single answer, else the merge if it is the biggest usable thing we have.
    if cached and _is_complete(len(cached)):
        return cached, "last_good_cache", tried, cache_saved_at
    biggest_name, biggest = max(got, key=lambda g: len(g[1]))
    if len(merged) <= _N500_COMPLETE_RANGE[1] and len(merged) >= len(biggest):
        return merged, f"merged({names})", tried, cache_saved_at
    return biggest, biggest_name, tried, cache_saved_at


def fetch_nifty500_with_meta() -> tuple[list[str], dict]:
    """
    Build the Nifty 500 universe (Fyers format) and report exactly where it came from.

    Source order (all NSE / local — none of this touches the Fyers API):
        1. NSE live JSON  (equity-stockIndices?index=NIFTY 500)
        2. NSE archive CSV (ind_nifty500list.csv, fetched with NSE headers)
        3. niftyindices.com CSV (same official file, independent host)
        4. If none of them is complete: the sources combined with the last
           complete list saved on disk.
    The Nifty-50-only list is used ONLY when everything fails, and the returned
    meta then says complete=False so callers can warn instead of silently
    scanning a truncated universe.

    Order: Nifty 50 first (only members that are really in the Nifty 500 list),
    then the remainder. Nothing outside the Nifty 500 list is ever added.

    After the list is built, Fyers' public symbol master is used to map each NSE
    symbol to its exact Fyers ticker (see data/fyers_master.py).
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
    rows, source, tried, cache_saved_at = _fetch_n500_rows(session)
    n500 = list(rows)
    if source == "last_good_cache":
        print(f"   ⚠️   NSE unreachable/incomplete — using last good Nifty 500 list saved {cache_saved_at}")

    meta: dict = {
        "index": "NIFTY 500",
        "built_at": datetime.datetime.now(_IST).isoformat(),
        "nifty50_source": n50_source,
        "sources_tried": tried,
    }

    if len(n500) >= _N500_MIN_USABLE:
        complete = _is_complete(len(n500))
        # ISINs: needed only for the Fyers ticker mapping. Fetch the CSV once if the list has few.
        if sum(1 for v in rows.values() if v) < 0.9 * len(rows):
            for url in (_CSV_NIFTY500, _CSV_NIFTY500_ALT):
                extra = _fetch_rows_from_csv(url, session)
                if extra:
                    for sym in rows:
                        if not rows[sym] and extra.get(sym):
                            rows[sym] = extra[sym]
                    if sum(1 for v in rows.values() if v) >= 0.9 * len(rows):
                        break
        if complete and source != "last_good_cache":
            _save_universe_cache(rows, source)
        n500_set = set(n500)
        n50_in = [s for s in n50 if s in n500_set]
        n50_set = set(n50_in)
        remainder = [s for s in n500 if s not in n50_set]
        ordered = n50_in + remainder
        meta.update({
            "source": source,
            "count": len(ordered),
            "complete": complete,
            "list_saved_at": cache_saved_at,
            "nifty50_not_in_nifty500": sorted(s for s in n50 if s not in n500_set),
            "error": None if complete else (
                f"Only {len(ordered)} Nifty 500 symbols could be assembled from NSE "
                f"(expected {_N500_COMPLETE_RANGE[0]}–{_N500_COMPLETE_RANGE[1]}); the list is incomplete."
            ),
        })
        print(f"   ✅  {len(n500)} Nifty 500 symbols loaded ({source}"
              f"{'' if complete else ' — INCOMPLETE'})")
        print(f"   📊  Scan order: {len(n50_in)} Nifty 50 → {len(remainder)} remainder")
        result = ordered
        isin_map = {s: rows.get(s) for s in ordered}
    else:
        print("   ⚠️   Nifty 500 list unavailable from every source. Universe is Nifty 50 ONLY.")
        meta.update({
            "source": "nifty50_only_fallback",
            "count": len(n50),
            "complete": False,
            "list_saved_at": None,
            "nifty50_not_in_nifty500": [],
            "error": "Nifty 500 constituents could not be fetched from NSE (API, CSV, niftyindices) "
                     "and no saved copy exists; the universe is incomplete.",
        })
        result = list(n50)
        isin_map = {s: None for s in result}

    # Exact Fyers ticker per stock (zero Fyers API calls). Never raises.
    try:
        from data.fyers_master import build_hints
        fm = build_hints(isin_map)
        meta["fyers_master"] = fm
        if fm.get("loaded"):
            print(f"   🧭  Fyers symbol master ({fm.get('source')}): {fm['listed_on_fyers']}/{fm['universe']} "
                  f"listed, {len(fm['remapped'])} on a non-EQ/renamed ticker, "
                  f"{len(fm['not_in_master'])} not in master")
        else:
            print("   ⚠️   Fyers symbol master unavailable — falling back to -EQ/-BE/-BZ/-SM/-ST probing")
    except Exception as exc:
        meta["fyers_master"] = {"loaded": False, "error": f"{type(exc).__name__}: {exc}"}

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

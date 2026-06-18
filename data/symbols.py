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

# Nifty Next 50 — Sensex tab uses this index on NSE; static fallback if CSV/API fail.
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


def _to_fyers(symbol: str) -> str:
    return f"NSE:{symbol.strip()}-EQ"


def _fetch_from_api(url: str, session: requests.Session) -> list[str]:
    """Fetches symbols from NSE API. Returns [] on failure."""
    try:
        resp = session.get(url, headers=NSE_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return [
            d["symbol"] for d in data.get("data", [])
            if d.get("symbol")
            # Case-insensitive filter — NSE returns index names in mixed case
            # e.g. 'Nifty 50', 'Nifty Bank' — startswith('NIFTY') misses these.
            and d["symbol"].upper() not in ("NIFTY 50", "NIFTY 500", "NIFTY50", "NIFTY500")
            and not d["symbol"].upper().startswith("NIFTY")
            and " " not in d["symbol"]   # all index names have spaces; equities don't
        ]
    except Exception:
        return []


def _fetch_from_csv(url: str) -> list[str]:
    """Fetches symbols from NSE CSV fallback. Returns [] on failure."""
    try:
        df  = pd.read_csv(url)
        col = next(c for c in df.columns if "symbol" in c.lower())
        return df[col].dropna().str.strip().tolist()
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
    spec = {
        "nifty"     : (_URL_NIFTY50,     _CSV_NIFTY50,     _NIFTY50_FALLBACK),
        "bank_nifty": (_URL_NIFTY_BANK,  _CSV_NIFTY_BANK,  _NIFTY_BANK_FALLBACK),
        "sensex"    : (_URL_NIFTY_NEXT50, _CSV_NIFTY_NEXT50, _NIFTY_NEXT50_FALLBACK),
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

    csv_syms = _fetch_from_csv(csv_url)
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

    symbols = _fetch_from_csv(_CSV_NIFTY50)
    if len(symbols) >= 45:
        return [_to_fyers(s) for s in symbols]

    return [_to_fyers(s) for s in _NIFTY50_FALLBACK]


def fetch_nifty500() -> list[str]:
    """
    Returns all Nifty 500 symbols in Fyers format,
    with Nifty 50 stocks placed FIRST in the list.

    Order: [Nifty 50 (50)] + [Nifty 500 remainder (450)]
    Total: up to 500 symbols
    """
    session = requests.Session()
    try:
        session.get("https://www.nseindia.com/", headers=NSE_HEADERS, timeout=10)
    except Exception:
        pass

    print("\n📡  Fetching Nifty 50 …")
    n50 = _fetch_from_api(_URL_NIFTY50, session)
    if len(n50) < 45:
        n50 = _fetch_from_csv(_CSV_NIFTY50)
    if len(n50) < 45:
        n50 = _NIFTY50_FALLBACK
    print(f"   ✅  {len(n50)} Nifty 50 symbols loaded")

    print("📡  Fetching Nifty 500 …")
    # Use CSV as primary for Nifty 500 — more reliable for getting all 500
    n500 = _fetch_from_csv(_CSV_NIFTY500)
    if len(n500) < 400:
        n500 = _fetch_from_api(_URL_NIFTY500, session)

    if len(n500) >= 400:
        print(f"   ✅  {len(n500)} Nifty 500 symbols loaded")
        # Build ordered list: Nifty 50 first, then remaining 450
        n50_set      = set(n50)
        n500_set     = set(n500)
        remainder    = [s for s in n500 if s not in n50_set]
        ordered      = list(n50) + remainder
        print(f"   📊  Scan order: {len(n50)} Nifty 50 → {len(remainder)} remainder")
        return [_to_fyers(s) for s in ordered]
    else:
        # Fallback — just return Nifty 50
        print("   ⚠️   Nifty 500 fetch failed. Scanning Nifty 50 only.")
        return [_to_fyers(s) for s in n50]
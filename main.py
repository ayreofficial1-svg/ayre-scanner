"""
main.py
───────
Nifty 500 Swing Trading Scanner — Fyers API

Backend only. React frontend lives in frontend/ and builds to static/.

Development:
    cd frontend && npm run dev        # React on :5173, proxies /api to :5000
    python main.py                    # Flask API on :5000

Production (after npm run build):
    python main.py                    # Flask serves everything on :5000

Other options:
    python main.py --verbose
    python main.py --port 8080
"""

import os
import sys
import datetime
import argparse
import threading
import time
import webbrowser
import requests

# Fixed UTC+5:30 offset — no pytz/zoneinfo dependency.
# Railway (and most cloud hosts) run UTC; this ensures all market-hour
# comparisons use IST wall-clock time regardless of the host timezone.
_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

from auth.fyers_auth import connect_fyers, reconnect_fyers
from config.settings import ACTIVE_CHECK_HOURS, PASSIVE_CHECK_INTERVAL
from data.symbols import fetch_nifty500, plain_constituents_for_market
from scanner.watchlist import (
    load_watchlist, clean_watchlist, save_watchlist,
    load_alert_log, clean_alert_log, save_alert_log,
)
from scanner.engine import run_scan
from utils.logger import get_log_summary
from data.quotes import fetch_ltp_bulk, fetch_constituents_quotes_bulk

try:
    from flask import Flask, jsonify, send_from_directory
except ImportError:
    sys.exit("❌  Flask not installed. Run: pip install flask")

try:
    from flask_cors import CORS
    _CORS_AVAILABLE = True
except ImportError:
    _CORS_AVAILABLE = False

# Static folder = nifty_scanner/static/ (React build output)
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")

# ── CORS — required for Flutter web / mobile clients ─────────────────────────
if _CORS_AVAILABLE:
    CORS(app, resources={r"/api/*": {"origins": "*"}})
else:
    @app.after_request
    def _add_cors_headers(response):
        response.headers["Access-Control-Allow-Origin"]  = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return response

# ── NSE session — reused across all NSE fetches ───────────────────────────────
# NSE requires a browser-like session with cookies. Created once at startup
# and reused for all NSE API calls. Reset automatically on failure.
_nse_session: requests.Session | None = None
_nse_session_lock = threading.Lock()

# ── Shared scan state ─────────────────────────────────────────────────────────
_state = {
    "signals"         : [],
    "watchlist_items" : [],
    "scan_time"       : None,
    "total_scanned"   : 0,    # symbols with usable data returned this run
    "total_attempted" : 0,    # ground truth: len(symbols) passed to engine
    "scanning"        : False,
    "error"           : None,
    "next_scan_time"  : None, # ISO timestamp of next scheduled boundary
}
_fyers      = None
_symbols    = None
_start_time = time.time()   # for /api/status uptime tracking

# Market hours (IST) — scanner runs only within this window
_MARKET_OPEN  = datetime.time(9,  0)   # 09:00 IST — first scan trigger
_MARKET_CLOSE = datetime.time(15, 30)  # 15:30 IST — no new scans after this

# ── Market snapshot cache ─────────────────────────────────────────────────────
_market_lock = threading.Lock()
_market_cache = {
    "data"      : None,
    "expires_at": 0.0,
}

# ── Constituents cache ────────────────────────────────────────────────────────
# Shape: { market_key: {"data": {...}, "expires_at": float} }
_constituents_lock  = threading.Lock()
_constituents_cache: dict[str, dict] = {}
_CONSTITUENTS_TTL   = 60   # seconds

# ── Market index configuration ────────────────────────────────────────────────
# Defines all markets served by the API in carousel order.
# Flutter should render them in list order and cycle infinitely:
#   nextIndex = (currentIndex + 1) % markets.length
#
# nse_index_param: exact string for NSE equity-stockIndices ?index=
#   "NIFTY 50"      → 50 large-cap NSE stocks
#   "NIFTY NEXT 50" → Best NSE-native proxy for Sensex 50 (BSE constituent
#                     API requires BSE auth; this gives the equivalent 50
#                     large-cap stocks via the same free NSE endpoint)
#   "NIFTY BANK"    → All Bank Nifty constituents (12 banking stocks)
MARKETS = [
    {
        "market_key"        : "nifty",
        "display_name"      : "Nifty 50",
        "nse_allindices_key": "NIFTY 50",
        "nse_index_param"   : "NIFTY 50",
        "fyers_symbol"      : "NSE:NIFTY50-INDEX",
        "yahoo_symbol"      : "^NSEI",
        "fyers_key"         : "NIFTY50",
    },
    {
        "market_key"        : "sensex",
        "display_name"      : "Sensex",
        "nse_allindices_key": "SENSEX",
        "nse_index_param"   : "NIFTY NEXT 50",
        "fyers_symbol"      : "BSE:SENSEX-INDEX",
        "yahoo_symbol"      : "^BSESN",
        "fyers_key"         : "SENSEX",
    },
    {
        "market_key"        : "bank_nifty",
        "display_name"      : "Bank Nifty",
        "nse_allindices_key": "NIFTY BANK",
        "nse_index_param"   : "NIFTY BANK",
        "fyers_symbol"      : "NSE:NIFTYBANK-INDEX",
        "yahoo_symbol"      : "^NSEBANK",
        "fyers_key"         : "NIFTYBANK",
    },
]

# ── Live quotes cache ─────────────────────────────────────────────────────────
_quotes_lock  = threading.Lock()
_quotes_cache : dict[str, float | None] = {}
_quotes_updated_at: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# API routes (defined before the catch-all)
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/results")
def api_results():
    return jsonify({
        "scanning"        : _state["scanning"],
        "scan_time"       : _state["scan_time"],
        "total_scanned"   : _state["total_scanned"],
        "total_attempted" : _state["total_attempted"],
        "signals"         : _state["signals"],
        "watchlist_items" : _state["watchlist_items"],
        "error"           : _state["error"],
    })


@app.route("/api/rescan", methods=["POST"])
def api_rescan():
    if _state["scanning"]:
        return jsonify({"status": "already_running"})
    _state["scanning"] = True
    threading.Thread(target=_do_scan, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/api/status")
def api_status():
    """
    Returns system health, uptime, and next scheduled scan time.

    Response shape
    ──────────────
    {
        "status"         : "live" | "scanning",
        "uptime_seconds" : 3600,
        "signals_logged" : 256,
        "logs_days"      : 42,
        "next_scan"      : "05 Apr 2026 10:30:00",
        "total_scanned"  : 404,
        "total_attempted": 498,
        "memory_mb"      : 125.4
    }
    """
    try:
        import psutil
        proc   = psutil.Process(os.getpid())
        mem_mb = round(proc.memory_info().rss / 1_048_576, 1)
    except Exception:
        mem_mb = None

    summary = get_log_summary()
    uptime  = int(time.time() - _start_time)

    return jsonify({
        "status"          : "scanning" if _state["scanning"] else "live",
        "market_open"     : _is_market_open(),
        "uptime_seconds"  : uptime,
        "signals_logged"  : summary["total_signals"],
        "logs_days"       : summary["days_logged"],
        "next_scan"       : _state.get("next_scan_time"),
        "total_scanned"   : _state["total_scanned"],
        "total_attempted" : _state["total_attempted"],
        "memory_mb"       : mem_mb,
    })


@app.route("/api/market")
def api_market():
    """
    Returns live snapshot for all markets in carousel order.

    Response shape
    ──────────────
    {
        "markets": [
            {
                "key"   : "nifty",
                "name"  : "Nifty 50",
                "value" : 22150.50,
                "change": 0.45,
                "points": 99.70
            },
            { "key": "sensex",     "name": "Sensex",     ... },
            { "key": "bank_nifty", "name": "Bank Nifty", ... }
        ],
        "source"    : "nse" | "fyers" | "yahoo" | "fallback",
        "updated_at": "<ISO timestamp>"
    }

    Flutter integration notes
    ──────────────────────────
    - Poll every 5 s during market hours.
    - markets list is always in fixed order: Nifty → Sensex → Bank Nifty.
    - Infinite carousel: nextIndex = (currentIndex + 1) % markets.length
    - On tap, pass markets[i].key to GET /api/market/<key>/constituents.
    """
    return jsonify(_get_market_snapshot())


@app.route("/api/market/<string:market_key>/constituents")
def api_market_constituents(market_key: str):
    """
    Returns top-50 constituent stocks for the selected market.

    URL
    ───
    GET /api/market/nifty/constituents
    GET /api/market/sensex/constituents
    GET /api/market/bank_nifty/constituents

    Response shape
    ──────────────
    {
        "market_key" : "nifty",
        "market_name": "Nifty 50",
        "count"      : 50,
        "stocks": [
            {
                "rank"         : 1,
                "symbol"       : "RELIANCE",
                "company_name" : "Reliance Industries Ltd.",
                "last_price"   : 2850.45,
                "change_pct"   : 1.23,
                "change_points": 34.55,
                "open"         : 2820.00,
                "high"         : 2865.00,
                "low"          : 2810.00,
                "year_high"    : 3050.00,
                "year_low"     : 2180.00,
                "volume"       : 4500000,
                "market_cap"   : 1923456.78    (crores, may be null)
            },
            ...
        ],
        "source"    : "nse",
        "updated_at": "<ISO timestamp>"
    }

    Error: unknown market_key → HTTP 404 { "error": "..." }

    Flutter integration notes
    ──────────────────────────
    - Navigate to a new page when a market card is tapped.
    - Pass market_key and market_name as route arguments.
    - Fetch this endpoint once on page load; refresh on pull-to-refresh.
    - stocks[i].change_pct > 0 → green text, < 0 → red text.
    - Backend caches constituent data for 60 s — safe to call on every
      page entry without hammering NSE.
    """
    market_cfg = next((m for m in MARKETS if m["market_key"] == market_key), None)
    if market_cfg is None:
        valid = [m["market_key"] for m in MARKETS]
        return jsonify({
            "error": f"Unknown market key: '{market_key}'. Valid keys: {valid}"
        }), 404

    return jsonify(_get_constituents(market_cfg))


@app.route("/api/quotes")
def api_quotes():
    """
    Returns the latest traded price (LTP) for every stock in _state
    (signals + watchlist combined).

    Response shape
    --------------
    {
        "quotes": { "ONGC": 284.50, "HDFCBANK": 1923.10, ... },
        "updated_at": "<ISO timestamp>"
    }

    Poll every 15 s during market hours. Rapid polling is safe — the backend
    has a minimum-interval guard inside data/quotes.py.
    """
    with _quotes_lock:
        return jsonify({
            "quotes"    : dict(_quotes_cache),
            "updated_at": _quotes_updated_at,
        })


# ── React catch-all (serves index.html for all non-API routes) ───────────────
@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_react(path: str):
    if not os.path.isdir(STATIC_DIR):
        return (
            "<h2>Frontend not built.</h2>"
            "<p>Run <code>cd frontend && npm install && npm run build</code></p>"
        ), 503

    file_path = os.path.join(STATIC_DIR, path)
    if path and os.path.isfile(file_path):
        return send_from_directory(STATIC_DIR, path)

    index = os.path.join(STATIC_DIR, "index.html")
    if not os.path.isfile(index):
        return (
            "<h2>Frontend not built.</h2>"
            "<p>Run <code>cd frontend && npm install && npm run build</code></p>"
        ), 503

    return send_from_directory(STATIC_DIR, "index.html")


# ─────────────────────────────────────────────────────────────────────────────
# Scan
# ─────────────────────────────────────────────────────────────────────────────

def _do_scan():
    global _fyers, _symbols
    _state["scanning"] = True
    _state["error"]    = None
    try:
        watchlist = clean_watchlist(load_watchlist())
        alert_log = clean_alert_log(load_alert_log())
        save_watchlist(watchlist)
        save_alert_log(alert_log)

        signals, watchlist_items, fetch_report = run_scan(
            fyers     = _fyers,
            symbols   = _symbols,
            interval  = "D",
            watchlist = watchlist,
            alert_log = alert_log,
        )

        _state["signals"]         = signals
        _state["watchlist_items"] = watchlist_items
        _state["scan_time"]       = datetime.datetime.now().strftime("%d %b %Y %H:%M:%S")
        # Use authoritative counts from the fetch completeness report.
        # total_scanned = symbols that reached conditions.py after optional filters.
        # total_attempted = ground truth len(symbols) — never varies.
        _state["total_scanned"]   = fetch_report.get("evaluated", fetch_report["valid"])
        _state["total_attempted"] = fetch_report["attempted"]

    except Exception as e:
        _state["error"] = str(e)
    finally:
        _state["scanning"] = False


# ─────────────────────────────────────────────────────────────────────────────
# Clock-aligned scan scheduler
# ─────────────────────────────────────────────────────────────────────────────

def _seconds_until_next_active_slot() -> float | None:
    """
    Seconds until the next fixed Active Check slot (whole hour, IST).
    Returns None when all slots for today have passed (after 15:00).
    """
    now = datetime.datetime.now(_IST)
    for h in ACTIVE_CHECK_HOURS:
        target = now.replace(hour=h, minute=0, second=0, microsecond=0)
        diff = (target - now).total_seconds()
        if diff > 5.0:
            return diff
    return None


def _is_market_open() -> bool:
    """
    Return True only when the current IST wall-clock time falls within the
    trading window [_MARKET_OPEN, _MARKET_CLOSE] inclusive.

    Always derives IST from UTC via a fixed +5:30 offset so this is correct
    on any host timezone (including Railway's UTC default).
    """
    return _MARKET_OPEN <= datetime.datetime.now(_IST).time() <= _MARKET_CLOSE


def _passive_market_status() -> str:
    """
    Lightweight market-state probe using Yahoo Finance only. No Fyers.
    Returns 'open' | 'closed' | 'holiday'.
    Only called during the passive window (outside 09:00-15:30 IST).
    """
    try:
        resp = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/%5ENSEI",
            params={"interval": "1m", "range": "1d"},
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        result = resp.json().get("chart", {}).get("result") or []
        if not result:
            return "closed"
        meta = result[0].get("meta", {})
        state = str(meta.get("marketState", "")).upper()
        if state in ("REGULAR",):
            return "open"
        if not result[0].get("timestamp"):
            return "holiday"
        return "closed"
    except Exception:
        return "closed"


def _seconds_until_market_open() -> float:
    """
    Seconds to sleep until the next 09:00 IST market open.
    Used when the scanner is started outside market hours or after close.
    """
    now       = datetime.datetime.now(_IST)
    today_open = now.replace(
        hour=_MARKET_OPEN.hour, minute=_MARKET_OPEN.minute,
        second=0, microsecond=0,
    )
    if now >= today_open:
        # Already past today's open — wait for tomorrow's open
        today_open += datetime.timedelta(days=1)
    return (today_open - now).total_seconds()


def _scan_loop() -> None:
    """
    Unified scheduler: Passive Check (closed) -> Active Check (open).

    Passive  (15:30-09:00 IST): polls _passive_market_status() every
             PASSIVE_CHECK_INTERVAL seconds. No Fyers calls.
    Active   (09:00-15:30 IST): fires at fixed hourly slots 9-15.
             Re-authenticates Fyers once per trading day before first scan.
    """
    global _fyers
    _last_auth_date: datetime.date | None = None

    while True:
        now_ist = datetime.datetime.now(_IST)
        today = now_ist.date()

        # ── PASSIVE WINDOW ────────────────────────────────────────────────────
        if not _is_market_open():
            wait = _seconds_until_market_open()
            next_open = now_ist + datetime.timedelta(seconds=wait)
            _state["next_scan_time"] = next_open.strftime("%d %b %Y %H:%M:%S")

            status = _passive_market_status()
            if status == "holiday":
                print(f"📅  Trading holiday - passive check in {PASSIVE_CHECK_INTERVAL}s")
            time.sleep(PASSIVE_CHECK_INTERVAL)
            continue

        # ── ACTIVE WINDOW ─────────────────────────────────────────────────────
        if _last_auth_date != today:
            print(f"\n🔑  New trading day ({today}) - refreshing Fyers token …")
            for attempt in range(3):
                try:
                    _fyers = reconnect_fyers()
                    _last_auth_date = today
                    break
                except Exception as e:
                    print(f"   ⚠️  Re-auth attempt {attempt + 1}/3 failed: {e}")
                    time.sleep(30)
            else:
                print("   ❌ All re-auth attempts failed - will retry on next passive cycle.")
                time.sleep(PASSIVE_CHECK_INTERVAL)
                continue

        sleep_secs = _seconds_until_next_active_slot()
        if sleep_secs is None:
            h, rem = divmod(int(_seconds_until_market_open()), 3600)
            m, s = divmod(rem, 60)
            print(f"\n🔴  Active slots exhausted. Passive resumes. "
                  f"Next market open in {h}h {m}m {s}s")
            time.sleep(PASSIVE_CHECK_INTERVAL)
            continue

        next_dt = datetime.datetime.now(_IST) + datetime.timedelta(seconds=sleep_secs)
        _state["next_scan_time"] = next_dt.strftime("%d %b %Y %H:%M:%S")
        _, rem = divmod(int(sleep_secs), 3600)
        m, s = divmod(rem, 60)
        print(f"\n⏰  Next active scan at {next_dt.strftime('%H:%M')} IST (in {m}m {s}s)")

        time.sleep(sleep_secs)

        if not _is_market_open():
            print("\n🔴  Woke after market close — skipping scan.")
            continue

        print(f"\n🟢  Active check — {datetime.datetime.now(_IST).strftime('%H:%M')} IST")
        _do_scan()


# ─────────────────────────────────────────────────────────────────────────────
# Live quotes poller
# ─────────────────────────────────────────────────────────────────────────────

def _refresh_quotes() -> None:
    """Fetch live LTPs for all stocks in _state and update _quotes_cache."""
    global _quotes_cache, _quotes_updated_at

    if _fyers is None:
        return

    all_items = _state["signals"] + _state["watchlist_items"]
    if not all_items:
        return

    sym_map: dict[str, str] = {}
    for item in all_items:
        fyers_sym   = item.get("fyers_symbol") or ""
        display_sym = item.get("symbol")        or ""
        if fyers_sym and display_sym:
            sym_map[fyers_sym] = display_sym

    if not sym_map:
        return

    raw = fetch_ltp_bulk(_fyers, list(sym_map.keys()))
    remapped: dict[str, float | None] = {
        sym_map[fsym]: ltp
        for fsym, ltp in raw.items()
        if fsym in sym_map
    }

    with _quotes_lock:
        _quotes_cache      = remapped
        _quotes_updated_at = datetime.datetime.now().isoformat()


def _start_quotes_poller(interval_seconds: int = 15) -> None:
    def _poll():
        while not (_state["signals"] or _state["watchlist_items"]):
            time.sleep(2)
        print(f"📈  Quotes poller started (every {interval_seconds}s via Fyers bulk quotes)")
        while True:
            try:
                if _is_market_open():
                    _refresh_quotes()
            except Exception:
                pass
            time.sleep(interval_seconds)

    t = threading.Thread(target=_poll, daemon=True, name="quotes-poller")
    t.start()


# ─────────────────────────────────────────────────────────────────────────────
# Market snapshot — Nifty 50 + Sensex + Bank Nifty
# ─────────────────────────────────────────────────────────────────────────────

def _get_market_snapshot() -> dict:
    """Returns the cached market snapshot, refreshing it if stale."""
    now = time.time()
    with _market_lock:
        cached = _market_cache["data"]
        if cached is not None and _market_cache["expires_at"] > now:
            return cached

    # Source waterfall: NSE (free, primary) → Fyers → Yahoo → hardcoded fallback
    # Fyers is only used as a fallback during market hours — calling it outside
    # the trading window burns the daily rate limit before the market opens.
    if _is_market_open():
        fetchers = (_fetch_market_from_nse, _fetch_market_from_fyers, _fetch_market_from_yahoo)
    else:
        fetchers = (_fetch_market_from_nse, _fetch_market_from_yahoo)
    for fetcher in fetchers:
        try:
            data = fetcher()
            with _market_lock:
                _market_cache["data"]       = data
                _market_cache["expires_at"] = now + 5
            return data
        except Exception:
            continue

    # Return stale cache before giving up
    with _market_lock:
        if _market_cache["data"] is not None:
            return _market_cache["data"]

    # Absolute last resort
    timestamp    = datetime.datetime.now().isoformat()
    markets_list = [
        {"key": "nifty",      "name": "Nifty 50",    "value": 22150.50, "change": 0.45, "points":  99.70},
        {"key": "sensex",     "name": "Sensex",       "value": 73200.10, "change": 0.38, "points": 277.10},
        {"key": "bank_nifty", "name": "Bank Nifty",   "value": 46800.00, "change": 0.30, "points": 140.00},
    ]
    return {"markets": markets_list, "source": "fallback", "updated_at": timestamp}


def _fetch_market_from_nse() -> dict:
    """
    Primary source: NSE allIndices API.
    Fetches Nifty 50, Sensex, and Bank Nifty in a single HTTP call.
    """
    session  = _get_nse_session()
    response = session.get("https://www.nseindia.com/api/allIndices", timeout=10)
    response.raise_for_status()
    indices  = response.json().get("data", [])

    # Build a normalised lookup keyed by indexSymbol
    lookup: dict[str, dict] = {
        str(item.get("indexSymbol") or item.get("index") or "").upper().strip(): item
        for item in indices
    }

    found: dict[str, dict] = {}
    for market in MARKETS:
        item = lookup.get(market["nse_allindices_key"])
        if item:
            found[market["market_key"]] = _normalize_market_item(
                market["display_name"], {
                    "value" : item.get("last",          0),
                    "change": item.get("percentChange", 0),
                    "points": item.get("change",        0),
                }
            )

    if len(found) < len(MARKETS):
        missing = [m["market_key"] for m in MARKETS if m["market_key"] not in found]
        raise RuntimeError(
            f"NSE allIndices missing markets: {missing}. "
            f"Available keys (first 10): {list(lookup.keys())[:10]}"
        )

    return _build_market_payload(found, source="nse")


def _fetch_market_from_fyers() -> dict:
    """
    First fallback: Fyers quotes API using the existing trading connection.
    Fetches all three index symbols in a single call.
    """
    if _fyers is None:
        raise RuntimeError("Fyers connection unavailable")

    symbols_str = ",".join(m["fyers_symbol"] for m in MARKETS)
    response    = _fyers.quotes(data={"symbols": symbols_str})

    if not response or response.get("s") != "ok":
        raise RuntimeError(f"Fyers quote error: {response}")

    found: dict[str, dict] = {}
    for item in response.get("d", []):
        symbol = str(item.get("n") or item.get("symbol") or "").upper()
        values = item.get("v") or item
        for market in MARKETS:
            # Match by the Fyers symbol string (e.g. "NSE:NIFTY50-INDEX")
            if market["fyers_symbol"].upper() in symbol:
                found[market["market_key"]] = _normalize_market_item(
                    market["display_name"], values
                )
                break

    if len(found) < len(MARKETS):
        missing = [m["market_key"] for m in MARKETS if m["market_key"] not in found]
        raise RuntimeError(f"Incomplete Fyers market payload. Missing: {missing}")

    return _build_market_payload(found, source="fyers")


def _fetch_market_from_yahoo() -> dict:
    """
    Second fallback: Yahoo Finance. May throttle under heavy polling.
    """
    yahoo_symbols = ",".join(m["yahoo_symbol"] for m in MARKETS)
    response = requests.get(
        "https://query1.finance.yahoo.com/v7/finance/quote",
        params={"symbols": yahoo_symbols},
        timeout=10,
    )
    response.raise_for_status()
    results = response.json().get("quoteResponse", {}).get("result", [])

    found: dict[str, dict] = {}
    for item in results:
        symbol = str(item.get("symbol") or "").upper()
        values = {
            "value" : item.get("regularMarketPrice"),
            "change": item.get("regularMarketChangePercent"),
            "points": item.get("regularMarketChange"),
        }
        for market in MARKETS:
            if market["yahoo_symbol"].upper() == symbol:
                found[market["market_key"]] = _normalize_market_item(
                    market["display_name"], values
                )
                break

    if len(found) < len(MARKETS):
        missing = [m["market_key"] for m in MARKETS if m["market_key"] not in found]
        raise RuntimeError(f"Incomplete Yahoo market payload. Missing: {missing}")

    return _build_market_payload(found, source="yahoo")


def _build_market_payload(found: dict[str, dict], source: str) -> dict:
    """
    Assemble the canonical /api/market response.
    Markets are always emitted in MARKETS list order for a stable carousel.
    """
    markets_list = []
    for market in MARKETS:
        entry = found[market["market_key"]].copy()
        entry["key"] = market["market_key"]
        markets_list.append(entry)

    return {
        "markets"   : markets_list,
        "source"    : source,
        "updated_at": datetime.datetime.now().isoformat(),
    }


def _normalize_market_item(name: str, values: dict) -> dict:
    value  = _pick_number(values, "value",  "lp", "ltp", "last_price")
    change = _pick_number(values, "change", "change_pct", "chp", "percent_change")
    points = _pick_number(values, "points", "change_points", "ch")
    return {
        "name"  : name,
        "value" : round(value,  2),
        "change": round(change, 2),
        "points": round(points, 2),
    }


def _pick_number(values: dict, *keys: str) -> float:
    for key in keys:
        raw = values.get(key)
        if raw is None:
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    raise ValueError(f"Missing numeric value for keys: {keys}")


# ─────────────────────────────────────────────────────────────────────────────
# Constituent stocks — top-50 per market
# ─────────────────────────────────────────────────────────────────────────────

def _get_constituents(market_cfg: dict) -> dict:
    """Return (cached) constituent list for the market. TTL = 60 s."""
    market_key = market_cfg["market_key"]
    now        = time.time()

    with _constituents_lock:
        cached = _constituents_cache.get(market_key)
        if cached is not None and cached["expires_at"] > now:
            return cached["data"]

    try:
        data = _build_constituents_payload(market_cfg)
    except Exception as e:
        # Surface a partial response rather than a 500 error.
        # Use plain hardcoded symbols so the frontend always gets a list.
        from data.symbols import plain_constituents_for_market
        syms   = plain_constituents_for_market(market_key)
        stocks = _constituent_dicts_from_plain_symbols(syms)
        data   = {
            "market_key" : market_key,
            "market_name": market_cfg["display_name"],
            "count"      : len(stocks),
            "stocks"     : stocks,
            "source"     : "hardcoded_fallback",
            "updated_at" : datetime.datetime.now().isoformat(),
            "error"      : str(e),
        }

    with _constituents_lock:
        _constituents_cache[market_key] = {
            "data"      : data,
            "expires_at": now + _CONSTITUENTS_TTL,
        }

    return data


def _is_nse_index_symbol_row(symbol: str) -> bool:
    """True for NSE header / index rows, not common equities."""
    if not symbol:
        return True
    if " " in symbol:
        return True
    u = symbol.upper().strip()
    return u.startswith("NIFTY")


def _parse_equity_stockindices_rows(raw_stocks: list) -> list[dict]:
    """Parse NSE equity-stockIndices `data` into stock dicts (≤50)."""
    stocks = []
    rank   = 0
    for item in raw_stocks:
        symbol = str(item.get("symbol") or "").strip()
        if not symbol or _is_nse_index_symbol_row(symbol):
            continue

        meta = item.get("meta")
        if isinstance(meta, dict):
            company = str(
                item.get("companyName")
                or meta.get("companyName")
                or meta.get("symbol")
                or ""
            ).strip()
        else:
            company = str(item.get("companyName") or "").strip()
        if not company:
            company = symbol

        rank += 1
        stocks.append({
            "rank"         : rank,
            "symbol"       : symbol.upper(),
            "company_name" : company,
            "last_price"   : _safe_float(item.get("lastPrice")),
            "change_pct"   : _safe_float(item.get("pChange")),
            "change_points": _safe_float(item.get("change")),
            "open"         : _safe_float(item.get("open")),
            "high"         : _safe_float(item.get("dayHigh")),
            "low"          : _safe_float(item.get("dayLow")),
            "year_high"    : _safe_float(item.get("yearHigh")),
            "year_low"     : _safe_float(item.get("yearLow")),
            "volume"       : _safe_int(item.get("totalTradedVolume")),
            "market_cap"   : _safe_float(item.get("marketCap")),
        })
        if rank >= 50:
            break
    return stocks


def _constituent_dicts_from_plain_symbols(symbols: list[str]) -> list[dict]:
    """Skeleton rows — Fyers (or NSE) fills prices."""
    out = []
    for sym in symbols[:50]:
        s = sym.strip().upper()
        if not s or _is_nse_index_symbol_row(s):
            continue
        out.append({
            "rank"          : len(out) + 1,
            "symbol"        : s,
            "company_name"  : s,
            "last_price"    : None,
            "change_pct"    : None,
            "change_points" : None,
            "open"          : None,
            "high"          : None,
            "low"           : None,
            "year_high"     : None,
            "year_low"      : None,
            "volume"        : None,
            "market_cap"    : None,
        })
    return out


def _merge_fyers_into_constituent_stocks(stocks: list[dict]) -> bool:
    """Enrich rows with batched Fyers quotes. Returns True if any field updated."""
    global _fyers
    if not stocks or _fyers is None:
        return False
    syms = [s["symbol"] for s in stocks if s.get("symbol")]
    qmap = fetch_constituents_quotes_bulk(_fyers, syms)
    if not qmap:
        return False
    any_hit = False
    for row in stocks:
        key = str(row.get("symbol") or "").upper()
        q   = qmap.get(key) or {}
        if not q:
            continue
        if q.get("last_price") is not None:
            row["last_price"] = q["last_price"]
            any_hit = True
        if q.get("high") is not None:
            row["high"] = q["high"]
            any_hit = True
        if q.get("low") is not None:
            row["low"] = q["low"]
            any_hit = True
        if q.get("open") is not None:
            row["open"] = q["open"]
        if q.get("change_pct") is not None:
            row["change_pct"] = q["change_pct"]
        if q.get("change_points") is not None:
            row["change_points"] = q["change_points"]
        if q.get("volume") is not None:
            vi = _safe_int(q.get("volume"))
            if vi is not None:
                row["volume"] = vi
    return any_hit


def _build_constituents_payload(market_cfg: dict) -> dict:
    """
    Compose index constituents: NSE table when available, CSV/API fallbacks,
    then batched Fyers quotes (≤2 calls for 50) for live LTP / high / low.
    """
    market_key = market_cfg["market_key"]
    session    = _get_nse_session()
    url        = "https://www.nseindia.com/api/equity-stockIndices"
    params     = {"index": market_cfg["nse_index_param"]}
    raw_stocks : list = []
    nse_table_ok = False

    try:
        response = session.get(url, params=params, timeout=12)
        response.raise_for_status()
        raw_stocks = response.json().get("data", [])
        nse_table_ok = True
    except Exception:
        global _nse_session
        with _nse_session_lock:
            _nse_session = None
        try:
            session = _get_nse_session()
            response = session.get(url, params=params, timeout=12)
            response.raise_for_status()
            raw_stocks = response.json().get("data", [])
            nse_table_ok = True
        except Exception:
            raw_stocks = []

    stocks        = _parse_equity_stockindices_rows(raw_stocks)
    used_fallback = False
    min_rows      = 6 if market_key == "bank_nifty" else 15

    if len(stocks) < min_rows:
        # Pass the already-warmed _nse_session so plain_constituents_for_market
        # doesn't create a redundant new session that will hit the same IP block.
        try:
            alt = plain_constituents_for_market(market_key, session=session)
        except Exception:
            alt = []
        if len(alt) > len(stocks):
            stocks = _constituent_dicts_from_plain_symbols(alt)
            used_fallback = True
            nse_table_ok = False

    fyers_hit = _merge_fyers_into_constituent_stocks(stocks)

    if nse_table_ok and not used_fallback:
        base = "nse"
    elif used_fallback:
        base = "nse_fallback"
    else:
        base = "snapshot"

    source = f"{base}+fyers" if fyers_hit else base

    return {
        "market_key" : market_key,
        "market_name": market_cfg["display_name"],
        "count"      : len(stocks),
        "stocks"     : stocks,
        "source"     : source,
        "updated_at" : datetime.datetime.now().isoformat(),
    }


def _safe_float(value) -> float | None:
    if value is None:
        return None
    try:
        return round(float(str(value).replace(",", "")), 2)
    except (TypeError, ValueError):
        return None


def _safe_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# NSE session management
# ─────────────────────────────────────────────────────────────────────────────

def _get_nse_session() -> requests.Session:
    """
    Return a warmed-up NSE session with cookies.
    NSE requires visiting the homepage first to receive a session cookie;
    otherwise all API endpoints return 401 or empty data.

    The warm-up visits two pages — homepage then the market-data page —
    to ensure NSE's anti-scraping layer issues a full session cookie set.
    Without the second visit the equity-stockIndices endpoint often returns
    HTML instead of JSON on Railway / cloud IPs.
    """
    global _nse_session
    with _nse_session_lock:
        if _nse_session is None:
            s = requests.Session()
            s.headers.update({
                "User-Agent"     : "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                                   "Chrome/124.0.0.0 Safari/537.36",
                "Accept"         : "text/html,application/xhtml+xml,application/xml;"
                                   "q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection"     : "keep-alive",
                "Referer"        : "https://www.nseindia.com/",
                "DNT"            : "1",
            })
            try:
                # Visit 1: homepage — sets initial cookies
                s.get("https://www.nseindia.com", timeout=10)
                time.sleep(0.8)
                # Visit 2: market-data page — NSE sets the full API-access cookie
                # after this second visit; without it equity-stockIndices returns HTML
                s.get("https://www.nseindia.com/market-data/live-equity-market",
                      timeout=10)
                time.sleep(0.5)
                # Switch headers to JSON-accepting mode for API calls
                s.headers.update({
                    "Accept" : "application/json, text/plain, */*",
                    "Referer": "https://www.nseindia.com/market-data/live-equity-market",
                })
            except Exception:
                pass   # session may still work partially; let callers handle failure
            _nse_session = s
        return _nse_session


# ─────────────────────────────────────────────────────────────────────────────
# Background pollers
# ─────────────────────────────────────────────────────────────────────────────

def _start_market_poller(interval_seconds: int = 5) -> None:
    """
    Background thread that refreshes the market snapshot cache every interval.
    Cache is always warm — Flutter clients get instant responses.
    Outside market hours the poll slows to 60 s (index values don't change)
    and Fyers is never called, preserving the daily API rate limit.
    """
    def _poll():
        try:
            _get_nse_session()
        except Exception:
            pass

        while True:
            try:
                _get_market_snapshot()
            except Exception:
                pass
            sleep = interval_seconds if _is_market_open() else 60
            time.sleep(sleep)

    t = threading.Thread(target=_poll, daemon=True, name="market-poller")
    t.start()
    print(f"📡  Market poller started (every {interval_seconds}s during market hours — NSE → Fyers → Yahoo)")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    global _fyers, _symbols

    parser = argparse.ArgumentParser(description="Nifty 500 Swing Trading Scanner")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--port",    type=int, default=5000)
    args = parser.parse_args()

    print("\n╔═════════════════════════════════════════════════════════════╗")
    print("  ║     Nifty 500 Swing Trading Scanner — Fyers API             ║")
    print("  ╚═════════════════════════════════════════════════════════════╝")

    if not os.path.isdir(STATIC_DIR):
        print("\n⚠️   React frontend not built. Run:")
        print("     cd frontend && npm install && npm run build\n")
        print("     Or for dev: npm run dev → open http://localhost:5173\n")

    _symbols = fetch_nifty500()
    print("   Auth will run automatically before first market-open scan.")

    summary = get_log_summary()
    print(f"\n📋  Signal log : {summary['total_signals']} signals across {summary['days_logged']} day(s)")
    print(f"    Stocks     : {len(_symbols)}")

    print(f"\n🔄  Starting fixed-slot scan loop "
          f"(09:00–15:30 IST, hourly at {ACTIVE_CHECK_HOURS}) …")
    threading.Thread(target=_scan_loop, daemon=True, name="scan-loop").start()
    _start_market_poller(interval_seconds=5)
    _start_quotes_poller(interval_seconds=15)

    url = f"http://localhost:{args.port}"
    print(f"🌐  Opening {url} …")
    print(f"    Press Ctrl+C to stop.\n")
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    app.run(host="0.0.0.0", port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()

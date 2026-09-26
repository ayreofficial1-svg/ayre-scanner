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
import hmac
import math
import threading
import time
import uuid
import webbrowser
import requests

# Fixed UTC+5:30 offset — no pytz/zoneinfo dependency.
# Railway (and most cloud hosts) run UTC; this ensures all market-hour
# comparisons use IST wall-clock time regardless of the host timezone.
_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def _json_safe(value):
    """
    Convert scanner/debug payloads to strict browser-parseable JSON values.

    debug_run.py can render Python/NumPy values directly into files, but API
    responses must not contain NaN/Infinity or NumPy scalar objects.
    """
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except Exception:
            pass
    return str(value)

from auth.fyers_auth import reconnect_fyers, get_cached_token
from config.settings import (
    ACTIVE_CHECK_HOURS, ACTIVE_CHECK_MINUTE, PASSIVE_CHECK_INTERVAL,
    FYERS_APP_ID_FULL, BREADTH_CHECK_HOURS, BREADTH_CHECK_MINUTE,
)
from data.symbols import fetch_nifty500, plain_constituents_for_market, SENSEX30
from data.fyers_stream import stream as _fyers_stream
from scanner.watchlist import (
    load_watchlist, clean_watchlist, save_watchlist,
    load_alert_log, clean_alert_log, save_alert_log,
)
from scanner.engine import run_scan
from scanner.historical import run_historical_scan
from utils.logger import get_log_summary
from data.quotes import fetch_ltp_bulk, fetch_constituents_quotes_bulk, fetch_full_market_breadth
from data.app_signals import (
    load_signals, add_signal, update_signal, delete_signal,
    is_visible as _signal_is_visible, set_push_state,
)
from data.app_devices import (
    register_device, unregister_device, device_count,
)
from alerts import push as push_alerts
from data.app_learn import load_articles, add_article, update_article, delete_article, get_article
from data.app_insights import load_insights, add_insight, update_insight, delete_insight
from data.app_weekly_report import load_reports, add_report, update_report, delete_report
from data.app_sentiment import compute_sentiment
from data.universe_stats import load_universe_stats, save_universe_stats
from data.breadth import load_full_breadth, save_full_breadth
from data.market_close import load_close_snapshot, save_close_snapshot
from config.settings import APP_ASSET_DIR
from config.settings import RA_REGISTRATION_NUMBER, DISCLAIMER

try:
    from flask import Flask, jsonify, request, send_from_directory, session
except ImportError:
    sys.exit("❌  Flask not installed. Run: pip install flask")

try:
    from werkzeug.utils import secure_filename
except ImportError:
    secure_filename = None

try:
    from flask_cors import CORS
    _CORS_AVAILABLE = True
except ImportError:
    _CORS_AVAILABLE = False

# Static folder = nifty_scanner/static/ (React build output)
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or os.environ.get("SESSION_SECRET") or os.urandom(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "").lower() in {"1", "true", "yes"},
)

# ── CORS — required for Flutter web / mobile clients ─────────────────────────
if _CORS_AVAILABLE:
    CORS(app, resources={r"/api/*": {"origins": "*"}})
else:
    @app.after_request
    def _add_cors_headers(response):
        response.headers["Access-Control-Allow-Origin"]  = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
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
    "next_passive_check_time": None,
    # Insights (spec §2): {bare_symbol: {atr_pct, macd_bullish, volume_surge,
    # close}}, captured as a free byproduct of run_scan(); loaded from disk
    # at startup, refreshed every scan.
    "universe_stats"        : {},
    "universe_stats_as_of"  : None,
    # §3: set in _do_scan()'s and _run_backtest_job()'s finally blocks —
    # timestamp of the last time a scan/rescan/backtest finished, used by
    # _fyers_busy_for_extras() to keep new extras off Fyers for a short
    # cooldown after any of the three ends.
    "last_heavy_fyers_op_at": None,
}
_fyers      = None
_symbols    = None
_start_time = time.time()   # for /api/status uptime tracking

# Full Nifty-500 breadth snapshot cache (spec §4), populated at startup from
# disk and refreshed only by _breadth_loop's paced batch — read-only cache
# for GET /api/breadth/full, never fetched at request time.
_breadth_full_lock  = threading.Lock()
_breadth_full_cache : dict | None = None

# Market hours (IST) — scanner runs only within this window.
# NSE regular session opens at 09:15; scans are intentionally fixed to
# 09:30, 10:30, ... 15:30 so the first data pull has a settled opening range.
_MARKET_OPEN  = datetime.time(9,  15)
_MARKET_CLOSE = datetime.time(15, 30)
_POST_CLOSE_PASSIVE_START = datetime.time(16, 0)

# ── End-of-session index close ───────────────────────────────────────────────
# The Fyers WebSocket is shut down after the close, and every live market
# endpoint is fed by it — so without a saved copy the app would have nothing to
# show overnight, at weekends or on holidays. _close_snapshot_loop() keeps one
# snapshot of the last reading: refreshed every _CLOSE_SNAPSHOT_INTERVAL_SECONDS
# during the session, then the INDEX values are read again at 15:30:00 IST — the
# bell — and once more _CLOSE_INDEX_SETTLE_SECONDS later to pick up any final
# tick that arrives just after it. That is the number users see as the day's
# close, until the next session.
#
# This is a read of ticks the WebSocket has already delivered: no REST call, no
# extra Fyers request and no scan. Movers, constituents and the Insights charts
# are NOT re-collected at the close; they keep the last reading they already had
# (the last minute of the session for the live lists, the last scanner run for
# the Insights charts). See data/market_close.py for the file shape.
_CLOSE_SNAPSHOT_INTERVAL_SECONDS = 60
_CLOSE_INDEX_SETTLE_SECONDS      = 30                     # re-read after the bell (0 = off)
_CLOSE_CAPTURE_DEADLINE          = datetime.time(15, 45)  # give up + keep last reading

_close_lock: threading.Lock = threading.Lock()
_close_snapshot: dict | None = None
_close_index_captured_for: datetime.date | None = None  # bell reading taken for this date
_close_capture_done_for: datetime.date | None = None    # settle re-read done for this date

# Free-source market-status checks are cached separately from price snapshots.
# The short active-window TTL lets the app notice market-open transitions without
# polling Yahoo/NSE on every frontend request; the closed TTL keeps overnight
# checks light for a 24/7 process.
_MARKET_STATUS_ACTIVE_TTL = 60
_MARKET_STATUS_CLOSED_TTL = PASSIVE_CHECK_INTERVAL
_YAHOO_FRESH_TICK_MAX_AGE_SECONDS = 5 * 60

# ── Market snapshot cache ─────────────────────────────────────────────────────
_market_lock = threading.Lock()
_market_cache = {
    "data"      : None,
    "expires_at": 0.0,
}
_market_status_cache = {
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
#   "NIFTY 50"   → 50 large-cap NSE stocks
#   "NIFTY BANK" → All Bank Nifty constituents (12 banking stocks)
#   None         → no NSE endpoint is used for this market (see sensex below)
#
# §4.1 fix: Sensex previously set nse_index_param to "NIFTY NEXT 50" as a
# "proxy" — that was a bug, not a real proxy: it silently served Nifty Next
# 50 stocks under the Sensex tab. BSE has no free constituents endpoint, so
# Sensex's 30 stocks are now a maintained hardcoded list
# (data/symbols.py::SENSEX30) instead of borrowed from an unrelated NSE
# index. nse_index_param is None here on purpose — the constituents builder
# skips NSE entirely for this market and goes straight to SENSEX30 + live
# Fyers prices.
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
        "nse_index_param"   : None,
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

_backtest_lock = threading.Lock()
_backtest_jobs: dict[str, dict] = {}


# ── Session authentication ───────────────────────────────────────────────────
_AUTH_PUBLIC_API = {
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/session",
    # Push-token registration. Public by necessity: the mobile app currently
    # runs without its login gate, so a device has no session to present.
    # All it can do is add/remove its own FCM token (validated and capped in
    # data/app_devices.py); it cannot read anything or trigger a send.
    "/api/devices/register",
    "/api/devices/unregister",
}


def _auth_credentials_configured() -> bool:
    return bool(_configured_users())


def _configured_users() -> dict[str, str]:
    """
    Return configured username -> password entries.

    Railway env:
      SCANNER_USERS=alice:password1,bob:password2

    Backward-compatible fallback:
      SCANNER_USERNAME=alice
      SCANNER_PASSWORD=password1
    """
    users: dict[str, str] = {}
    raw_users = os.environ.get("SCANNER_USERS", "")
    for entry in raw_users.split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        username, password = entry.split(":", 1)
        username = username.strip()
        password = password.strip()
        if username and password:
            users[username] = password

    legacy_username = os.environ.get("SCANNER_USERNAME")
    legacy_password = os.environ.get("SCANNER_PASSWORD")
    if legacy_username and legacy_password:
        users.setdefault(legacy_username, legacy_password)

    return users


def _is_authenticated() -> bool:
    return bool(session.get("authenticated"))


def _display_name() -> str:
    """Human-friendly name for the Home tab greeting. Falls back to username."""
    username = str(session.get("username") or "")
    return username[:1].upper() + username[1:] if username else "there"


def _configured_admin_users() -> set[str]:
    raw = os.environ.get("SCANNER_ADMIN_USERS", "")
    return {u.strip() for u in raw.split(",") if u.strip()}


def _is_admin() -> bool:
    """
    Gate for the website-only write endpoints (POST/DELETE on /api/signals,
    POST on /api/learn). If SCANNER_ADMIN_USERS is unset, any authenticated
    session may write — matching today's single-tier auth. Set it to restrict
    writes to specific usernames once the website has its own admin login.
    """
    if not _is_authenticated():
        return False
    admins = _configured_admin_users()
    if not admins:
        return True
    return session.get("username") in admins


def _is_static_asset(path: str) -> bool:
    return bool(path and os.path.isfile(os.path.join(STATIC_DIR, path)))


def _auth_required() -> bool:
    """
    Sign-in is required by default. Set AUTH_REQUIRED=false (also 0/no/off) to
    let anonymous clients make read-only (GET/HEAD) API calls — used while the
    mobile app runs without its login gate. Writes and scan-triggering POSTs
    still require a session regardless of this flag.
    """
    return os.environ.get("AUTH_REQUIRED", "true").strip().lower() not in {
        "0", "false", "no", "off",
    }


@app.before_request
def _require_authentication():
    if request.method == "OPTIONS":
        return None
    if request.path in _AUTH_PUBLIC_API:
        return None
    if request.path.startswith("/assets/") or _is_static_asset(request.path.lstrip("/")):
        return None
    if not _auth_required() and request.method in ("GET", "HEAD"):
        return None
    if _is_authenticated():
        return None
    if request.path.startswith("/api/"):
        return jsonify({"authenticated": False, "error": "Authentication required"}), 401
    return None


# ─────────────────────────────────────────────────────────────────────────────
# API routes (defined before the catch-all)
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/auth/session")
def api_auth_session():
    payload = {
        "authenticated": _is_authenticated(),
        "configured": _auth_credentials_configured(),
    }
    if _is_authenticated():
        payload["username"] = session.get("username")
        payload["display_name"] = _display_name()
    return jsonify(payload)


@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    if not _auth_credentials_configured():
        return jsonify({
            "authenticated": False,
            "error": "Login is not configured. Set SCANNER_USERS in Railway.",
        }), 503

    payload = request.get_json(silent=True) or {}
    username = str(payload.get("username", ""))
    password = str(payload.get("password", ""))
    users = _configured_users()
    expected_password = users.get(username)

    if expected_password and hmac.compare_digest(password, expected_password):
        session.clear()
        session["authenticated"] = True
        session["username"] = username
        return jsonify({"authenticated": True})

    return jsonify({"authenticated": False, "error": "Invalid username or password"}), 401


@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    session.clear()
    return jsonify({"authenticated": False})

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
    # Force a live market-status check so a stale "closed" cache from before
    # market open (TTL up to PASSIVE_CHECK_INTERVAL = 3600s) never silently
    # blocks a manual rescan during trading hours.
    fresh_status = _free_market_status(force=True)
    if not (_is_market_open() and fresh_status.get("status") == "open"):
        return jsonify({
            "status": "market_closed",
            "message": "Manual scans are allowed only while free sources confirm the market is open.",
            "market_status": fresh_status,
        }), 409
    _state["scanning"] = True
    threading.Thread(target=_do_scan, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/api/backtest/scan", methods=["POST"])
@app.route("/api/debug/scan", methods=["POST"])
def api_backtest_scan():
    payload = request.get_json(silent=True) or {}
    date_value = str(payload.get("date", "")).strip()
    try:
        target_date = datetime.date.fromisoformat(date_value)
    except ValueError:
        return jsonify({"error": "Enter a valid date in YYYY-MM-DD format."}), 400

    today_ist = datetime.datetime.now(_IST).date()
    if target_date > today_ist:
        return jsonify({"error": "Backtests cannot run for a future date."}), 400

    job_id = uuid.uuid4().hex
    with _backtest_lock:
        _backtest_jobs[job_id] = {
            "job_id": job_id,
            "status": "running",
            "date": target_date.isoformat(),
            "created_at": datetime.datetime.now(_IST).isoformat(),
            "result": None,
            "error": None,
        }

    threading.Thread(
        target=_run_backtest_job,
        args=(job_id, target_date),
        daemon=True,
        name=f"backtest-{job_id[:8]}",
    ).start()

    print(f"🧪  Backtest job queued: id={job_id} date={target_date.isoformat()}")
    return jsonify({
        "job_id": job_id,
        "status": "running",
        "scanning": True,
        "scan_time": f"Backtest queued for {target_date.strftime('%d %b %Y')}",
        "total_scanned": 0,
        "total_attempted": 0,
        "signals": [],
        "watchlist_items": [],
        "error": None,
    }), 202


@app.route("/api/backtest/status/<job_id>")
def api_backtest_status(job_id: str):
    with _backtest_lock:
        job = _backtest_jobs.get(job_id)
        if not job:
            return jsonify({"error": "Backtest job not found"}), 404
        if job["status"] == "done":
            result = job["result"]
            n_bt = len(result.get("backtest_results") or [])
            print(f"Backtest status served: id={job_id} status=done backtest_results={n_bt}")
            return jsonify(result)
        if job["status"] == "error":
            return jsonify({
                "job_id": job_id,
                "status": "error",
                "scanning": False,
                "scan_time": None,
                "total_scanned": 0,
                "total_attempted": 0,
                "signals": [],
                "watchlist_items": [],
                "backtest_results": [],
                "error": job["error"],
            }), 500
        return jsonify({
            "job_id": job_id,
            "status": "running",
            "scanning": True,
            "scan_time": f"Backtest running for {job['date']}",
            "total_scanned": 0,
            "total_attempted": 0,
            "signals": [],
            "watchlist_items": [],
            "backtest_results": [],
            "error": None,
        })


def _run_backtest_job(job_id: str, target_date: datetime.date) -> None:
    global _fyers, _symbols

    try:
        print(f"🧪  Backtest API request: date={target_date.isoformat()}")
        if _symbols is None:
            _symbols = fetch_nifty500()
        if _fyers is None:
            _fyers = reconnect_fyers()

        result = run_historical_scan(_fyers, _symbols, target_date)
        scan_time = datetime.datetime.now(_IST).strftime("%d %b %Y %H:%M:%S")
        requested = datetime.date.fromisoformat(result["requested_date"]).strftime("%d %b %Y")
        resolved = (
            datetime.date.fromisoformat(result["resolved_date"]).strftime("%d %b %Y")
            if result.get("resolved_date") else None
        )
        label = f"Backtest — requested {requested}"
        if resolved and resolved != requested:
            label += f", resolved {resolved}"

        report = result["report"]
        print(
            "🧪  Backtest API response: "
            f"attempted={report.get('attempted', 0)} "
            f"evaluated={report.get('evaluated', 0)} "
            f"signals={len(result['signals'])} "
            f"watchlist={len(result['watchlist_items'])} "
            f"status_counts={report.get('status_counts', {})}"
        )
        payload = _json_safe({
            "job_id": job_id,
            "status": "done",
            "scanning": False,
            "scan_time": f"{label} · ran {scan_time}",
            "total_scanned": report.get("evaluated", report.get("valid", 0)),
            "total_attempted": report.get("attempted", 0),
            "signals": result["signals"],
            "watchlist_items": result["watchlist_items"],
            "backtest_results": list(result.get("results", {}).values()),
            "error": result.get("error"),
            "debug": {
                "requested_date": result.get("requested_date"),
                "resolved_date": result.get("resolved_date"),
                "window_start": result.get("window_start"),
                "runtime_seconds": report.get("runtime_seconds"),
                "status_counts": report.get("status_counts", {}),
                "stage_counts": report.get("stage_counts", {}),
                "daily_valid": report.get("daily_valid", report.get("valid", 0)),
                "prepared": report.get("prepared"),
                "dropped_short": report.get("dropped_short", 0),
                "quality_filtered": report.get("quality_filtered", 0),
                "weekly_valid": report.get("weekly_valid", 0),
                "weekly_no_data": report.get("weekly_no_data", 0),
                "weekly_filtered": report.get("weekly_filtered", 0),
                "failed": report.get("failed", 0),
                "no_data": report.get("no_data", 0),
                "recovered": report.get("recovered", 0),
                "persistent_recovered": report.get("persistent_recovered", 0),
                "persistent_retries": report.get("persistent_retries", 0),
                "evaluation_errors": report.get("evaluation_errors", []),
                "debug_outputs": report.get("debug_outputs", {}),
            },
        })
        n_signals  = len(payload.get("signals", []))
        n_watchlist = len(payload.get("watchlist_items", []))
        n_results  = len(payload.get("backtest_results", []))
        print(
            "🧪  Backtest API payload ready: "
            f"signals={n_signals} "
            f"watchlist={n_watchlist} "
            f"backtest_results={n_results}"
        )
        # Defensive: ensure backtest_results is always a list, never None or missing.
        # The frontend gates its results table on this key being a non-empty list.
        if not isinstance(payload.get("backtest_results"), list):
            payload["backtest_results"] = []
            print("🧪  WARNING: backtest_results was not a list — reset to []")

        with _backtest_lock:
            if job_id in _backtest_jobs:
                _backtest_jobs[job_id]["status"] = "done"
                _backtest_jobs[job_id]["result"] = payload
                print(f"🧪  Backtest job stored: id={job_id} backtest_results={n_results}")
    except Exception as e:
        print(f"🧪  Backtest job failed: id={job_id} error={e}")
        with _backtest_lock:
            if job_id in _backtest_jobs:
                _backtest_jobs[job_id]["status"] = "error"
                _backtest_jobs[job_id]["error"] = str(e)
    finally:
        # §3: marks "a heavy Fyers op just finished" for the breadth
        # poller's busy-guard cooldown — set regardless of success/failure.
        _state["last_heavy_fyers_op_at"] = time.time()


def _format_ist(dt: datetime.datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(_IST).strftime("%d %b %Y %H:%M:%S")


@app.route("/api/status")
def api_status():
    """
    Returns system health, uptime, next scheduled scan time, and next passive
    free-source market-status check.

    Response shape
    ──────────────
    {
        "status"         : "live" | "scanning",
        "market_open"    : true,
        "market_status"  : {"status": "open", "source": "yahoo", ...},
        "uptime_seconds" : 3600,
        "signals_logged" : 256,
        "logs_days"      : 42,
        "next_scan"      : "05 Apr 2026 10:30:00",
        "next_passive_check": "05 Apr 2026 16:00:00",
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
    market_status = _free_market_status()

    return jsonify({
        "status"          : "scanning" if _state["scanning"] else "live",
        "market_open"     : market_status.get("status") == "open" and _is_market_open(),
        "market_status"   : market_status,
        "uptime_seconds"  : uptime,
        "signals_logged"  : summary["total_signals"],
        "logs_days"       : summary["days_logged"],
        "next_scan"       : _state.get("next_scan_time"),
        "next_passive_check": _state.get("next_passive_check_time"),
        "close_snapshot"  : _close_snapshot_summary(),
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
        "source"    : "fyers_ws" | "fyers" | "nse" | "yahoo" | "fallback",
        "updated_at": "<ISO timestamp>"
    }

    "fyers_ws" means this came straight from the live WebSocket feed (the
    common case during market hours) — "fyers"/"nse"/"yahoo" mean the
    WebSocket hadn't ticked yet and a one-off REST call filled in instead.

    Flutter integration notes
    ──────────────────────────
    - Poll this endpoint at the frontend's display refresh cadence.
      Backend caching and Fyers gating keep external requests bounded.
    - markets list is always in fixed order: Nifty → Sensex → Bank Nifty.
    - Infinite carousel: nextIndex = (currentIndex + 1) % markets.length
    - On tap, pass markets[i].key to GET /api/market/<key>/constituents.
    """
    return jsonify(_get_market_snapshot())


# ── Movers — gainers / losers / most-active, straight from Fyers ─────────────
_MOVERS_CACHE_TTL = 15  # seconds; only matters for the REST-fallback path
_movers_lock  = threading.Lock()
_movers_cache = {"data": None, "expires_at": 0.0}


def _get_movers_payload() -> dict:
    """
    {"gainers": [...], "losers": [...], "most_active": [...], "source": ...}

    Primary source is the live Fyers WebSocket (_fyers_stream.movers()) —
    ranked across the full deduplicated Nifty 50 + Sensex 30 + Bank Nifty
    universe, computed from ticks already in memory, no extra API calls.

    Falls back to deriving the same ranking from the (REST/NSE-backed,
    60s-cached) constituents endpoints when the socket hasn't produced
    enough ticks yet — e.g. just after startup — cached for 15s so rapid
    polling never re-derives on every request.
    """
    now = time.time()
    if _market_is_closed_now():
        closed = _closing_movers_payload()
        if closed is not None:
            return closed

    if _fyers_market_data_allowed():
        try:
            live = _fyers_stream.movers()
        except Exception:
            live = None
        if live:
            return {**live, "source": "fyers_ws", "updated_at": datetime.datetime.now().isoformat()}

    with _movers_lock:
        cached = _movers_cache["data"]
        if cached is not None and _movers_cache["expires_at"] > now:
            return cached

    rows: list[dict] = []
    for market_cfg in MARKETS:
        payload = _get_constituents(market_cfg)
        for s in payload.get("stocks", []):
            if s.get("last_price") is None or s.get("change_pct") is None:
                continue
            rows.append(s)

    # De-duplicate by symbol (a stock can appear in more than one index).
    by_symbol = {s["symbol"]: s for s in rows if s.get("symbol")}
    rows = list(by_symbol.values())

    if not rows:
        # No priced stocks from any source — show the last saved reading
        # rather than three empty lists.
        closed = _closing_movers_payload()
        if closed is not None:
            return closed

    gainers = sorted(rows, key=lambda r: r["change_pct"], reverse=True)[:10]
    losers  = sorted(rows, key=lambda r: r["change_pct"])[:10]
    active  = sorted(rows, key=lambda r: r.get("volume") or 0, reverse=True)[:10]

    data = {
        "gainers"    : gainers,
        "losers"     : losers,
        "most_active": active,
        "source"     : "derived",
        "updated_at" : datetime.datetime.now().isoformat(),
    }
    with _movers_lock:
        _movers_cache["data"]       = data
        _movers_cache["expires_at"] = now + _MOVERS_CACHE_TTL
    return data


@app.route("/api/market/gainers")
def api_market_gainers():
    """Top 10 gainers across Nifty 50 + Sensex 30 + Bank Nifty, deduplicated."""
    payload = _get_movers_payload()
    return jsonify({
        "stocks"     : payload["gainers"],
        "source"     : payload["source"],
        "updated_at" : payload["updated_at"],
        "market_closed": payload.get("market_closed", False),
    })


@app.route("/api/market/losers")
def api_market_losers():
    """Top 10 losers across Nifty 50 + Sensex 30 + Bank Nifty, deduplicated."""
    payload = _get_movers_payload()
    return jsonify({
        "stocks"     : payload["losers"],
        "source"     : payload["source"],
        "updated_at" : payload["updated_at"],
        "market_closed": payload.get("market_closed", False),
    })


@app.route("/api/market/most-active")
def api_market_most_active():
    """Top 10 stocks by traded volume across Nifty 50 + Sensex 30 + Bank Nifty, deduplicated."""
    payload = _get_movers_payload()
    return jsonify({
        "stocks"     : payload["most_active"],
        "source"     : payload["source"],
        "updated_at" : payload["updated_at"],
        "market_closed": payload.get("market_closed", False),
    })


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

    Poll every 15 s only while source-confirmed market data is allowed. Rapid
    frontend polling is safe because this endpoint returns the backend cache.
    """
    with _quotes_lock:
        return jsonify({
            "quotes"    : dict(_quotes_cache),
            "updated_at": _quotes_updated_at,
        })


def _content_fields(payload: dict) -> dict:
    fields = {}
    for key in (
        "enabled",
        "featured",
        "pinned",
        "display_order",
        "category",
        "image_url",
        "icon",
        "tone",
        "start_at",
        "end_at",
        "tags",
    ):
        if key in payload:
            fields[key] = payload.get(key)
    if "published" in payload and "enabled" not in fields:
        fields["enabled"] = bool(payload.get("published"))
    if "display_order" in fields:
        try:
            fields["display_order"] = int(fields["display_order"] or 0)
        except (TypeError, ValueError):
            fields["display_order"] = 0
    if "tags" in fields and isinstance(fields["tags"], str):
        fields["tags"] = [t.strip() for t in fields["tags"].split(",") if t.strip()]
    return fields


@app.route("/api/uploads", methods=["POST"])
def api_upload_asset():
    if not _is_admin():
        return jsonify({"error": "Admin access required"}), 403
    if "file" not in request.files:
        return jsonify({"error": "file is required"}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "filename is required"}), 400
    filename = secure_filename(file.filename) if secure_filename else os.path.basename(file.filename)
    ext = os.path.splitext(filename)[1].lower()
    if ext not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}:
        return jsonify({"error": "Unsupported asset type"}), 400
    os.makedirs(APP_ASSET_DIR, exist_ok=True)
    stored = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(APP_ASSET_DIR, stored)
    file.save(path)
    return jsonify({"url": f"/uploads/{stored}"})


# ─────────────────────────────────────────────────────────────────────────────
# Consumer app: Signals tab (admin-curated stock picks)
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/signals", methods=["GET"])
def api_signals_list():
    """
    List of admin-picked stocks with live price/% change attached.

    Response shape
    ──────────────
    {
        "signals": [
            {
                "id"         : "b3f1...",
                "symbol"     : "RELIANCE",
                "rationale"  : "Breakout above SMA44 with rising volume.",
                "date_added" : "2026-07-03",
                "added_by"   : "raghav",
                "last_price" : 2850.45,
                "change_pct" : 1.23
            },
            ...
        ],
        "updated_at": "<ISO timestamp>"
    }
    """
    global _fyers
    include_hidden = request.args.get("all") == "1" and _is_admin()
    signals = load_signals(active_only=not include_hidden)

    quotes: dict[str, dict] = {}
    symbols = [s["symbol"] for s in signals if s.get("symbol")]
    if symbols and _fyers is not None and _fyers_market_data_allowed():
        quotes = fetch_constituents_quotes_bulk(_fyers, symbols)

    enriched = []
    for s in signals:
        q = quotes.get(str(s.get("symbol", "")).upper(), {})
        enriched.append({
            **s,
            "last_price": q.get("last_price"),
            "change_pct": q.get("change_pct"),
        })

    return jsonify({
        "signals"   : enriched,
        "updated_at": datetime.datetime.now(_IST).isoformat(),
    })


@app.route("/api/signals", methods=["POST"])
def api_signals_add():
    """Website-only. Creates or updates an admin-curated stock recommendation."""
    if not _is_admin():
        return jsonify({"error": "Admin access required"}), 403

    payload = request.get_json(silent=True) or {}
    signal_id = payload.get("id")
    symbol    = str(payload.get("symbol", "")).strip()
    rationale = str(payload.get("rationale", "")).strip()
    if not symbol:
        return jsonify({"error": "symbol is required"}), 400

    fields = _content_fields(payload)
    if signal_id:
        previous = next(
            (s for s in load_signals() if s.get("id") == str(signal_id)), None
        )
        was_visible = bool(previous and _signal_is_visible(previous))
        entry = update_signal(str(signal_id), symbol=symbol, rationale=rationale, **fields)
        if entry is None:
            return jsonify({"error": "Signal not found"}), 404
        _push_signal_if_due(entry, was_visible=was_visible)
        return jsonify({"signal": entry})

    entry = add_signal(
        symbol=symbol,
        rationale=rationale,
        added_by=session.get("username"),
        **fields,
    )
    _push_signal_if_due(entry, was_visible=False)
    return jsonify({"signal": entry}), 201


@app.route("/api/signals/<string:signal_id>", methods=["DELETE"])
def api_signals_delete(signal_id: str):
    """
    Website-only. Deactivates the signal (sets active=False) rather than
    deleting the record — GET /api/signals only returns active signals, so
    the effect on the app is the same, but history is preserved.
    """
    if not _is_admin():
        return jsonify({"error": "Admin access required"}), 403

    removed = delete_signal(signal_id)
    if not removed:
        return jsonify({"error": "Signal not found (or already inactive)"}), 404
    return jsonify({"deleted": True, "id": signal_id})


# ─────────────────────────────────────────────────────────────────────────────
# Consumer app: push notifications (FCM)
# ─────────────────────────────────────────────────────────────────────────────

def _push_signal_if_due(entry: dict, was_visible: bool) -> None:
    """
    Announce an admin-published signal on the users' phones — exactly once.

    Called after the signal endpoints save a signal. `was_visible` is whether
    the signal was already live *before* this save (always False for a brand
    new one), which is what separates "just published" from "an admin fixed a
    typo in a signal that has been live for weeks".

      • already announced (push_sent_at)      → nothing
      • live now, and newly so                 → push, then record it
      • enabled but scheduled (start_at later) → mark pending; the loop below
                                                 sends it when it goes live
    """
    try:
        signal_id = entry.get("id")
        if not signal_id or entry.get("push_sent_at"):
            return
        if not entry.get("enabled", True):
            return
        if not push_alerts.is_configured():
            return      # nothing was sent, so don't record it as sent
        if _signal_is_visible(entry):
            if was_visible and not entry.get("push_pending"):
                return
            push_alerts.notify_new_signal(entry)
            set_push_state(signal_id, sent=True)
        elif entry.get("start_at"):
            set_push_state(signal_id, pending=True)
    except Exception as e:      # a push problem must never fail a publish
        print(f"   ⚠️   Push: could not process signal — {e}")


def _push_pending_loop() -> None:
    """Send the push for scheduled signals once their start_at passes."""
    while True:
        time.sleep(60)
        try:
            if not push_alerts.is_configured():
                continue
            for signal in load_signals():
                if (
                    signal.get("push_pending")
                    and not signal.get("push_sent_at")
                    and _signal_is_visible(signal)
                ):
                    push_alerts.notify_new_signal(signal)
                    set_push_state(signal["id"], sent=True)
        except Exception as e:
            print(f"   ⚠️   Push: pending-signal check failed — {e}")


@app.route("/api/devices/register", methods=["POST"])
def api_devices_register():
    """
    Called by the mobile app whenever it obtains (or refreshes) its FCM token,
    and again when the user changes a notification preference.

    Body: {"token": "...", "platform": "android"|"ios",
           "signals": true, "app_version": "2.0.0"}
    """
    payload = request.get_json(silent=True) or {}
    entry = register_device(
        token=str(payload.get("token") or ""),
        platform=str(payload.get("platform") or ""),
        signals=payload.get("signals", True) is not False,
        app_version=payload.get("app_version"),
    )
    if entry is None:
        return jsonify({"error": "Invalid token, or device limit reached"}), 400
    return jsonify({"registered": True})


@app.route("/api/devices/unregister", methods=["POST"])
def api_devices_unregister():
    """Called when the user switches push off in Settings."""
    payload = request.get_json(silent=True) or {}
    unregister_device(str(payload.get("token") or ""))
    return jsonify({"registered": False})


@app.route("/api/push/status", methods=["GET"])
def api_push_status():
    """Website-only. Is push set up, and how many phones would receive it?"""
    if not _is_admin():
        return jsonify({"error": "Admin access required"}), 403
    return jsonify({
        "configured": push_alerts.is_configured(),
        "devices"   : device_count(),
    })


@app.route("/api/push/send", methods=["POST"])
def api_push_send():
    """
    Website-only. Send a custom notification to every registered phone — also
    the quickest way to test the whole chain end to end.
    Body: {"title": "...", "body": "..."}
    """
    if not _is_admin():
        return jsonify({"error": "Admin access required"}), 403
    if not push_alerts.is_configured():
        return jsonify({
            "error": "Push is not configured. Set FIREBASE_SERVICE_ACCOUNT_JSON "
                     "(and install firebase-admin) on the server.",
        }), 503

    payload = request.get_json(silent=True) or {}
    title = str(payload.get("title") or "").strip()
    body  = str(payload.get("body") or "").strip()
    if not title or not body:
        return jsonify({"error": "title and body are required"}), 400

    push_alerts.broadcast(title, body)
    return jsonify({"queued": True, "devices": device_count()}), 202


# ─────────────────────────────────────────────────────────────────────────────
# Consumer app: Insights tab (sentiment gauge placeholder)
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/sentiment", methods=["GET"])
def api_sentiment():
    """
    Sentiment gauge value (0-100), computed live from the cached full
    Nifty-500 breadth snapshot via data/app_sentiment.py::compute_sentiment
    — see that module's docstring for the formula. This reads the same
    in-memory `_breadth_full_cache` GET /api/breadth/full serves, so this
    route makes no Fyers call and no disk read of its own; the manual
    admin-set value (formerly load_sentiment/save_sentiment) was retired
    in Phase 1/2.

    Also merges in, when the live Fyers feed has ticked enough stocks,
    real advances/declines/unchanged counted across the full tracked
    universe (Nifty 50 + Sensex 30 + Bank Nifty, deduplicated) — this is a
    smaller, different universe from the Nifty-500 breadth the score
    itself is computed from, and is included purely as supplementary
    display data, exactly as before.

    Response shape
    ──────────────
    {
        "sentiment" : 65 | null,
        "updated_at": "<as_of timestamp>" | null,
        "note"      : "312 of 490 stocks advancing" | null,
        "advances"  : 312 | absent,
        "declines"  : 178 | absent,
        "unchanged" : 10  | absent
    }

    sentiment/updated_at/note are null together when there's no breadth
    snapshot yet or coverage is too thin to trust (see compute_sentiment).
    advances/declines/unchanged are omitted entirely (not null) until the
    live WebSocket has enough data — the Flutter app already falls back to
    its own locally-computed breadth in that case, so omitting rather than
    sending nulls/zeros avoids it briefly showing "0 advances".
    """
    with _breadth_full_lock:
        breadth_snapshot = dict(_breadth_full_cache) if _breadth_full_cache else None
    data = compute_sentiment(breadth_snapshot)
    try:
        live_breadth = _fyers_stream.breadth()
    except Exception:
        live_breadth = None
    if live_breadth:
        data = {**data, **live_breadth}
    elif _market_is_closed_now():
        closing_breadth = _closing_breadth()
        if closing_breadth:
            data = {**data, **closing_breadth}
    return jsonify(data)


@app.route("/api/breadth/full")
def api_breadth_full():
    """
    Full Nifty-500 breadth (spec §4.6) — a pure cache read for the
    frontend. The 10-call Fyers fetch happens only in the background
    poller (_breadth_loop), never on request.
    """
    with _breadth_full_lock:
        data = dict(_breadth_full_cache) if _breadth_full_cache else None
    if not data:
        return jsonify({
            "as_of": None,
            "advances": 0,
            "declines": 0,
            "unchanged": 0,
            "avg_change_pct": 0.0,
            "coverage": 0,
        })
    return jsonify(data)


@app.route("/api/insights/volatility")
def api_insights_volatility():
    """
    Histogram buckets of ATR% across all tracked stocks (spec §2.4).
    Cache-only — reads _state["universe_stats"], no Fyers calls at
    request time. Refreshes at scan cadence (up to 7×/day).
    """
    stats = _state.get("universe_stats") or {}
    buckets = {"0-1%": 0, "1-2%": 0, "2-3%": 0, "3%+": 0}
    for row in stats.values():
        atr_pct = row.get("atr_pct")
        if atr_pct is None:
            continue
        if atr_pct < 1:
            buckets["0-1%"] += 1
        elif atr_pct < 2:
            buckets["1-2%"] += 1
        elif atr_pct < 3:
            buckets["2-3%"] += 1
        else:
            buckets["3%+"] += 1
    return jsonify({
        "buckets": buckets,
        "as_of": _state.get("universe_stats_as_of"),
    })


@app.route("/api/insights/momentum")
def api_insights_momentum():
    """
    Bullish/bearish MACD tilt across all tracked stocks (spec §2.4).
    Cache-only, same as /api/insights/volatility.
    """
    stats = _state.get("universe_stats") or {}
    bullish = bearish = 0
    for row in stats.values():
        flag = row.get("macd_bullish")
        if flag is True:
            bullish += 1
        elif flag is False:
            bearish += 1
    return jsonify({
        "bullish": bullish,
        "bearish": bearish,
        "as_of": _state.get("universe_stats_as_of"),
    })


@app.route("/api/insights/volume-surge")
def api_insights_volume_surge():
    """
    Top N stocks by volume surge (today's volume ÷ 20-day average volume),
    descending (spec §2.4). Cache-only, same as the other /api/insights/*
    endpoints. ?limit=N overrides the default of 15 (clamped to 1-50).
    """
    stats = _state.get("universe_stats") or {}
    try:
        limit = int(request.args.get("limit", 15))
    except (TypeError, ValueError):
        limit = 15
    limit = max(1, min(limit, 50))

    rows = [
        {"symbol": sym, "volume_surge": row["volume_surge"], "close": row.get("close")}
        for sym, row in stats.items()
        if row.get("volume_surge") is not None
    ]
    rows.sort(key=lambda r: r["volume_surge"], reverse=True)

    return jsonify({
        "items": rows[:limit],
        "as_of": _state.get("universe_stats_as_of"),
    })


@app.route("/api/compliance")
def api_compliance():
    """
    SEBI Research Analyst registration number and the standard disclaimer
    text (spec: Phase 6, Research Analyst information screen). Both values
    already live in config/settings.py and are reused as-is by
    alerts/notify.py and utils/logger.py — this route just exposes the same
    in-memory strings over HTTP so the app has one real source instead of a
    second, hard-coded copy that could drift. No Fyers call, no scan, no
    per-request file read — this returns two already-loaded config values.
    """
    return jsonify({
        "ra_registration_number": RA_REGISTRATION_NUMBER,
        "disclaimer": DISCLAIMER,
    })


@app.route("/api/insights", methods=["GET"])
def api_insights_list():
    include_hidden = request.args.get("all") == "1" and _is_admin()
    return jsonify({"insights": load_insights(visible_only=not include_hidden)})


@app.route("/api/insights", methods=["POST"])
def api_insights_save():
    if not _is_admin():
        return jsonify({"error": "Admin access required"}), 403
    payload = request.get_json(silent=True) or {}
    insight_id = payload.get("id")
    title = str(payload.get("title") or "").strip()
    body = str(payload.get("body") or "").strip()
    if not title or not body:
        return jsonify({"error": "title and body are required"}), 400
    fields = {"title": title, "body": body, **_content_fields(payload)}
    if insight_id:
        entry = update_insight(str(insight_id), **fields)
        if entry is None:
            return jsonify({"error": "Insight not found"}), 404
        return jsonify({"insight": entry})
    return jsonify({"insight": add_insight(**fields)}), 201


@app.route("/api/insights/<string:insight_id>", methods=["DELETE"])
def api_insights_delete(insight_id: str):
    if not _is_admin():
        return jsonify({"error": "Admin access required"}), 403
    removed = delete_insight(insight_id)
    if not removed:
        return jsonify({"error": "Insight not found"}), 404
    return jsonify({"deleted": True, "id": insight_id})


# ─────────────────────────────────────────────────────────────────────────────
# Consumer app: Learn tab (educational articles)
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/learn", methods=["GET"])
def api_learn_list():
    """
    Flat list of published learn articles, newest first. Drafts
    (published=false) are excluded — this is what the consumer app reads.

    Response shape
    ──────────────
    {
        "articles": [
            { "id": "a1c2...", "title": "...", "body": "...",
              "category": null, "published": true,
              "created_at": "...", "updated_at": "..." },
            ...
        ]
    }
    """
    include_hidden = request.args.get("all") == "1" and _is_admin()
    return jsonify({"articles": load_articles(published_only=not include_hidden)})


@app.route("/api/learn/<string:article_id>", methods=["GET"])
def api_learn_get(article_id: str):
    """
    Full body of a single published article. 404s for unknown ids and for
    drafts (published=false) — same visibility rule as the list endpoint.

    Response shape
    ──────────────
    { "article": { "id": "...", "title": "...", "body": "...", ... } }
    """
    article = get_article(article_id, published_only=True)
    if article is None:
        return jsonify({"error": "Article not found"}), 404
    return jsonify({"article": article})


@app.route("/api/learn", methods=["POST"])
def api_learn_add():
    """
    Website-only. Body: {"title": "...", "body": "...", "category": "...",
    "published": true}. "published" defaults to true if omitted. Pass "id"
    in the body to edit an existing article instead of creating one.
    """
    if not _is_admin():
        return jsonify({"error": "Admin access required"}), 403

    payload = request.get_json(silent=True) or {}
    article_id = payload.get("id")
    title      = payload.get("title")
    body       = payload.get("body")
    category   = payload.get("category")
    published  = payload.get("published")

    if article_id:
        updated = update_article(
            str(article_id),
            title,
            body,
            category,
            published,
            **_content_fields(payload),
        )
        if updated is None:
            return jsonify({"error": "Article not found"}), 404
        return jsonify({"article": updated})

    title = str(title or "").strip()
    body  = str(body or "").strip()
    if not title or not body:
        return jsonify({"error": "title and body are required"}), 400

    entry = add_article(
        title=title,
        body=body,
        category=category,
        published=True if published is None else bool(published),
        **_content_fields(payload),
    )
    return jsonify({"article": entry}), 201


@app.route("/api/learn/<string:article_id>", methods=["DELETE"])
def api_learn_delete(article_id: str):
    if not _is_admin():
        return jsonify({"error": "Admin access required"}), 403
    removed = delete_article(article_id)
    if not removed:
        return jsonify({"error": "Article not found"}), 404
    return jsonify({"deleted": True, "id": article_id})


# ─────────────────────────────────────────────────────────────────────────────
# Consumer app: Weekly Report (admin-entered historical performance)
#
# Admin-entered, not computed — there is no code anywhere in this repo that
# watches a fired signal afterwards to determine whether price later hit a
# target or a stop loss (see IMPLEMENTATION_SPEC_weekly_report_and_
# sentiment.md §A.4). The owner enters the week, its stocks, profit %, and
# outcome by hand on the website; these three routes just store and serve
# that, following the exact same GET/POST/DELETE shape as /api/learn.
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/weekly-report", methods=["GET"])
def api_weekly_report_list():
    """
    List of weekly performance reports, newest week first. Hidden reports
    (enabled=false) are excluded unless ?all=1 is passed by an admin
    session — same convention as /api/learn and /api/insights.

    Response shape
    ──────────────
    {
        "reports": [
            { "id": "...", "week_start": "2026-09-06", "week_end": "2026-09-12",
              "enabled": true,
              "stocks": [ { "symbol": "RELIANCE", "profit_pct": 4.2,
                            "outcome": "target" } ],
              "created_at": "...", "updated_at": "..." },
            ...
        ]
    }
    """
    include_hidden = request.args.get("all") == "1" and _is_admin()
    return jsonify({"reports": load_reports(enabled_only=not include_hidden)})


@app.route("/api/weekly-report", methods=["POST"])
def api_weekly_report_add():
    """
    Website-only. Body: {"week_start": "2026-09-06", "week_end": "2026-09-12",
    "stocks": [{"symbol": "RELIANCE", "profit_pct": 4.2, "outcome": "target"}]}.
    Pass "id" in the body to edit an existing report instead of creating one
    (same convention /api/learn uses to distinguish create vs. update).

    The full "stocks" list replaces whatever was previously stored for that
    report on every save — simplest, and matches how
    app_signals.py::save_signals persists its whole list at once, rather
    than supporting incremental "add one row" edits.

    Each row's "outcome" must be exactly "target" or "stop_loss" — no other
    spelling accepted, so the value means the same thing in the backend,
    the website, and (Phase 5) the Flutter app. Invalid rows reject the
    whole save with 400 rather than silently dropping or reinterpreting one
    row, since this is a small admin-entered form, not a bulk import.
    """
    if not _is_admin():
        return jsonify({"error": "Admin access required"}), 403

    payload    = request.get_json(silent=True) or {}
    report_id  = payload.get("id")
    week_start = str(payload.get("week_start") or "").strip()
    week_end   = str(payload.get("week_end") or "").strip()
    raw_stocks = payload.get("stocks")

    if not week_start or not week_end:
        return jsonify({"error": "week_start and week_end are required"}), 400
    if not isinstance(raw_stocks, list) or not raw_stocks:
        return jsonify({"error": "at least one stock row is required"}), 400

    stocks = []
    for row in raw_stocks:
        if not isinstance(row, dict):
            return jsonify({"error": "each stock row must be an object"}), 400
        symbol  = str(row.get("symbol") or "").strip().upper()
        outcome = str(row.get("outcome") or "").strip().lower()
        if not symbol:
            return jsonify({"error": "each stock row needs a symbol"}), 400
        if outcome not in ("target", "stop_loss"):
            return jsonify({
                "error": f"invalid outcome for {symbol}: must be 'target' or 'stop_loss'"
            }), 400
        try:
            profit_pct = float(row.get("profit_pct"))
        except (TypeError, ValueError):
            return jsonify({"error": f"invalid profit_pct for {symbol}"}), 400
        stocks.append({"symbol": symbol, "profit_pct": profit_pct, "outcome": outcome})

    if report_id:
        updated = update_report(
            str(report_id),
            week_start,
            week_end,
            stocks,
            **_content_fields(payload),
        )
        if updated is None:
            return jsonify({"error": "Report not found"}), 404
        return jsonify({"report": updated})

    entry = add_report(
        week_start=week_start,
        week_end=week_end,
        stocks=stocks,
        **_content_fields(payload),
    )
    return jsonify({"report": entry}), 201


@app.route("/api/weekly-report/<string:report_id>", methods=["DELETE"])
def api_weekly_report_delete(report_id: str):
    """
    Admin-only. Soft-deletes: sets enabled=false rather than removing the
    entry, so past weekly performance records stay in history (same
    convention app_signals.py's DELETE uses) — GET /api/weekly-report
    (without ?all=1) simply stops returning it.
    """
    if not _is_admin():
        return jsonify({"error": "Admin access required"}), 403
    removed = delete_report(report_id)
    if not removed:
        return jsonify({"error": "Report not found"}), 404
    return jsonify({"deleted": True, "id": report_id})


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
# "Fyers busy" guard — shared by everything in the breadth poller (spec §3/§4)
# ─────────────────────────────────────────────────────────────────────────────

_BUSY_COOLDOWN_SECONDS = 20   # buffer after a scan/backtest ends, before
                               # anything new is allowed to fire


def _fyers_busy_for_extras() -> bool:
    """
    True if a scheduled scan, a manual rescan, or a backtest is running
    right now, or finished less than _BUSY_COOLDOWN_SECONDS ago.

    A manual rescan calls the exact same _do_scan() function a scheduled
    scan does, and sets the exact same _state["scanning"] flag — so this
    never needs to distinguish "manual" from "automatic" scans. The
    backtest job is the one genuinely separate path, which is why it gets
    its own check via _backtest_jobs.

    This is a *skip* primitive. _breadth_loop() builds a wait-then-fire
    primitive on top of it (spec §4.4), which is what actually gives the
    breadth poller "pause and continue after scanning is done" behavior
    rather than "skip this cycle and wait an hour."
    """
    if _state.get("scanning"):
        return True
    with _backtest_lock:
        if any(j.get("status") == "running" for j in _backtest_jobs.values()):
            return True
    last_finished = _state.get("last_heavy_fyers_op_at")
    if last_finished and (time.time() - last_finished) < _BUSY_COOLDOWN_SECONDS:
        return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Scan
# ─────────────────────────────────────────────────────────────────────────────

def _do_scan():
    global _fyers, _symbols
    _state["scanning"] = True
    _state["error"]    = None
    try:
        if not _fyers_market_data_allowed():
            _state["error"] = "Scan skipped because the market is not source-confirmed open."
            return

        watchlist = clean_watchlist(load_watchlist())
        alert_log = clean_alert_log(load_alert_log())
        save_watchlist(watchlist)
        save_alert_log(alert_log)

        signals, watchlist_items, fetch_report, universe_stats = run_scan(
            fyers     = _fyers,
            symbols   = _symbols,
            interval  = "D",
            watchlist = watchlist,
            alert_log = alert_log,
        )

        _state["signals"]         = signals
        _state["watchlist_items"] = watchlist_items
        _state["scan_time"]       = datetime.datetime.now(_IST).strftime("%d %b %Y %H:%M:%S")
        # Use authoritative counts from the fetch completeness report.
        # total_scanned = symbols that reached conditions.py after optional filters.
        # total_attempted = ground truth len(symbols) — never varies.
        _state["total_scanned"]   = fetch_report.get("evaluated", fetch_report["valid"])
        _state["total_attempted"] = fetch_report["attempted"]

        # Insights (spec §2) — persist so a restart doesn't blank the charts
        # until the next scan, and update the in-memory copy the
        # /api/insights/* endpoints read from.
        _state["universe_stats"] = universe_stats
        saved_stats = save_universe_stats(universe_stats)
        _state["universe_stats_as_of"] = saved_stats["as_of"]

    except Exception as e:
        _state["error"] = str(e)
    finally:
        _state["scanning"] = False
        # §3: marks "a heavy Fyers op just finished" for the breadth
        # poller's busy-guard cooldown — set regardless of success/failure.
        _state["last_heavy_fyers_op_at"] = time.time()


# ─────────────────────────────────────────────────────────────────────────────
# Clock-aligned scan scheduler
# ─────────────────────────────────────────────────────────────────────────────

def _seconds_until_next_active_slot() -> float | None:
    """
    Seconds until the next fixed Active Check slot (HH:30, IST).
    Returns None when all slots for today have passed (after 15:30).

    Guard is >= 0 (not > 5.0) so the current slot is not silently skipped
    in the last few seconds before it fires.  The caller does
    time.sleep(sleep_secs) which degenerates to a no-op for near-zero values,
    then immediately checks _free_sources_confirm_market_open before scanning.
    """
    now = datetime.datetime.now(_IST)
    for h in ACTIVE_CHECK_HOURS:
        target = now.replace(
            hour=h, minute=ACTIVE_CHECK_MINUTE, second=0, microsecond=0
        )
        diff = (target - now).total_seconds()
        if diff >= 0:
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


def _next_scan_slot_after(now: datetime.datetime | None = None) -> datetime.datetime:
    now = now or datetime.datetime.now(_IST)
    for h in ACTIVE_CHECK_HOURS:
        target = now.replace(
            hour=h, minute=ACTIVE_CHECK_MINUTE, second=0, microsecond=0
        )
        if target > now:
            return target
    tomorrow = now + datetime.timedelta(days=1)
    return tomorrow.replace(
        hour=ACTIVE_CHECK_HOURS[0],
        minute=ACTIVE_CHECK_MINUTE,
        second=0,
        microsecond=0,
    )


def _seconds_until_next_scan_slot() -> float:
    now = datetime.datetime.now(_IST)
    return max(0.0, (_next_scan_slot_after(now) - now).total_seconds())


def _next_breadth_slot_after(now: datetime.datetime | None = None) -> datetime.datetime | None:
    """
    Next fixed full-breadth poller slot (HH:45 IST, spec §4.3) still to come
    today. Returns None once today's slots are exhausted — analogous to
    _seconds_until_next_active_slot's None-when-exhausted contract (not
    _next_scan_slot_after's always-wraps-to-tomorrow one), so _breadth_loop's
    "no more slots today" branch has something to detect.
    """
    now = now or datetime.datetime.now(_IST)
    for h in BREADTH_CHECK_HOURS:
        target = now.replace(
            hour=h, minute=BREADTH_CHECK_MINUTE, second=0, microsecond=0
        )
        if target > now:
            return target
    return None


def _clock_hour_at_or_after(now: datetime.datetime) -> datetime.datetime:
    current_hour = now.replace(minute=0, second=0, microsecond=0)
    if current_hour >= now:
        return current_hour
    return current_hour + datetime.timedelta(hours=1)


def _today_at(now: datetime.datetime, clock_time: datetime.time) -> datetime.datetime:
    return now.replace(
        hour=clock_time.hour,
        minute=clock_time.minute,
        second=0,
        microsecond=0,
    )


def _next_passive_status_check_at_or_after(now: datetime.datetime | None = None) -> datetime.datetime:
    """
    Next fixed passive market-status check, including a boundary due now.

    After the regular session, checks are aligned hourly from 16:00 IST. A
    special 09:15 IST pre-open check lets the app authenticate before 09:30
    when Yahoo/NSE indicate the market has opened.
    """
    now = now or datetime.datetime.now(_IST)
    candidates: list[datetime.datetime] = []

    today_preopen = _today_at(now, _MARKET_OPEN)
    if today_preopen >= now:
        candidates.append(today_preopen)

    today_post_close = _today_at(now, _POST_CLOSE_PASSIVE_START)
    if today_post_close >= now:
        candidates.append(today_post_close)

    candidates.append(_clock_hour_at_or_after(now))
    return min(candidate for candidate in candidates if candidate >= now)


def _sleep_until(target: datetime.datetime) -> None:
    time.sleep(max(0.0, (target - datetime.datetime.now(_IST)).total_seconds()))


def _market_status_payload(status: str, source: str, detail: str = "") -> dict:
    return {
        "status": status,
        "source": source,
        "detail": detail,
        "checked_at": datetime.datetime.now(_IST).isoformat(),
    }


def _yahoo_regular_period_contains_now(meta: dict, now: datetime.datetime) -> bool:
    regular = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    try:
        start = datetime.datetime.fromtimestamp(int(regular["start"]), _IST)
        end = datetime.datetime.fromtimestamp(int(regular["end"]), _IST)
    except (KeyError, TypeError, ValueError, OSError):
        return False
    return start <= now <= end


def _latest_yahoo_tick_at(row: dict) -> datetime.datetime | None:
    timestamps = row.get("timestamp") or []
    if not timestamps:
        return None
    try:
        return datetime.datetime.fromtimestamp(int(timestamps[-1]), _IST)
    except (TypeError, ValueError, OSError):
        return None


def _fetch_market_status_from_yahoo() -> dict:
    """
    Lightweight market-state probe using Yahoo Finance only. No Fyers.
    Returns a payload with status 'open' | 'closed' | 'holiday'.
    """
    resp = requests.get(
        "https://query1.finance.yahoo.com/v8/finance/chart/%5ENSEI",
        params={"interval": "1m", "range": "1d"},
        timeout=8,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    resp.raise_for_status()
    result = resp.json().get("chart", {}).get("result") or []
    if not result:
        return _market_status_payload("closed", "yahoo", "empty chart result")

    row = result[0]
    meta = row.get("meta", {})
    state = str(meta.get("marketState", "")).upper()
    if state == "REGULAR":
        return _market_status_payload("open", "yahoo", state)

    now_ist = datetime.datetime.now(_IST)
    latest_tick = _latest_yahoo_tick_at(row)
    if latest_tick is not None:
        tick_age = (now_ist - latest_tick).total_seconds()
        tick_is_fresh = 0 <= tick_age <= _YAHOO_FRESH_TICK_MAX_AGE_SECONDS
        if (
            _MARKET_OPEN <= now_ist.time() <= _MARKET_CLOSE
            and _yahoo_regular_period_contains_now(meta, now_ist)
            and latest_tick.date() == now_ist.date()
            and tick_is_fresh
        ):
            return _market_status_payload(
                "open",
                "yahoo",
                f"fresh 1m tick at {latest_tick.strftime('%H:%M:%S')} IST; "
                f"marketState={state or 'missing'}",
            )

    if latest_tick is None:
        return _market_status_payload("holiday", "yahoo", state or "no ticks")

    return _market_status_payload(
        "closed",
        "yahoo",
        f"{state or 'not regular'}; last tick {latest_tick.strftime('%d %b %Y %H:%M:%S')} IST",
    )


def _fetch_market_status_from_nse() -> dict:
    """
    Free NSE fallback for market-state checks. No Fyers.

    NSE does not expose a dedicated open/closed flag here, so this uses the
    index feed timestamp as confirmation during the regular session window.
    """
    session = _get_nse_session()
    response = session.get("https://www.nseindia.com/api/allIndices", timeout=10)
    response.raise_for_status()
    indices = response.json().get("data", [])
    item = next(
        (
            row for row in indices
            if str(row.get("indexSymbol") or row.get("index") or "").upper().strip()
            == "NIFTY 50"
        ),
        None,
    )
    if not item:
        return _market_status_payload("closed", "nse", "NIFTY 50 missing")

    timestamp = str(item.get("lastUpdateTime") or item.get("timeVal") or "").strip()
    now = datetime.datetime.now(_IST)
    timestamp_is_today = False
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%m-%Y %H:%M:%S"):
        try:
            timestamp_is_today = (
                datetime.datetime.strptime(timestamp, fmt).date() == now.date()
            )
            break
        except ValueError:
            continue
    if _MARKET_OPEN <= now.time() <= _MARKET_CLOSE and timestamp_is_today:
        return _market_status_payload("open", "nse", timestamp)
    if not timestamp:
        return _market_status_payload("holiday", "nse", "no timestamp")
    return _market_status_payload("closed", "nse", timestamp)


def _free_market_status(*, force: bool = False) -> dict:
    """
    Cached market-state check using Yahoo/NSE only. No Fyers calls.
    """
    now = time.time()
    with _market_lock:
        cached = _market_status_cache["data"]
        if not force and cached is not None and _market_status_cache["expires_at"] > now:
            return dict(cached)

    fallback_data = None
    inside_trading_window = _is_market_open()
    for fetcher in (_fetch_market_status_from_yahoo, _fetch_market_status_from_nse):
        try:
            data = fetcher()
            if fallback_data is None:
                fallback_data = data
            if inside_trading_window and data.get("status") != "open":
                continue
            ttl = (
                _MARKET_STATUS_ACTIVE_TTL
                if data.get("status") == "open"
                else _MARKET_STATUS_CLOSED_TTL
            )
            with _market_lock:
                _market_status_cache["data"] = dict(data)
                _market_status_cache["expires_at"] = now + ttl
            return data
        except Exception:
            continue

    data = fallback_data or _market_status_payload(
        "unknown", "fallback", "free sources unavailable"
    )
    with _market_lock:
        _market_status_cache["data"] = dict(data)
        ttl = (
            _MARKET_STATUS_ACTIVE_TTL
            if data.get("status") == "open"
            else min(60, PASSIVE_CHECK_INTERVAL)
        )
        _market_status_cache["expires_at"] = now + ttl
    return data


def _free_sources_confirm_market_open(*, force: bool = False) -> bool:
    return _is_market_open() and _free_market_status(force=force).get("status") == "open"


def _fyers_market_data_allowed() -> bool:
    """
    Fyers may be used only inside regular hours after a free source confirms
    that the market is actually open. This prevents holiday/weekend burn.
    """
    return _free_sources_confirm_market_open(force=False)


# ── Live market WebSocket (real-time advances/declines, movers, index board) ─
# Deliberately fully independent of _scan_loop() — the scanner's own
# schedule, auth retries and scan-slot timing are untouched. This poller
# only *reads* scanner state (_fyers_market_data_allowed(), the cached
# Fyers token) the same way the pre-existing _start_market_poller() and
# _start_quotes_poller() already do; it never calls reconnect_fyers()
# itself and never assigns to any scanner global.
def _build_stream_universe() -> dict[str, list[str]]:
    """
    Bare NSE symbols to track live, per market: Nifty 50 and Bank Nifty
    come from the same NSE-sourced lists the scanner already uses (with
    their existing hardcoded fallbacks); Sensex comes from the maintained
    SENSEX30 list (§4.1) since there is no NSE endpoint for it.
    """
    session = _get_nse_session()
    try:
        nifty_syms = plain_constituents_for_market("nifty", session=session)
    except Exception:
        nifty_syms = []
    try:
        bank_syms = plain_constituents_for_market("bank_nifty", session=session)
    except Exception:
        bank_syms = []
    return {
        "nifty"     : nifty_syms or [],
        "sensex"    : list(SENSEX30),
        "bank_nifty": bank_syms or [],
    }


def _start_fyers_stream_poller(check_interval_seconds: int = 30) -> None:
    """
    Background thread, independent of _scan_loop(), that opens/closes the
    single Fyers Data WebSocket connection based on the same conditions the
    rest of the app already uses (_fyers_market_data_allowed()) and whatever
    token the scanner's own daily re-auth has already cached
    (auth.fyers_auth.get_cached_token() — read-only, triggers no login).

    - Market allowed + a token is cached + we haven't started on this exact
      token yet → (re)configure the symbol universe and start the socket.
    - Market not allowed (closed / not source-confirmed open) and the
      socket is currently connected → stop it, so nothing lingers connected
      to Fyers outside trading hours.
    Never touches _fyers, _scan_loop's schedule, or any scanner state —
    only reads it. A failure here only means live market data falls back
    to the existing NSE/REST-Fyers/Yahoo waterfall; it can't affect scans.
    """
    def _poll():
        started_for_token: str | None = None
        print(
            f"🔌  Fyers live-market-data poller started "
            f"(checks every {check_interval_seconds}s; independent of the scan loop)"
        )
        while True:
            try:
                if _fyers_market_data_allowed():
                    token = get_cached_token()
                    if token and token != started_for_token:
                        _fyers_stream.configure_universe(MARKETS, _build_stream_universe())
                        _fyers_stream.start(f"{FYERS_APP_ID_FULL}:{token}")
                        started_for_token = token
                        print("   🔌 Fyers live market WebSocket (re)started.")
                elif started_for_token is not None and not _close_window_holds_stream():
                    # (kept open through the close window so the final ticks
                    # are received — see _close_window_holds_stream)
                    _fyers_stream.stop()
                    started_for_token = None
                    print("   🔌 Fyers live market WebSocket stopped (market data not allowed).")
            except Exception as e:
                print(f"   ⚠️  fyers_stream poller error: {e}")
            time.sleep(check_interval_seconds)

    t = threading.Thread(target=_poll, daemon=True, name="fyers-stream-poller")
    t.start()


def _scan_loop() -> None:
    """
    Unified scheduler: Passive Check (closed) -> Active Check (open).

    Passive  (closed): checks Yahoo/NSE market status at fixed clock times:
             hourly from 16:00 IST after close, and 09:15 IST before the first
             scan. No Fyers calls.
    Active   (source-confirmed open): fires at fixed half-hour slots
             09:30-15:30. Re-authenticates Fyers once per trading day before
             the first scan.
    """
    global _fyers
    _last_auth_date: datetime.date | None = None

    while True:
        now_ist = datetime.datetime.now(_IST)
        today = now_ist.date()
        _state["next_scan_time"] = _format_ist(_next_scan_slot_after(now_ist))

        # ── PASSIVE WINDOW ────────────────────────────────────────────────────
        if not _is_market_open():
            next_check = _next_passive_status_check_at_or_after(now_ist)
            _state["next_passive_check_time"] = _format_ist(next_check)
            print(
                f"\n🔵  Market outside trading window. "
                f"Next passive status check at {next_check.strftime('%H:%M')} IST "
                f"(next scan slot {_next_scan_slot_after(now_ist).strftime('%H:%M')} IST)."
            )
            _sleep_until(next_check)
            status = _free_market_status(force=True)
            print(
                f"🔵  Passive status check: market {status.get('status')} "
                f"via {status.get('source')} — no Fyers calls."
            )
            continue

        market_status = _free_market_status(force=True)
        if market_status.get("status") != "open":
            status = market_status.get("status", "unknown")
            next_check = _next_passive_status_check_at_or_after(now_ist)
            _state["next_passive_check_time"] = _format_ist(next_check)
            print(
                f"\n🔵  Market {status} via {market_status.get('source')} "
                f"during trading window. Next passive status check at "
                f"{next_check.strftime('%H:%M')} IST — no Fyers calls."
            )
            _sleep_until(next_check)
            continue

        _state["next_passive_check_time"] = None

        # ── ACTIVE WINDOW ─────────────────────────────────────────────────────
        if _last_auth_date != today:
            print(f"\n🔑  Source-confirmed trading day ({today}) - refreshing Fyers token before scan slots …")
            for attempt in range(3):
                try:
                    _fyers = reconnect_fyers()
                    _last_auth_date = today
                    break
                except Exception as e:
                    print(f"   ⚠️  Re-auth attempt {attempt + 1}/3 failed: {e}")
                    time.sleep(30)
            else:
                retry_at = _next_passive_status_check_at_or_after(datetime.datetime.now(_IST))
                _state["next_passive_check_time"] = _format_ist(retry_at)
                print(
                    "   ❌ All re-auth attempts failed - will retry after the next "
                    f"passive status check at {retry_at.strftime('%H:%M')} IST."
                )
                _sleep_until(retry_at)
                continue

        sleep_secs = _seconds_until_next_active_slot()
        if sleep_secs is None:
            secs_to_next = _seconds_until_next_scan_slot()
            h, rem = divmod(int(secs_to_next), 3600)
            m, s = divmod(rem, 60)
            print(
                f"\n🔴  Active scan slots exhausted. "
                f"Next first scan slot in {h}h {m}m {s}s."
            )
            # Sleep until tomorrow's first slot so we don't spin.
            # _seconds_until_next_scan_slot() already wraps to tomorrow
            # when all today's slots have passed.
            time.sleep(secs_to_next)
            continue

        next_dt = datetime.datetime.now(_IST) + datetime.timedelta(seconds=sleep_secs)
        _state["next_scan_time"] = _format_ist(next_dt)
        _, rem = divmod(int(sleep_secs), 3600)
        m, s = divmod(rem, 60)
        print(f"\n⏰  Next active scan at {next_dt.strftime('%H:%M')} IST (in {m}m {s}s)")

        time.sleep(sleep_secs)

        if not _free_sources_confirm_market_open(force=True):
            print("\n🔴  Market is not source-confirmed open — skipping this fixed scan slot.")
            continue

        if _state["scanning"]:
            print("\n⏭️   Previous scan still running — skipping this fixed slot.")
            continue

        print(f"\n🟢  Active check — {datetime.datetime.now(_IST).strftime('%H:%M')} IST")
        _do_scan()


# ─────────────────────────────────────────────────────────────────────────────
# Full Nifty-500 breadth poller (spec §4) — independent thread and schedule
# ─────────────────────────────────────────────────────────────────────────────

def _refresh_full_breadth() -> None:
    """
    The paced 10-call Fyers batch (spec §4.2): advances/declines/unchanged +
    average change % across the full Nifty 500, cached for GET
    /api/breadth/full. Called only from _breadth_loop, only once the busy
    guard has cleared and the market is source-confirmed open.
    """
    global _breadth_full_cache

    if _fyers is None or not _symbols:
        return

    try:
        result = fetch_full_market_breadth(_fyers, _symbols)
    except Exception as e:
        print(f"   ⚠️  Breadth poller: fetch error: {e}")
        return

    if not result:
        print("   ⚠️  Breadth poller: no usable data returned this cycle.")
        return

    data = save_full_breadth(
        advances       = result["advances"],
        declines       = result["declines"],
        unchanged      = result["unchanged"],
        avg_change_pct = result["avg_change_pct"],
        coverage       = result["coverage"],
    )
    with _breadth_full_lock:
        _breadth_full_cache = data

    print(
        f"   📊  Full breadth refreshed: {data['advances']} adv / "
        f"{data['declines']} dec / {data['unchanged']} unch "
        f"(coverage {data['coverage']}/{len(_symbols)})"
    )


_BREADTH_MAX_WAIT_SECONDS = 15 * 60   # generous bound: scans normally take
                                        # 2–4 min; backtests can run longer


def _breadth_loop() -> None:
    """
    Full Nifty-500 breadth poller — fixed hourly slots (spec §4.3), 15
    minutes offset from the scanner's own schedule.

    Runs on its own daemon thread, started independently in main() — never
    called from or blocking on _scan_loop / _do_scan. A slow breadth fetch
    can therefore never delay a scan slot, and a slow scan can never
    silently swallow a breadth slot: two threads, two schedules, one shared
    guard (_fyers_busy_for_extras, which only *reads* state) is the whole
    coordination mechanism.
    """
    print(
        f"📊  Breadth poller started — slots at "
        f"{', '.join(f'{h:02d}:{BREADTH_CHECK_MINUTE:02d}' for h in BREADTH_CHECK_HOURS)} IST"
    )
    while True:
        now = datetime.datetime.now(_IST)

        if not _is_market_open():
            time.sleep(300)   # cheap poll while closed; no Fyers calls here
            continue

        next_slot = _next_breadth_slot_after(now)
        if next_slot is None:
            time.sleep(_seconds_until_next_scan_slot())  # reuse existing helper's shape
            continue

        _sleep_until(next_slot)   # reuse the existing helper as-is

        # ── This slot's fire time has arrived. If a scan/rescan/backtest is
        #    running right now, WAIT for it to finish rather than skipping —
        #    this is the "pause, then continue" behavior. ──
        waited = 0
        while _fyers_busy_for_extras() and waited < _BREADTH_MAX_WAIT_SECONDS:
            time.sleep(5)
            waited += 5

        if _fyers_busy_for_extras():
            # Still busy after the max wait (an unusually long backtest,
            # say) — give up on THIS slot rather than firing late into the
            # next one's territory. The next loop iteration computes the
            # next fixed slot fresh, so this never double-fires or drifts.
            continue

        if not _fyers_market_data_allowed():
            continue   # market status may have changed while we were waiting

        _refresh_full_breadth()   # the paced 10-call batch from §4.2


# ─────────────────────────────────────────────────────────────────────────────
# Live quotes poller
# ─────────────────────────────────────────────────────────────────────────────

def _refresh_quotes() -> None:
    """Fetch live LTPs for all stocks in _state and update _quotes_cache."""
    global _quotes_cache, _quotes_updated_at

    if _fyers is None or not _fyers_market_data_allowed():
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
        print(
            f"📈  Quotes poller started "
            f"(checks every {interval_seconds}s; Fyers only while source-confirmed open)"
        )
        while True:
            try:
                if _fyers_market_data_allowed():
                    _refresh_quotes()
            except Exception:
                pass
            time.sleep(interval_seconds)

    t = threading.Thread(target=_poll, daemon=True, name="quotes-poller")
    t.start()


# ─────────────────────────────────────────────────────────────────────────────
# End-of-session closing snapshot
# ─────────────────────────────────────────────────────────────────────────────

def _market_is_closed_now() -> bool:
    """
    True when the exchange is definitely not trading: outside 09:15-15:30 IST,
    or at the weekend. (A weekday *holiday* inside the window is not detected
    here — that case keeps using the normal fallbacks, then the last snapshot.)
    """
    now = datetime.datetime.now(_IST)
    return now.weekday() >= 5 or not (_MARKET_OPEN <= now.time() <= _MARKET_CLOSE)


def _close_snapshot_get() -> dict | None:
    with _close_lock:
        return _close_snapshot


def _closing_snapshot_is_for(day: datetime.date) -> bool:
    snap = _close_snapshot_get()
    return bool(snap) and snap.get("trade_date") == day.isoformat()


def _store_close_snapshot(snapshot: dict) -> None:
    """
    Keep `snapshot` in memory and on disk. Never lets an older session, or an
    intraday reading, replace a finalised close for the same day.
    """
    global _close_snapshot
    snapshot = _json_safe(snapshot)
    with _close_lock:
        current = _close_snapshot
        if current:
            if snapshot["trade_date"] < current["trade_date"]:
                return
            if (
                snapshot["trade_date"] == current["trade_date"]
                and current.get("final")
                and not snapshot.get("final")
            ):
                return
        _close_snapshot = snapshot
    try:
        save_close_snapshot(snapshot)
    except Exception as e:
        print(f"   ⚠️  Could not save closing snapshot: {e}")


def _read_index_board(today: datetime.date) -> list | None:
    """
    The three index rows from the live tick cache, or None unless all three
    have ticked *today* — a stale cache from a previous session (or a holiday)
    can never be mistaken for today's reading.

    Deliberately ignores _fyers_market_data_allowed(): the tick cache is still
    in memory just after the close, which is exactly when this runs. It only
    reads memory — no network call of any kind.
    """
    last_tick = _fyers_stream.last_tick_at()
    if last_tick is None:
        return None
    if datetime.datetime.fromtimestamp(last_tick, _IST).date() != today:
        return None
    try:
        board = _fyers_stream.index_board(MARKETS, last_known=True)
    except Exception:
        return None
    return board["markets"] if board else None


def _build_close_snapshot(today: datetime.date) -> dict | None:
    """An intraday reading of everything the live feed holds (memory only)."""
    markets = _read_index_board(today)
    if not markets:
        return None

    try:
        movers = _fyers_stream.movers()
    except Exception:
        movers = None

    constituents: dict[str, list] = {}
    for market_cfg in MARKETS:
        try:
            rows = _fyers_stream.constituents(market_cfg["market_key"])
        except Exception:
            rows = None
        if rows:
            constituents[market_cfg["market_key"]] = rows

    try:
        breadth = _fyers_stream.breadth()
    except Exception:
        breadth = None

    return {
        "trade_date" : today.isoformat(),
        "captured_at": datetime.datetime.now(_IST).isoformat(),
        "final"      : False,
        "markets"    : markets,
        "movers"     : movers,
        "constituents": constituents,
        "breadth"    : breadth,
    }


def _capture_index_close(today: datetime.date) -> bool:
    """
    Refresh ONLY the index values in today's snapshot from the latest ticks and
    mark it final. Everything else in the snapshot is left exactly as the last
    intraday reading had it.
    """
    markets = _read_index_board(today)
    if not markets:
        return False
    current = _close_snapshot_get()
    if not current or current.get("trade_date") != today.isoformat():
        return False
    _store_close_snapshot({
        **current,
        "markets"    : markets,
        "captured_at": datetime.datetime.now(_IST).isoformat(),
        "final"      : True,
    })
    return True


def _promote_last_reading_to_final(today: datetime.date) -> None:
    """The live feed gave us nothing after the close — keep the last intraday
    reading as the close rather than leaving today unfinalised."""
    current = _close_snapshot_get()
    if current and current.get("trade_date") == today.isoformat() and not current.get("final"):
        _store_close_snapshot({**current, "final": True})
        print("   🏁  No post-close ticks — last intraday reading kept as the close.")


def _close_window_holds_stream() -> bool:
    """
    True from 15:30 until the index close has been taken, on a day that traded.
    The Fyers socket is kept open through this brief window so the final index
    ticks are received instead of being cut off the moment the market stops
    being "open". Costs nothing: the socket is already connected.
    """
    now = datetime.datetime.now(_IST)
    if now.weekday() >= 5:
        return False
    if not (_today_at(now, _MARKET_CLOSE) <= now <= _today_at(now, _CLOSE_CAPTURE_DEADLINE)):
        return False
    if _close_capture_done_for == now.date():
        return False
    return _closing_snapshot_is_for(now.date())


def _close_snapshot_tick() -> float:
    """One pass of the snapshot loop. Returns how many seconds to sleep."""
    global _close_index_captured_for, _close_capture_done_for
    now = datetime.datetime.now(_IST)
    today = now.date()
    if now.weekday() >= 5:
        return 300.0
    close_dt = _today_at(now, _MARKET_CLOSE)

    # ── In session: keep the "last reading" fresh ───────────────────────────
    if _is_market_open() and _fyers_market_data_allowed():
        snapshot = _build_close_snapshot(today)
        if snapshot is not None:
            _store_close_snapshot(snapshot)
        # Wake right at the bell rather than up to a minute after it.
        to_bell = (close_dt - now).total_seconds()
        if 0 < to_bell < _CLOSE_SNAPSHOT_INTERVAL_SECONDS:
            return max(0.05, to_bell)
        return float(_CLOSE_SNAPSHOT_INTERVAL_SECONDS)

    # ── After the bell: index close, then leave it alone ────────────────────
    if _close_capture_done_for == today:
        return 60.0
    current = _close_snapshot_get()
    if current and current.get("trade_date") == today.isoformat() and current.get("final"):
        _close_capture_done_for = today          # e.g. restarted after the close
        return 60.0
    if now < close_dt:
        return 60.0
    if not _closing_snapshot_is_for(today):
        # Holiday, or the server only started after the close: nothing to finalise.
        _close_capture_done_for = today
        return 60.0

    # (1) The bell reading: the latest index ticks, as close to 15:30:00 as the
    #     loop can get. Saved at once so users never wait for the re-read below.
    if _close_index_captured_for != today:
        if _capture_index_close(today):
            _close_index_captured_for = today
            print(f"   🏁  Index close saved for {today.isoformat()} at "
                  f"{datetime.datetime.now(_IST).strftime('%H:%M:%S')} IST.")
        elif now >= _today_at(now, _CLOSE_CAPTURE_DEADLINE):
            _promote_last_reading_to_final(today)
            _close_capture_done_for = today
            return 60.0
        else:
            return 5.0   # no usable ticks yet — try again shortly

    # (2) One re-read a few seconds on, to pick up a final tick that lands just
    #     after the bell. Index values only; harmless if it finds nothing new.
    settle_at = close_dt + datetime.timedelta(seconds=_CLOSE_INDEX_SETTLE_SECONDS)
    if now < settle_at:
        return max(0.5, min(5.0, (settle_at - now).total_seconds()))
    _capture_index_close(today)
    _close_capture_done_for = today
    return 60.0


def _close_snapshot_loop() -> None:
    """
    Independent daemon thread (like the Fyers stream poller): it only reads the
    in-memory tick cache and writes the snapshot file. It makes no network call
    and never touches scanner state.
    """
    print(
        f"🏁  Index-close loop started (reading every {_CLOSE_SNAPSHOT_INTERVAL_SECONDS}s "
        f"in session; index close at {_MARKET_CLOSE.strftime('%H:%M')} IST "
        f"+ {_CLOSE_INDEX_SETTLE_SECONDS}s re-read)"
    )
    while True:
        sleep_for = 30.0
        try:
            sleep_for = _close_snapshot_tick()
        except Exception as e:
            print(f"   ⚠️  index-close loop error: {e}")
        time.sleep(sleep_for)


def _closing_board_payload() -> dict | None:
    snap = _close_snapshot_get()
    if not snap or not snap.get("markets"):
        return None
    return {
        "markets"      : snap["markets"],
        "source"       : "market_close",
        "updated_at"   : snap["captured_at"],
        "market_closed": _market_is_closed_now(),
        "trade_date"   : snap["trade_date"],
    }


def _closing_movers_payload() -> dict | None:
    snap = _close_snapshot_get()
    movers = (snap or {}).get("movers") or {}
    if not (movers.get("gainers") or movers.get("losers") or movers.get("most_active")):
        return None
    return {
        "gainers"      : movers.get("gainers", []),
        "losers"       : movers.get("losers", []),
        "most_active"  : movers.get("most_active", []),
        "source"       : "market_close",
        "updated_at"   : snap["captured_at"],
        "market_closed": _market_is_closed_now(),
    }


def _closing_constituents_payload(market_cfg: dict) -> dict | None:
    snap = _close_snapshot_get()
    rows = ((snap or {}).get("constituents") or {}).get(market_cfg["market_key"])
    if not rows:
        return None
    return {
        "market_key"   : market_cfg["market_key"],
        "market_name"  : market_cfg["display_name"],
        "count"        : len(rows),
        "stocks"       : rows,
        "source"       : "market_close",
        "updated_at"   : snap["captured_at"],
        "market_closed": _market_is_closed_now(),
    }


def _closing_breadth() -> dict | None:
    snap = _close_snapshot_get()
    return (snap or {}).get("breadth") or None


def _close_snapshot_summary() -> dict | None:
    snap = _close_snapshot_get()
    if not snap:
        return None
    return {
        "trade_date" : snap.get("trade_date"),
        "captured_at": snap.get("captured_at"),
        "final"      : bool(snap.get("final")),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Market snapshot — Nifty 50 + Sensex + Bank Nifty
# ─────────────────────────────────────────────────────────────────────────────

def _get_market_snapshot() -> dict:
    """
    Live Fyers WebSocket ticks are read fresh on every call (see
    _get_constituents() for why — in-memory reads are free, so caching
    them only adds needless staleness). The cache below applies only to
    the NSE/Fyers-REST/Yahoo fallback path, which does cost a real network
    call and is reached only before the WebSocket has produced a tick for
    all three indices (e.g. just after startup/reconnect).
    """
    now = time.time()

    # Market closed (evenings, overnight, pre-open, weekends): serve the saved
    # closing values. They don't change until the next session, so there is
    # nothing to fetch — and nothing that can fail and leave the board empty.
    if _market_is_closed_now():
        closed = _closing_board_payload()
        if closed is not None:
            return closed

    # Primary source: the live Fyers WebSocket — zero extra API calls, just
    # reads whatever the socket has already received, fresh every time.
    if _fyers_market_data_allowed():
        try:
            live = _fyers_stream.index_board(MARKETS)
        except Exception:
            live = None
        if live is not None:
            with _market_lock:
                _market_cache["data"]       = live  # kept only as an emergency last-resort value
                _market_cache["expires_at"] = now
            return live

    with _market_lock:
        cached = _market_cache["data"]
        if cached is not None and _market_cache["expires_at"] > now:
            return cached

    # Fallback waterfall — only reached while the WebSocket hasn't produced
    # a full tick yet (e.g. just after startup/reconnect). Fyers REST is
    # tried first per-request when allowed, since Fyers is now the primary
    # source end-to-end; NSE and Yahoo remain as safety nets so the board
    # never goes blank, and outside market hours NSE/Yahoo are used alone
    # (Fyers is not called at all when data use isn't allowed).
    if _fyers_market_data_allowed():
        fetchers = (_fetch_market_from_fyers, _fetch_market_from_nse, _fetch_market_from_yahoo)
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

    # Every live source failed: the last saved reading beats a stale in-memory
    # value, and far beats the made-up numbers below.
    closed = _closing_board_payload()
    if closed is not None:
        return closed

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
    """
    Constituent list for the market.

    Live Fyers WebSocket ticks are read fresh on every single call — no
    caching layer sits in front of them, because reading them costs
    nothing (in-memory, no external request) and caching them would
    reintroduce exactly the staleness the WebSocket exists to remove.

    The 60s cache below applies ONLY to the NSE/REST-Fyers fallback path,
    which is a real external network call and does need throttling — it
    is reached only while the WebSocket hasn't produced ticks for this
    market yet (e.g. just after startup/reconnect).
    """
    market_key = market_cfg["market_key"]
    now        = time.time()

    # Market closed: the saved closing rows (the NSE/REST fallback below has
    # no prices at all once Fyers is switched off — Sensex especially).
    if _market_is_closed_now():
        closed = _closing_constituents_payload(market_cfg)
        if closed is not None:
            return closed

    # Primary source: live Fyers WebSocket ticks — always read fresh,
    # never cached. This is what makes the app show truly live numbers.
    if _fyers_market_data_allowed():
        try:
            live_rows = _fyers_stream.constituents(market_key)
        except Exception:
            live_rows = None
        if live_rows:
            return {
                "market_key" : market_key,
                "market_name": market_cfg["display_name"],
                "count"      : len(live_rows),
                "stocks"     : live_rows,
                "source"     : "fyers_ws",
                "updated_at" : datetime.datetime.now().isoformat(),
            }

    # Fallback path (NSE / REST-Fyers) — real external calls, so this one
    # is genuinely cache-worthy. TTL = 60s.
    with _constituents_lock:
        cached = _constituents_cache.get(market_key)
        if cached is not None and cached["expires_at"] > now:
            return cached["data"]

    try:
        data = _build_constituents_payload(market_cfg)
    except Exception as e:
        # Surface a partial response rather than a 500 error.
        # Use plain hardcoded symbols so the frontend always gets a list.
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

    if not any(s.get("last_price") is not None for s in data.get("stocks", [])):
        # Names but no prices from any source: last saved reading instead.
        closed = _closing_constituents_payload(market_cfg)
        if closed is not None:
            return closed

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
    if not stocks or _fyers is None or not _fyers_market_data_allowed():
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
    then batched Fyers quotes for live LTP / high / low only while market-data
    use is allowed.
    """
    market_key = market_cfg["market_key"]
    raw_stocks : list = []
    nse_table_ok = False
    stocks        = []
    used_fallback = False

    # §4.1 fix: Sensex has no NSE endpoint (nse_index_param is None) — skip
    # the NSE call entirely and start from the maintained SENSEX30 list.
    if market_cfg["nse_index_param"] is None:
        session = _get_nse_session()
        stocks = _constituent_dicts_from_plain_symbols(SENSEX30)
        used_fallback = True
        min_rows = 1
    else:
        session = _get_nse_session()
        url    = "https://www.nseindia.com/api/equity-stockIndices"
        params = {"index": market_cfg["nse_index_param"]}

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

        stocks   = _parse_equity_stockindices_rows(raw_stocks)
        min_rows = 6 if market_key == "bank_nifty" else 15

    if not used_fallback and len(stocks) < min_rows:
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
    elif market_cfg["nse_index_param"] is None:
        base = "sensex30_static"   # maintained list, not an NSE fallback — §4.1
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
    Background thread that refreshes the market snapshot cache.
    Cache is always warm — Flutter clients get instant responses.
    Fyers is only allowed after Yahoo/NSE confirm the market is open. Outside
    market hours the poll slows to PASSIVE_CHECK_INTERVAL and uses free sources.
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
            if _fyers_market_data_allowed():
                sleep = interval_seconds
            elif _is_market_open():
                sleep = 60
            else:
                sleep = PASSIVE_CHECK_INTERVAL
            time.sleep(sleep)

    t = threading.Thread(target=_poll, daemon=True, name="market-poller")
    t.start()
    print(
        f"📡  Market poller started "
        f"({interval_seconds}s while source-confirmed open; "
        f"{PASSIVE_CHECK_INTERVAL // 60}m outside market hours; NSE/Yahoo free-source fallback)"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    global _fyers, _symbols, _breadth_full_cache, _close_snapshot

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
    print("   Auth will run automatically after Yahoo/NSE confirm a trading day, before the first scan slot.")

    # Restore cached Insights stats and full-breadth snapshot from disk so
    # neither screen is blank between process restart and the next
    # scan / breadth slot.
    loaded_stats = load_universe_stats()
    _state["universe_stats"]       = loaded_stats["stats"]
    _state["universe_stats_as_of"] = loaded_stats["as_of"]
    _breadth_full_cache = load_full_breadth()
    _close_snapshot     = load_close_snapshot()

    summary = get_log_summary()
    print(f"\n📋  Signal log : {summary['total_signals']} signals across {summary['days_logged']} day(s)")
    print(f"    Stocks     : {len(_symbols)}")

    slot_labels = [f"{h:02d}:{ACTIVE_CHECK_MINUTE:02d}" for h in ACTIVE_CHECK_HOURS]
    print(
        f"\n🔄  Starting fixed-slot scan loop "
        f"({slot_labels[0]}–{slot_labels[-1]} IST via {', '.join(slot_labels)}) …"
    )
    threading.Thread(target=_scan_loop, daemon=True, name="scan-loop").start()

    breadth_slot_labels = [f"{h:02d}:{BREADTH_CHECK_MINUTE:02d}" for h in BREADTH_CHECK_HOURS]
    print(
        f"📊  Starting full Nifty-500 breadth poller "
        f"({breadth_slot_labels[0]}–{breadth_slot_labels[-1]} IST via {', '.join(breadth_slot_labels)}) …"
    )
    threading.Thread(target=_breadth_loop, daemon=True, name="breadth-loop").start()

    if push_alerts.is_configured():
        print("🔔  Push notifications enabled (FCM)")
    else:
        print("🔕  Push notifications off — set FIREBASE_SERVICE_ACCOUNT_JSON to enable")
    threading.Thread(target=_push_pending_loop, daemon=True, name="push-pending-loop").start()

    _start_market_poller(interval_seconds=60)
    _start_quotes_poller(interval_seconds=15)
    _start_fyers_stream_poller(check_interval_seconds=30)
    threading.Thread(target=_close_snapshot_loop, daemon=True, name="close-snapshot-loop").start()

    url = f"http://localhost:{args.port}"
    print(f"🌐  Opening {url} …")
    print(f"    Press Ctrl+C to stop.\n")
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    # threaded=True: with the live market-data endpoints now doing a real
    # (if occasional) synchronous network call on their REST-fallback path
    # (NSE/Fyers/Yahoo — only reached before the WebSocket has produced a
    # tick), a single-threaded dev server would serialize *every* request
    # behind that one slow call — not just other market-data requests, but
    # scanner/website requests too. Flutter now also fires several market
    # requests concurrently per screen per refresh tick (Home, Index Detail,
    # Equity Detail, Insights each polling independently every 4s), so
    # without this the requests in one tick queue up behind each other even
    # when every one of them is a fast in-memory read. This is exactly the
    # kind of load the existing per-cache locks (_market_lock,
    # _constituents_lock, _movers_lock, and fyers_stream's own lock) were
    # already written to support concurrently — enabling threading here was
    # the missing piece, not a new design.
    app.run(host="0.0.0.0", port=args.port, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()

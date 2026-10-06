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

from auth.fyers_auth import reconnect_fyers, get_cached_token, client_from_cached_token
from config.settings import (
    ACTIVE_CHECK_HOURS, ACTIVE_CHECK_MINUTE, PASSIVE_CHECK_INTERVAL,
    FYERS_APP_ID_FULL, BREADTH_CHECK_HOURS, BREADTH_CHECK_MINUTE,
)
from data.symbols import (
    fetch_nifty500, fetch_nifty500_with_meta, get_universe_meta,
    plain_constituents_for_market, SENSEX30,
)
from data.fyers_stream import stream as _fyers_stream
from data import history_store
from config import settings as _cfg
from scanner.watchlist import (
    load_watchlist, clean_watchlist, save_watchlist,
    load_alert_log, clean_alert_log, save_alert_log,
)
from scanner.engine import run_scan
from scanner import sweep as sweep_mod
from scanner import entry_detect
from data import entry_hits
from scanner.sweep import RUNTIME as _sweep
from scanner.historical import (
    run_historical_scan, topup_historical_scan, backtest_universe_gap, backtest_retry_symbols,
    _tag as _universe_tag,
)
from utils.logger import get_log_summary
from utils.scan_progress import LIVE_PROGRESS, BACKTEST_PROGRESS
from data.quotes import fetch_ltp_bulk, fetch_constituents_quotes_bulk, fetch_full_market_breadth
from alerts import manual_push
from data import push_audit
from data.app_signals import (
    load_signals, add_signal, update_signal, delete_signal, set_published,
    set_notification_state, set_entry_reached, ENTRY_REACHED_ADMIN_FIELDS,
)
from data.app_devices import (
    register_device, unregister_device, device_count,
)
from data.app_exits import load_exits, add_exit, delete_exit
from alerts import push as push_alerts
from data.app_learn import load_articles, add_article, update_article, delete_article, get_article
from data.app_insights import load_insights, add_insight, update_insight, delete_insight
from data.app_weekly_report import load_reports, add_report, update_report, delete_report
from data.app_sentiment import compute_sentiment
from data.universe_stats import load_universe_stats, save_universe_stats
from data.backtest_store import (
    load_backtest_state, save_backtest_state, now_iso as _bt_now_iso, VALID_FILTERS as _BT_FILTERS,
)
from data.scan_store import (
    save_result as _save_scan_result, load_result as _load_scan_result,
    list_saved as _list_saved_scans,
)
from utils.scan_control import ScanCancelled, FyersAuthError
from data.breadth import load_full_breadth, save_full_breadth
from data.market_close import load_close_snapshot, save_close_snapshot
from config.settings import APP_ASSET_DIR
from config.settings import APP_REQUIRE_VERIFIED_EMAIL
from auth import app_auth
from flask import g
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
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
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
    # Scan control / completeness (no Fyers calls — pure bookkeeping)
    "notice"          : None,   # non-error message, e.g. "Scan stopped"
    "scan_waiting"    : False,  # scheduled scan is waiting for a running backtest
    "scan_report"     : None,   # data-completeness summary of the last live scan
}
_fyers      = None
_symbols    = None
# Once-per-day Fyers login, shared by the scan loop and the history-store job so
# whichever needs Fyers first logs in and the other reuses that session.
_auth_lock = threading.RLock()
_last_auth_date: datetime.date | None = None
_symbols_meta: dict = {}   # provenance of _symbols (source / count / complete) from data.symbols
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

# ── One Fyers-heavy job at a time ─────────────────────────────────────────────
# A scheduled/manual scan and a backtest both hammer Fyers history. Running them
# together doubled the request rate and caused rate-limit failures in BOTH.
# This lock makes them take turns. Scheduled scans wait for a running backtest;
# user-triggered runs are refused with a clear message instead of queueing.
_heavy_lock = threading.Lock()
_heavy_guard = threading.Lock()
_heavy_kind: str | None = None
_live_cancel = threading.Event()        # Stop for the scheduled / manual scan
_backtest_cancel = threading.Event()    # Stop for the running backtest
_history_cancel = threading.Event()     # Pause for the daily history-store download
# Held by a published one-minute sweep while it evaluates, and by the hourly scan
# for its whole run: the two never write the watchlist / alert log / results together.
_sweep_run_lock = threading.Lock()
_sweep_cancel = threading.Event()
_backtest_stop_reason: str | None = None  # why the backtest was stopped (shown to the user)


def _heavy_try_acquire(kind: str) -> bool:
    global _heavy_kind
    if _heavy_lock.acquire(blocking=False):
        with _heavy_guard:
            _heavy_kind = kind
        return True
    return False


def _heavy_acquire_wait(kind: str, cancel: threading.Event) -> bool:
    """Block until the lock is free. Raises ScanCancelled if Stop is pressed."""
    global _heavy_kind
    while True:
        if cancel.is_set():
            raise ScanCancelled()
        if _heavy_lock.acquire(timeout=1.0):
            with _heavy_guard:
                _heavy_kind = kind
            return True


def _heavy_release() -> None:
    global _heavy_kind
    with _heavy_guard:
        _heavy_kind = None
    try:
        _heavy_lock.release()
    except RuntimeError:
        pass


# ── Symbol universe refresh (NSE list — not a Fyers call) ────────────────────
_MIN_FULL_UNIVERSE = 400
_symbols_lock = threading.Lock()
_symbols_ok_on: datetime.date | None = None
_symbols_last_try = 0.0


def _symbols_complete() -> bool:
    """True when the current universe is a full Nifty 500 list (not a truncated fallback)."""
    if not _symbols or len(_symbols) < _MIN_FULL_UNIVERSE:
        return False
    if _symbols_meta and _symbols_meta.get("complete") is False:
        return False
    return True


def _set_universe(symbols: list[str], meta: dict) -> None:
    global _symbols, _symbols_meta
    _symbols = symbols
    _symbols_meta = dict(meta or {})


def _refresh_symbols(force_if_incomplete: bool = False) -> None:
    """
    Re-read the Nifty 500 list once per day (and retry every 10 min while the
    list looks truncated, e.g. after the Nifty-50 fallback).

    force_if_incomplete — used by scans and backtests: when the current universe
    is incomplete the 10-minute throttle is skipped, so a scan never runs on a
    truncated list if NSE has become reachable again in the meantime.

    Replacement rule (a worse list never replaces a good one):
      * a COMPLETE fresh list always replaces the current one (index changes);
      * an incomplete fresh list replaces the current one only when the current
        one is incomplete too and the fresh one is larger;
      * an incomplete fresh list NEVER replaces a complete one.
    """
    global _symbols_ok_on, _symbols_last_try
    today = datetime.datetime.now(_IST).date()
    with _symbols_lock:
        full = _symbols_complete()
        if full and _symbols_ok_on == today:
            return
        if _symbols and not (force_if_incomplete and not full) and time.time() - _symbols_last_try < 600:
            return
        _symbols_last_try = time.time()
        try:
            fresh, fresh_meta = fetch_nifty500_with_meta()
        except Exception as exc:
            print(f"⚠️   Symbol list refresh failed ({exc}); keeping the current list.")
            return
        fresh_complete = bool(fresh_meta.get("complete")) and len(fresh) >= _MIN_FULL_UNIVERSE
        if not fresh:
            accept = False
        elif not _symbols:
            accept = True
        elif fresh_complete:
            accept = True
        elif not full:
            accept = len(fresh) >= len(_symbols)
        else:
            accept = False
        if accept:
            if _symbols and set(fresh) != set(_symbols):
                added = sorted(set(fresh) - set(_symbols))
                dropped = sorted(set(_symbols) - set(fresh))
                print(f"🔄  Symbol universe updated: {len(_symbols)} → {len(fresh)} stocks "
                      f"(added {added[:15]}, removed {dropped[:15]})")
            _set_universe(fresh, fresh_meta)
            if fresh_complete:
                _symbols_ok_on = today
        else:
            print(f"⚠️   Symbol refresh returned {len(fresh or [])} stocks "
                  f"(complete={fresh_meta.get('complete')}); keeping {len(_symbols or [])}.")


def _saved_universe_gap(saved: dict | None) -> tuple[list[str], list[str]] | None:
    """
    (missing, removed) between a saved backtest and the current Nifty 500, or
    None when the saved result already covers it. Never compares against a
    truncated universe, so a failed NSE fetch can't prune a good saved result.
    """
    if not saved or not _symbols_complete():
        return None
    missing, removed = backtest_universe_gap(saved.get("payload") or {}, _symbols)
    return (missing, removed) if (missing or removed) else None


def _saved_retry_symbols(saved: dict | None) -> list[str]:
    """
    Stocks of the current universe whose saved backtest row says Fyers failed to
    answer (transient). They are re-requested alone — never a full rescan.
    """
    if not saved or not _symbols:
        return []
    return backtest_retry_symbols(saved.get("payload") or {}, _symbols)


# Shared, persisted backtest page state (selected date, filter, running job,
# last completed result). Guarded by _backtest_lock; always write to disk via
# _bt_commit_locked() so every change bumps `revision`.
_backtest_state: dict = load_backtest_state()


def _bt_commit_locked() -> None:
    """Bump revision + timestamp and persist. Caller must hold _backtest_lock."""
    _backtest_state["revision"] = int(_backtest_state.get("revision") or 0) + 1
    _backtest_state["updated_at"] = _bt_now_iso()
    try:
        save_backtest_state(_backtest_state)
    except Exception as exc:  # persistence is best-effort; never break a run
        print(f"🧪  WARNING: could not persist backtest state: {exc}")


def _bt_public_state_locked() -> dict:
    """Shape served to clients. Caller must hold _backtest_lock."""
    st = _backtest_state
    running = st.get("running_job")
    return {
        "revision": st.get("revision", 0),
        "updated_at": st.get("updated_at"),
        "selected_date": st.get("selected_date"),
        "filter": st.get("filter", "all"),
        "running": bool(running),
        "running_job": running,
        "result": st.get("result"),
        "error": st.get("error"),
        "notice": st.get("notice"),
    }


# ── Session authentication ───────────────────────────────────────────────────
_AUTH_PUBLIC_API = {
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/session",
}

# Endpoints that need a valid APP token (Firebase Bearer) and nothing else —
# even a website-admin cookie does not satisfy them, so a record is always tied
# to a real app account (uid).
_APP_ACCOUNT_RULES = {
    "/api/devices/register",
    "/api/devices/unregister",
}

# Website login throttle (in-memory, per client IP). Website only.
_LOGIN_WINDOW_SECONDS = 15 * 60
_LOGIN_MAX_FAILURES = 10
_login_failures: dict[str, list[float]] = {}
_login_lock = threading.Lock()


def _client_ip() -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[0].strip() if fwd else "") or (request.remote_addr or "unknown")


def _login_throttled(ip: str) -> bool:
    now = time.time()
    with _login_lock:
        recent = [t for t in _login_failures.get(ip, []) if now - t < _LOGIN_WINDOW_SECONDS]
        if recent:
            _login_failures[ip] = recent
        else:
            _login_failures.pop(ip, None)
        return len(recent) >= _LOGIN_MAX_FAILURES


def _login_record_failure(ip: str) -> None:
    with _login_lock:
        if len(_login_failures) > 5000:
            _login_failures.clear()
        _login_failures.setdefault(ip, []).append(time.time())


def _login_clear(ip: str) -> None:
    with _login_lock:
        _login_failures.pop(ip, None)


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
    Gate for admin-only endpoints (writes, rescans, backtests, ?all=1 views).
    Satisfied ONLY by the website's Flask cookie session. Mobile-app (Firebase
    token) users never count as admin: this function must not read g.app_user
    or the Authorization header. If SCANNER_ADMIN_USERS is unset, any website
    login is an admin — set it in Railway to restrict this to named users.
    """
    if not _is_authenticated():
        return False
    admins = _configured_admin_users()
    if not admins:
        return True
    return session.get("username") in admins


def _is_static_asset(path: str) -> bool:
    return bool(path and os.path.isfile(os.path.join(STATIC_DIR, path)))


# Endpoints a signed-in APP user (Firebase Bearer token) may call — read-only
# GETs the Flutter app uses. Matched against the Flask route rule. Anything not
# listed here is admin-only (cookie session) by default.
_APP_READABLE_RULES = {
    "/api/market",
    "/api/market/gainers",
    "/api/market/losers",
    "/api/market/most-active",
    "/api/market/<string:market_key>/constituents",
    "/api/sentiment",
    "/api/signals",
    "/api/learn",
    "/api/learn/<string:article_id>",
    "/api/insights",
    "/api/insights/volatility",
    "/api/insights/momentum",
    "/api/insights/volume-surge",
    "/api/breadth/full",
    "/api/weekly-report",
    "/api/compliance",
    "/api/app/me",
}


def _auth_error(status: int, code: str, message: str):
    return jsonify({"authenticated": False, "error": message, "code": code}), status


@app.before_request
def _require_authentication():
    g.app_user = None
    if request.method == "OPTIONS":
        return None
    if request.path in _AUTH_PUBLIC_API:
        return None
    if request.path.startswith("/assets/") or _is_static_asset(request.path.lstrip("/")):
        return None
    if not request.path.startswith("/api/"):
        return None

    rule = request.url_rule.rule if request.url_rule else ""

    # App-account endpoints: a valid app token is mandatory.
    if rule in _APP_ACCOUNT_RULES:
        token = app_auth.extract_bearer(request.headers.get("Authorization"))
        if not token:
            return _auth_error(401, "app_auth_required", "Authentication required")
        user, err = app_auth.verify_bearer(token)
        if err:
            return _auth_error(*err)
        if APP_REQUIRE_VERIFIED_EMAIL and not user["email_verified"]:
            return _auth_error(403, "email_not_verified", "Verify your email to continue")
        g.app_user = user
        return None

    # Website admin: cookie session (unchanged).
    if _is_authenticated():
        return None

    # App user: Firebase ID token.
    token = app_auth.extract_bearer(request.headers.get("Authorization"))
    if token:
        user, err = app_auth.verify_bearer(token)
        if err:
            return _auth_error(*err)
        if request.method not in ("GET", "HEAD") or rule not in _APP_READABLE_RULES:
            return _auth_error(403, "forbidden", "Not allowed")
        if APP_REQUIRE_VERIFIED_EMAIL and not user["email_verified"]:
            return _auth_error(403, "email_not_verified", "Verify your email to continue")
        g.app_user = user
        return None

    # Anonymous: always refused (no bypass).
    return _auth_error(401, "app_auth_required", "Authentication required")


@app.route("/api/app/me")
def api_app_me():
    """Identity of the signed-in app user, from the verified token. Stores nothing."""
    user = getattr(g, "app_user", None)
    if not user:
        return _auth_error(401, "app_auth_required", "Authentication required")
    return jsonify({
        "uid": user["uid"],
        "email": user["email"],
        "email_verified": user["email_verified"],
        "name": user["name"],
    })


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

    ip = _client_ip()
    if _login_throttled(ip):
        return jsonify({
            "authenticated": False,
            "error": "Too many failed attempts. Try again in a few minutes.",
        }), 429

    payload = request.get_json(silent=True) or {}
    username = str(payload.get("username", ""))
    password = str(payload.get("password", ""))
    users = _configured_users()
    expected_password = users.get(username)

    if expected_password and hmac.compare_digest(password, expected_password):
        session.clear()
        session["authenticated"] = True
        session["username"] = username
        _login_clear(ip)
        return jsonify({"authenticated": True})

    _login_record_failure(ip)
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
        "notice"          : _state.get("notice"),
        "scan_waiting"    : _state.get("scan_waiting", False),
        "scan_report"     : {
            k: v for k, v in (_state.get("scan_report") or {}).items() if k != "ledger"
        } or None,
    })


@app.route("/api/scan/skipped")
def api_scan_skipped():
    """
    Per-stock ledger of every stock that was NOT evaluated, with the reason
    (no Fyers data / stale / Fyers failure with its error code / short history /
    internal error). Memory read only — zero Fyers calls.
    """
    with _backtest_lock:
        bt = (_backtest_state.get("result") or {}).get("debug") or {}
    return jsonify(_json_safe({
        "live": _state.get("scan_report"),
        "backtest": {
            "date": bt.get("requested_date"),
            "ledger": bt.get("skipped"),
            "failed_symbols": bt.get("failed_symbols"),
        },
    }))


@app.route("/api/scan/progress")
def api_scan_progress():
    """
    Live progress of the scheduled/manual scan ("live") and of the Backtest
    scan ("backtest"), read from the in-memory trackers the scan loops feed
    (utils/scan_progress.py). Pure memory read — never touches Fyers — so the
    website can poll it every second while a scan runs.
    """
    resp = jsonify({
        "live"    : LIVE_PROGRESS.snapshot(),
        "backtest": BACKTEST_PROGRESS.snapshot(),
    })
    resp.headers["Cache-Control"] = "no-store"
    return resp


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
    if not _heavy_try_acquire("live"):
        return jsonify({
            "status": "busy",
            "message": f"A {_heavy_kind or 'backtest'} scan is running and uses the same Fyers "
                       "connection. Wait for it to finish, or stop it first.",
        }), 409
    _live_cancel.clear()
    _state["scanning"] = True
    threading.Thread(target=_do_scan, kwargs={"lock_held": True}, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/api/scan/stop", methods=["POST"])
def api_scan_stop():
    """Safely stop the running (or waiting) scheduled/manual scan."""
    if not (_state["scanning"] or _state.get("scan_waiting")):
        return jsonify({"status": "not_running"})
    _live_cancel.set()
    LIVE_PROGRESS.request_stop()
    return jsonify({"status": "stopping"})


@app.route("/api/backtest/stop", methods=["POST"])
def api_backtest_stop():
    """Safely stop the running backtest. The previous saved result is kept."""
    with _backtest_lock:
        running = _backtest_state.get("running_job")
    if not running:
        return jsonify({"status": "not_running"})
    _backtest_cancel.set()
    BACKTEST_PROGRESS.request_stop()
    return jsonify({"status": "stopping"})


@app.route("/api/backtest/saved")
def api_backtest_saved():
    """Dates that already have a saved backtest result (metadata only)."""
    return jsonify({"dates": _list_saved_scans("backtest")})


@app.route("/api/backtest/universe")
def api_backtest_universe():
    """
    Backtest universe audit — NSE list / local files only, never Fyers.

    GET /api/backtest/universe                 → source, count, completeness
    GET /api/backtest/universe?symbol=THELEELA → is it in the universe, and what
                                                 the last shown backtest says about it
    """
    try:
        _refresh_symbols()
    except Exception as exc:
        print(f"⚠️   Universe refresh failed: {exc}")
    out: dict = {
        "count": len(_symbols or []),
        "complete": _symbols_complete(),
        "meta": dict(_symbols_meta),
    }
    q = str(request.args.get("symbol", "")).strip().upper()
    if q:
        tag = _universe_tag(q if ":" in q else f"NSE:{q}-EQ")
        in_uni = any(_universe_tag(s) == tag for s in (_symbols or []))
        with _backtest_lock:
            res = _backtest_state.get("result") or {}
            row = next((r for r in (res.get("backtest_results") or [])
                        if isinstance(r, dict) and r.get("symbol") == tag), None)
            sel = _backtest_state.get("selected_date")
        if in_uni:
            verdict = "In the current Nifty 500 list."
        elif out["complete"]:
            verdict = (f"Not in the Nifty 500 list fetched from NSE ({_symbols_meta.get('source')}); "
                       "it is correctly excluded from the backtest universe.")
        else:
            verdict = "Universe list is incomplete right now — cannot confirm membership."
        out["symbol_check"] = {
            "symbol": tag,
            "in_universe": in_uni,
            "verdict": verdict,
            "shown_backtest_date": sel,
            "row_in_shown_backtest": (
                {"status": row.get("status"), "category": row.get("category"),
                 "reason": row.get("reason")} if row else None
            ),
        }
    return jsonify(_json_safe(out))


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

    force = bool(payload.get("force"))
    day_key = target_date.isoformat()

    # ── Reuse a saved result instead of scanning again (no Fyers calls) ───────
    topup_base = None
    if not force:
        saved = _load_scan_result("backtest", day_key)
        gap = None
        retry: list[str] = []
        if saved and saved.get("session_final"):
            try:
                _refresh_symbols()      # NSE list only — no Fyers call
            except Exception as exc:
                print(f"⚠️   Universe refresh before backtest failed: {exc}")
            gap = _saved_universe_gap(saved)
            retry = _saved_retry_symbols(saved)
            if gap or retry:
                topup_base = saved["payload"]
                print(f"🧪  Backtest {day_key}: saved result is missing {len(gap[0]) if gap else 0} stock(s) "
                      f"of the current Nifty 500 ({(gap[0] if gap else [])[:10]}), holds "
                      f"{len(gap[1]) if gap else 0} that left it and has {len(retry)} stock(s) "
                      f"Fyers failed to answer ({retry[:10]}) — re-requesting only those.")
        if saved and saved.get("session_final") and not (gap or retry):
            with _backtest_lock:
                if _backtest_state.get("running_job"):
                    return jsonify({
                        "error": "A backtest is already running. Wait for it to finish first.",
                        "running_job": _backtest_state.get("running_job"),
                        "state": _bt_public_state_locked(),
                    }), 409
                _backtest_state["result"] = saved["payload"]
                _backtest_state["selected_date"] = day_key
                _backtest_state["filter"] = "all"
                _backtest_state["error"] = None
                _backtest_state["notice"] = None
                _bt_commit_locked()
            print(f"🧪  Backtest {day_key}: loaded saved result (saved {saved.get('saved_at')}) — no scan run")
            return jsonify({
                "status": "cached", "cached": True, "date": day_key,
                "saved_at": saved.get("saved_at"), "partial": bool(saved.get("partial")),
            }), 200

    job_id = uuid.uuid4().hex
    with _backtest_lock:
        # One backtest at a time for the whole site. A second request while
        # one is running is refused (no duplicate Fyers traffic); the caller
        # just joins the running job through /api/backtest/state.
        existing = _backtest_state.get("running_job")
        if existing and any(j.get("status") == "running" for j in _backtest_jobs.values()):
            return jsonify({
                "error": f"A backtest for {existing.get('date')} is already running. "
                         "Its results will appear here when it finishes.",
                "running_job": existing,
                "state": _bt_public_state_locked(),
            }), 409

        if not _heavy_try_acquire("backtest"):
            what = "A scheduled scan" if _heavy_kind == "live" else "Another scan"
            return jsonify({
                "error": f"{what} is running and uses the same Fyers connection. "
                         "Try again when it finishes, or stop it first.",
            }), 409
        _backtest_cancel.clear()
        _backtest_stop_reason = None

        created_at = datetime.datetime.now(_IST).isoformat()
        _backtest_jobs[job_id] = {
            "job_id": job_id,
            "status": "running",
            "date": target_date.isoformat(),
            "created_at": created_at,
            "result": None,
            "error": None,
        }
        _backtest_state["running_job"] = {
            "job_id": job_id,
            "date": target_date.isoformat(),
            "created_at": created_at,
        }
        _backtest_state["selected_date"] = target_date.isoformat()
        _backtest_state["filter"] = "all"
        _backtest_state["error"] = None
        _backtest_state["notice"] = None
        _bt_commit_locked()

    threading.Thread(
        target=_run_backtest_job,
        args=(job_id, target_date, topup_base),
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
        if job["status"] == "cancelled":
            return jsonify({
                "job_id": job_id, "status": "cancelled", "scanning": False,
                "scan_time": None, "total_scanned": 0, "total_attempted": 0,
                "signals": [], "watchlist_items": [], "backtest_results": [],
                "error": None,
            })
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


@app.route("/api/backtest/state", methods=["GET"])
def api_backtest_state_get():
    """
    Shared Backtest page state: selected date, filter, running job and the
    last completed result. Served from memory/disk — no Fyers calls.

    `?since=<revision>` lets clients poll cheaply: when nothing changed the
    response is just {"unchanged": true, "revision": N}.
    """
    since = request.args.get("since", type=int)
    have_result_job = request.args.get("result_job", "").strip()
    with _backtest_lock:
        revision = int(_backtest_state.get("revision") or 0)
        if since is not None and since == revision:
            return jsonify({"unchanged": True, "revision": revision})
        state = _bt_public_state_locked()
        result = state.get("result")
        # Client already holds this exact result (e.g. only the filter or date
        # changed) — don't resend the large results payload.
        if have_result_job and isinstance(result, dict) and result.get("job_id") == have_result_job:
            state["result"] = None
            state["result_unchanged"] = True
        return jsonify(_json_safe(state))


@app.route("/api/backtest/state", methods=["POST", "PUT"])
def api_backtest_state_set():
    """
    Explicitly change the shared page state (date picker / result filter).
    Does not run a backtest and never touches Fyers.
    """
    payload = request.get_json(silent=True) or {}
    changed = False
    with _backtest_lock:
        if "date" in payload:
            try:
                new_date = datetime.date.fromisoformat(str(payload["date"]).strip()).isoformat()
            except ValueError:
                return jsonify({"error": "Enter a valid date in YYYY-MM-DD format."}), 400
            if new_date != _backtest_state.get("selected_date"):
                _backtest_state["selected_date"] = new_date
                changed = True
                # Returning to an already-scanned date shows its saved result.
                if not _backtest_state.get("running_job"):
                    saved = _load_scan_result("backtest", new_date)
                    if saved:
                        _backtest_state["result"] = saved["payload"]
                        _backtest_state["error"] = None
                        _backtest_state["notice"] = None
                        _gap = _saved_universe_gap(saved)
                        _retry = _saved_retry_symbols(saved)
                        if _gap or _retry:
                            _backtest_state["notice"] = (
                                f"This saved backtest is not complete for the current Nifty 500 "
                                f"({len(_gap[0]) if _gap else 0} stock(s) missing, "
                                f"{len(_gap[1]) if _gap else 0} no longer in the index, "
                                f"{len(_retry)} that Fyers failed to answer last time). "
                                "Press Backtest for this date to request only those again."
                            )
        if "filter" in payload:
            new_filter = str(payload["filter"]).strip()
            if new_filter not in _BT_FILTERS:
                return jsonify({"error": "Invalid filter."}), 400
            if new_filter != _backtest_state.get("filter"):
                _backtest_state["filter"] = new_filter
                changed = True
        if changed:
            _bt_commit_locked()
        return jsonify({
            "revision": int(_backtest_state.get("revision") or 0),
            "selected_date": _backtest_state.get("selected_date"),
            "filter": _backtest_state.get("filter", "all"),
        })


def _run_backtest_job(job_id: str, target_date: datetime.date, topup_base: dict | None = None) -> None:
    global _fyers, _symbols, _backtest_stop_reason

    progress_error: str | None = None
    cancelled = False
    progress_run = BACKTEST_PROGRESS.start(
        total=len(_symbols or []), target_date=target_date.isoformat()
    )
    try:
        print(f"🧪  Backtest API request: date={target_date.isoformat()}")
        _refresh_symbols(force_if_incomplete=True)
        if _symbols is None:
            _set_universe(*fetch_nifty500_with_meta())
        if _fyers is None:
            _fyers = reconnect_fyers()
        BACKTEST_PROGRESS.set_total(len(_symbols))
        _uni_meta = dict(_symbols_meta)
        _uni_warn = None
        if not _symbols_complete():
            _uni_warn = (
                (_uni_meta.get("error") or "The Nifty 500 list could not be fully loaded.")
                + f" This backtest covers only {len(_symbols)} stocks."
            )
            print(f"⚠️   {_uni_warn}")
        print(
            f"🧪  Universe: {len(_symbols)} stocks | source={_uni_meta.get('source')} | "
            f"complete={_uni_meta.get('complete')} | "
            f"mode={'top-up' if topup_base is not None else 'full'}"
        )

        for _attempt in (1, 2):
            try:
                if topup_base is not None:
                    result = topup_historical_scan(
                        _fyers, _symbols, target_date, topup_base,
                        universe_meta=_uni_meta, prune_removed=_symbols_complete(),
                        progress=BACKTEST_PROGRESS, cancel=_backtest_cancel,
                    )
                else:
                    result = run_historical_scan(
                        _fyers, _symbols, target_date,
                        progress=BACKTEST_PROGRESS, cancel=_backtest_cancel,
                        universe_meta=_uni_meta,
                    )
                break
            except FyersAuthError as exc:
                if _attempt == 2:
                    raise
                print(f"🧪  Fyers rejected the session ({exc}) — reconnecting and restarting the fetch once.")
                _fyers = reconnect_fyers()
                progress_run = BACKTEST_PROGRESS.start(
                    total=len(_symbols), target_date=target_date.isoformat()
                )
        progress_error = result.get("error")
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
                # Completeness: every stock that was not evaluated, with its reason.
                "attempted": report.get("attempted", 0),
                "failed_symbols": report.get("failed_symbols", []),
                "short_history": report.get("short_history", 0),
                "stale": report.get("stale", 0),
                "skipped": report.get("ledger", {}),
                # Universe proof: where the Nifty 500 list came from and that every
                # stock in it has a row (missing_result_rows must be empty).
                "universe": report.get("universe"),
                "universe_total": report.get("universe_total"),
                "no_data_symbols": report.get("no_data_symbols", 0),
                "topup": report.get("topup"),
            },
            "universe_warning": _uni_warn,
            "saved_at": datetime.datetime.now(_IST).isoformat(),
            "partial": bool(report.get("failed", 0)) or bool(_uni_warn),
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
            # Persist as the shared result (even if the job entry vanished).
            _backtest_state["result"] = payload
            _backtest_state["running_job"] = None
            _backtest_state["error"] = payload.get("error")
            _tu = report.get("topup") or {}
            _backtest_state["notice"] = _uni_warn or (
                f"Saved backtest updated to the current Nifty 500: added "
                f"{', '.join(_tu.get('added') or []) or 'none'}; re-requested from Fyers "
                f"{', '.join(_tu.get('retried') or []) or 'none'}; removed "
                f"{', '.join(_tu.get('removed') or []) or 'none'}."
                if topup_base is not None else None
            )
            _bt_commit_locked()
        # Persist per date so returning to this date never needs another scan.
        if not payload.get("error"):
            try:
                _save_scan_result(
                    "backtest", target_date.isoformat(), payload,
                    partial=bool(payload.get("partial")),
                )
                print(f"🧪  Backtest result saved for {target_date.isoformat()}")
            except Exception as exc:
                print(f"🧪  WARNING: could not save backtest result: {exc}")
    except ScanCancelled:
        cancelled = True
        print(f"🧪  Backtest stopped by user: id={job_id} date={target_date.isoformat()}")
        with _backtest_lock:
            if job_id in _backtest_jobs:
                _backtest_jobs[job_id]["status"] = "cancelled"
            _backtest_state["running_job"] = None
            _backtest_state["error"] = None
            _why = f" because {_backtest_stop_reason}" if _backtest_stop_reason else ""
            _backtest_state["notice"] = (
                f"Backtest for {target_date.strftime('%d %b %Y')} was stopped{_why}. "
                "The previous result is kept — press Rescan to run it again."
            )
            _backtest_stop_reason = None
            _bt_commit_locked()
    except Exception as e:
        progress_error = str(e)
        print(f"🧪  Backtest job failed: id={job_id} error={e}")
        with _backtest_lock:
            if job_id in _backtest_jobs:
                _backtest_jobs[job_id]["status"] = "error"
                _backtest_jobs[job_id]["error"] = str(e)
            # Keep the previous completed result; just record the failure.
            _backtest_state["running_job"] = None
            _backtest_state["error"] = str(e)
            _bt_commit_locked()
    finally:
        BACKTEST_PROGRESS.finish(error=progress_error, run_id=progress_run, cancelled=cancelled)
        _backtest_cancel.clear()
        _heavy_release()
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
        "history_store"   : history_store.health(),
        "sweep"           : _sweep.health(),
        "entry_detection" : entry_detect.status(),
    })


@app.route("/api/sweep/status")
def api_sweep_status():
    return jsonify(_sweep.health())


@app.route("/api/sweep/shadow")
def api_sweep_shadow():
    """Shadow mode: newest-first differences between each hourly scan and the sweep."""
    return jsonify({
        "mode": _cfg.SWEEP_MODE,
        "entries": sweep_mod.shadow_log(int(request.args.get("limit", 50) or 50)),
    })


def _dir_bytes(path: str) -> int:
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
    except OSError:
        pass
    return total


@app.route("/api/system/overview", methods=["GET"])
def api_system_overview():
    """
    Admin-only. ONE place for the whole picture: Fyers calls per minute, history
    store, active mode and sweep timing, entry detection (armed, last hit),
    push summary (from the audit log), publication counts and the size of every
    file this work added. Read-only; sends nothing, changes nothing.
    """
    if not _is_admin():
        return jsonify({"error": "Admin access required"}), 403

    signals = load_signals()
    counts = {"published": 0, "draft": 0, "hidden": 0, "entry_reached_live": 0}
    for sg in signals:
        if sg.get("enabled") is False or sg.get("active") is False:
            counts["hidden"] += 1
        elif sg.get("published"):
            counts["published"] += 1
        else:
            counts["draft"] += 1
        if sg.get("entry_reached_at"):
            counts["entry_reached_live"] += 1

    def _size(path):
        try:
            return round(os.path.getsize(path) / 1024, 1)
        except OSError:
            return None

    try:
        import psutil
        mem_mb = round(psutil.Process(os.getpid()).memory_info().rss / 1_048_576, 1)
    except Exception:
        mem_mb = None

    sweep_health = _sweep.health()
    audit = push_audit.recent(5)
    return jsonify({
        "fyers": {
            "calls_last_minute": sweep_health.get("fyers_calls_last_minute"),
            "cap_per_minute": sweep_health.get("fyers_calls_per_minute_cap"),
            "last_sweep_calls": sweep_health.get("last_sweep_calls"),
        },
        "mode": {
            "sweep_mode": _cfg.SWEEP_MODE,
            "serving_live": _sweep.serving_live(),
            "entry_detection_enabled": bool(_cfg.ENTRY_DETECTION_ENABLED),
            "live_feed_speedup": "not built (sweep-only detection)",
        },
        "history_store": history_store.health(),
        "sweep": sweep_health,
        "entry_detection": entry_detect.status(),
        "push": {
            "configured": push_alerts.is_configured(),
            "devices": device_count(),
            "sends_today": push_audit.sends_today(),
            "daily_cap": _cfg.PUSH_DAILY_MAX_MANUAL,
            "latest": audit,
            "automatic_push": False,     # structural: no code path sends without an admin button
        },
        "signals": counts,
        "storage_kb": {
            "app_signals": _size(_cfg.APP_SIGNALS_FILE),
            "entry_hits": _size(_cfg.ENTRY_HITS_FILE),
            "push_audit_log": _size(_cfg.PUSH_AUDIT_LOG_FILE),
            "alert_log": _size(_cfg.ALERT_LOG_FILE),
            "scan_results_folder": round(_dir_bytes(_cfg.SCAN_RESULTS_DIR) / 1024, 1),
        },
        "memory_mb": mem_mb,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Entry detection (Phase 5) — ADMIN ONLY.
# None of these routes is in _APP_READABLE_RULES, so the mobile app can never
# call them, and none of them sends a notification or publishes anything.
# ─────────────────────────────────────────────────────────────────────────────

entry_detect.configure(signals_provider=lambda: load_signals())


@app.route("/api/entries/status", methods=["GET"])
def api_entries_status():
    """Admin-only. Is detection on, what is armed, last hit, last sweep used."""
    if not _is_admin():
        return jsonify({"error": "Admin access required"}), 403
    return jsonify({**entry_detect.status(), "sweep": {
        "mode": _cfg.SWEEP_MODE, "serving_live": _sweep.serving_live(),
    }})


@app.route("/api/entries/hits", methods=["GET"])
def api_entries_hits():
    """
    Admin-only. Detected entry hits, newest first. ?days=1 (today) .. 7.
    Adds age, the price now (from the latest sweep — no Fyers call) and the
    linked signal's publication state.
    """
    if not _is_admin():
        return jsonify({"error": "Admin access required"}), 403
    try:
        days = max(1, min(30, int(request.args.get("days", 1))))
    except ValueError:
        days = 1

    signals = {s["id"]: s for s in load_signals()}
    with _sweep.lock:
        rows = dict(_sweep.rows)
    out = []
    for h in entry_hits.hits(days):
        row = rows.get(f"NSE:{h['symbol']}-EQ") or {}
        item = dict(h)
        sig = signals.get(h.get("signal_id") or "")
        if sig is not None:
            if sig.get("enabled") is False or sig.get("active") is False:
                item["signal_state"] = "Hidden"
            else:
                item["signal_state"] = "Published" if sig.get("published") else "Draft"
            item["entry_reached_live"] = bool(
                sig.get("entry_reached_at") and sig.get("entry_reached_hit_id") == h.get("id"))
            item["signal_entry_price"] = sig.get("entry_price")
        item.update(_entry_staleness(h, row))
        out.append(item)
    return jsonify({
        "hits": out, "detection": entry_detect.status(),
        "stale_minutes": _cfg.ENTRY_STALE_MINUTES,
        "market_open": _is_market_open(),
    })


def _entry_staleness(hit: dict, row: dict | None) -> dict:
    """
    How old a hit is and how far the price has run past its level (price from
    the latest sweep — no Fyers call). Used by the hits list AND enforced by the
    publish-entry-reached endpoint, so the website can only display what the
    server will require.
    """
    out = {"age_minutes": None, "price_now": None, "now_extended_pct": None,
           "stale": False, "stale_reasons": []}
    try:
        at = datetime.datetime.fromisoformat(hit["detected_at"])
        out["age_minutes"] = max(0, int((datetime.datetime.now(_IST) - at).total_seconds() // 60))
    except (KeyError, TypeError, ValueError):
        pass
    lp = (row or {}).get("lp")
    out["price_now"] = lp
    level, direction = hit.get("level"), hit.get("direction")
    try:
        if lp and level and direction in ("up", "down"):
            level, lp = float(level), float(lp)
            pct_past = (lp - level) / level * 100.0 if direction == "up" else (level - lp) / level * 100.0
            out["now_extended_pct"] = round(max(pct_past, 0.0), 2)
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    reasons = []
    if out["age_minutes"] is not None and out["age_minutes"] > _cfg.ENTRY_STALE_MINUTES:
        reasons.append(f"The level was reached {out['age_minutes']} minutes ago "
                       f"(limit {_cfg.ENTRY_STALE_MINUTES}).")
    if hit.get("extended"):
        reasons.append("The price was already past the level when it was detected.")
    if (out["now_extended_pct"] or 0) > _cfg.ENTRY_EXTENDED_PCT:
        reasons.append(f"The price is now {out['now_extended_pct']}% past the level.")
    if hit.get("late_start"):
        reasons.append("Detection started late today, so the detection time is not the touch time.")
    out["stale"] = bool(reasons)
    out["stale_reasons"] = reasons
    return out


def _hit_status_change(hit_id: str, status: str):
    if not _is_admin():
        return jsonify({"error": "Admin access required"}), 403
    rec = entry_hits.set_status(hit_id, status, session.get("username"))
    if rec is None:
        return jsonify({"error": "Hit not found"}), 404
    return jsonify({"hit": rec})


@app.route("/api/entries/hits/<string:hit_id>/review", methods=["POST"])
def api_entries_review(hit_id: str):
    """Admin-only. Mark a hit as reviewed. Detection for its signal continues."""
    return _hit_status_change(hit_id, "reviewed")


@app.route("/api/entries/hits/<string:hit_id>/dismiss", methods=["POST"])
def api_entries_dismiss(hit_id: str):
    """Admin-only. Dismiss a hit. For an admin signal this also stops detection for it until re-armed or its entry price is edited."""
    return _hit_status_change(hit_id, "dismissed")


@app.route("/api/signals/<string:signal_id>/rearm", methods=["POST"])
def api_signals_rearm(signal_id: str):
    """Admin-only. Start entry detection afresh for a signal (clears 'done'). Sends nothing."""
    if not _is_admin():
        return jsonify({"error": "Admin access required"}), 403
    if not any(s.get("id") == signal_id for s in load_signals()):
        return jsonify({"error": "Signal not found"}), 404
    if not entry_hits.rearm_signal(signal_id):
        return jsonify({"error": "This signal is not armed (needs an entry price, enabled, "
                                 "and a sweep with detection on)."}), 409
    return jsonify({"rearmed": True, "id": signal_id})


@app.route("/api/entries/signal-states", methods=["GET"])
def api_entries_signal_states():
    """
    Admin-only. Per admin signal: is detection armed, its direction, today's hit
    and whether entry-reached is published. Lives here (not in /api/signals) so
    the app-facing signals feed never reads the entry-hit store.
    """
    if not _is_admin():
        return jsonify({"error": "Admin access required"}), 403
    arms = entry_hits.arms_snapshot()
    latest: dict[str, dict] = {}
    for h in reversed(entry_hits.hits(1)):          # oldest first, newest wins
        if h.get("signal_id"):
            latest[h["signal_id"]] = h
    out = {}
    for s in load_signals():
        sid = s["id"]
        arm, hit = arms.get(sid), latest.get(sid)
        if arm is None and hit is None and not s.get("entry_reached_at"):
            continue
        out[sid] = {
            "armed": bool(arm and not arm.get("done") and arm.get("direction")),
            "done": bool(arm and arm.get("done")),
            "direction": (arm or {}).get("direction"),
            "hit": ({"id": hit["id"], "status": hit.get("status"), "detected_at": hit.get("detected_at"),
                     "level": hit.get("level")} if hit else None),
            "entry_reached_published": bool(s.get("entry_reached_at")),
            "entry_reached_at": s.get("entry_reached_at"),
        }
    return jsonify({"states": out})


@app.route("/api/entries/hits/<string:hit_id>/publish-entry-reached", methods=["POST"])
def api_entries_publish_reached(hit_id: str):
    """
    Admin-only. Publish "entry reached" for an ADMIN signal to the app, with an
    optional phone notification (type "entry_reached"). Never automatic.

    Body: {"confirm": true, "notify": false, "acknowledge_stale": false,
           "send_again": false}
    Rules: the signal must be Published and live; the hit must describe the
    signal's current entry price; a stale / extended hit needs
    "acknowledge_stale"; a notification is refused after the market close and
    goes through the shared Phase 4 guard (duplicate window, one per signal per
    day unless "send_again", daily cap, audit log). The fact is published first;
    a refused notification never undoes it.
    """
    if not _is_admin():
        return jsonify({"error": "Admin access required"}), 403
    payload = request.get_json(silent=True) or {}
    if payload.get("confirm") is not True:
        return jsonify({"error": "Confirmation required", "code": "confirm_required"}), 400
    notify = payload.get("notify") is True
    admin = session.get("username")

    hit = entry_hits.get_hit(hit_id)
    if hit is None:
        return jsonify({"error": "Hit not found"}), 404
    if hit.get("kind") != "admin" or not hit.get("signal_id"):
        return jsonify({"error": "Only a hit on an admin signal can be published as entry reached. "
                                 "For a scanner hit, create a draft signal first.",
                        "code": "not_admin_hit"}), 409
    signal = next((s for s in load_signals() if s.get("id") == hit["signal_id"]), None)
    if signal is None:
        return jsonify({"error": "The signal for this hit no longer exists.", "code": "no_signal"}), 404
    if not push_is_live_signal(signal):
        return jsonify({"error": "Publish the signal to the app first (it is a Draft, hidden or "
                                 "outside its visibility window).", "code": "not_published"}), 409
    try:
        same_level = abs(float(signal.get("entry_price")) - float(hit.get("level"))) < 1e-9
    except (TypeError, ValueError):
        same_level = False
    if not same_level or str(signal.get("symbol", "")).upper() != str(hit.get("symbol", "")).upper():
        return jsonify({"error": "The signal's entry price was changed after this hit. "
                                 "This hit describes the old level and cannot be published.",
                        "code": "level_changed"}), 409

    already = bool(signal.get("entry_reached_at") and signal.get("entry_reached_hit_id") == hit_id)
    if already and not notify:
        return jsonify({"signal": signal, "changed": False})

    # Staleness (server-enforced; price from the sweep, no Fyers call)
    with _sweep.lock:
        row = dict(_sweep.rows.get(f"NSE:{hit['symbol']}-EQ") or {})
    info = _entry_staleness(hit, row)
    if info["stale"] and not already and payload.get("acknowledge_stale") is not True:
        return jsonify({"error": "This hit is old or extended. Confirm \"I understand\" to publish it.",
                        "code": "stale_ack_required", "reasons": info["stale_reasons"],
                        "age_minutes": info["age_minutes"], "price_now": info["price_now"]}), 409
    # Phone notifications are never sent after the close (checked BEFORE anything changes).
    if notify and not _is_market_open():
        return jsonify({"error": "The market is closed. A notification cannot be sent now; "
                                 "you can still publish the fact without one.",
                        "code": "market_closed"}), 409

    entry = signal
    if not already:
        entry = set_entry_reached(signal["id"], {
            "at": hit.get("exact_minute") or hit.get("detected_at"),
            "price": hit.get("price_at_detection"),
            "extended": bool(hit.get("extended")),
            "level": hit.get("level"),
            "hit_id": hit_id,
            "by": admin,
        })
        if entry is None:
            return jsonify({"error": "Signal not found"}), 404
        entry_hits.set_status(hit_id, "entry_reached_published", admin)

    notification = None
    if notify:
        key = str(entry.get("symbol") or "").strip().upper()
        again = payload.get("send_again") is True
        if push_audit.sent_today("entry_reached", key) and not again:
            notification = {"ok": False, "code": "already_sent",
                            "error": "An entry-reached notification for this stock was already sent today. "
                                     "Confirm \"send again\" to repeat it."}
        else:
            try:
                res = manual_push.send_entry_reached(entry, admin, True, send_again=again)
                notification = {"ok": True, "audience": res["audience"]}
            except manual_push.SendRefused as refused:
                notification = {"ok": False, "code": refused.code, "error": refused.message,
                                "status": refused.status}

    print(f"   📣  Entry reached for {entry.get('symbol')} "
          f"{'already published' if already else 'PUBLISHED to the app'} by {admin} — "
          f"{('notification ' + ('sent' if notification and notification.get('ok') else 'not sent')) if notify else 'no notification requested'}")
    body = {"signal": entry, "changed": not already, "stale": info["stale"]}
    if notification is not None:
        body["notification"] = notification
    return jsonify(body)


@app.route("/api/entries/hits/<string:hit_id>/create-draft", methods=["POST"])
def api_entries_create_draft(hit_id: str):
    """
    Admin-only. Turn a SCANNER hit into a Draft signal pre-filled with the stock
    and the SMA44 level as entry price. The signal is a Draft (Phase 3 rules):
    not visible in the app, nothing is sent. Publishing is a separate button.
    """
    if not _is_admin():
        return jsonify({"error": "Admin access required"}), 403
    hit = entry_hits.get_hit(hit_id)
    if hit is None:
        return jsonify({"error": "Hit not found"}), 404
    if hit.get("kind") != "scanner":
        return jsonify({"error": "Only scanner hits can be turned into a draft signal."}), 409
    if hit.get("draft_signal_id"):
        return jsonify({"error": "A draft was already created from this hit."}), 409
    level = hit.get("level")
    fields = {"enabled": True}
    if isinstance(level, (int, float)) and level > 0:
        fields["entry_price"] = round(float(level), 2)
    entry = add_signal(symbol=hit["symbol"], rationale="", added_by=session.get("username"), **fields)
    entry_hits.set_status(hit_id, "draft_created", session.get("username"), draft_signal_id=entry["id"])
    print(f"   📝  Draft signal created from scanner hit {hit['symbol']} by {session.get('username')} — Draft, nothing sent")
    return jsonify({"signal": entry}), 201


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


def _signal_price_fields(payload: dict) -> tuple[dict, str | None]:
    """
    Signals-only numeric fields (entry/exit/stop-loss) — kept separate from
    _content_fields' generic whitelist since no other admin-curated resource
    (learn/insights) has these. All three are optional; a value that IS
    supplied but isn't numeric rejects the save with an error message rather
    than silently dropping it.
    """
    fields: dict = {}
    for key in ("entry_price", "exit_price", "stop_loss"):
        if key not in payload:
            continue
        raw = payload.get(key)
        if raw is None or raw == "":
            fields[key] = None
            continue
        try:
            fields[key] = float(raw)
        except (TypeError, ValueError):
            return {}, f"{key.replace('_', ' ')} must be numeric"
    return fields, None


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
# Website: stock directory for the admin panel's search-as-you-type pickers
# (Signals tab, Weekly Report tab). Not used by the Flutter app.
# ─────────────────────────────────────────────────────────────────────────────

_stock_directory_lock  = threading.Lock()
_stock_directory_cache: dict = {"data": None, "expires_at": 0.0}
_STOCK_DIRECTORY_TTL   = 3600  # seconds — this is a name/symbol list for a
                                # search box, not a price feed, so an hour is
                                # plenty fresh and avoids hammering NSE.


def _build_stock_directory() -> list[dict]:
    """
    One deduplicated {symbol, name} list the admin panel's autocomplete
    searches client-side. Built entirely from data this codebase already
    fetches for other features (data/symbols.py's index baskets plus the
    live-enriched Nifty/Sensex/Bank Nifty constituents), so there is no new
    external data source — just a merged, de-duplicated view of it.
    """
    by_symbol: dict[str, str] = {}

    # Company names where already known (constituents cache/live feed).
    for market_cfg in MARKETS:
        try:
            payload = _get_constituents(market_cfg)
        except Exception:
            continue
        for row in payload.get("stocks", []) or []:
            sym = str(row.get("symbol") or "").strip().upper()
            if not sym:
                continue
            name = str(row.get("company_name") or "").strip()
            if name and (sym not in by_symbol or len(name) > len(by_symbol[sym])):
                by_symbol[sym] = name

    # Broader symbol coverage (name-less is fine — the search matches symbols
    # too) from the same hardcoded/CSV baskets the scanner itself uses.
    from data.symbols import (
        _NIFTY50_FALLBACK, _NIFTY_BANK_FALLBACK, _NIFTY_NEXT50_FALLBACK,
    )
    for sym in (*_NIFTY50_FALLBACK, *_NIFTY_BANK_FALLBACK, *_NIFTY_NEXT50_FALLBACK, *SENSEX30):
        sym = sym.strip().upper()
        by_symbol.setdefault(sym, "")

    try:
        for sym in plain_constituents_for_market("nifty"):
            sym = sym.strip().upper()
            by_symbol.setdefault(sym, "")
    except Exception:
        pass

    return sorted(
        ({"symbol": s, "name": n} for s, n in by_symbol.items()),
        key=lambda r: r["symbol"],
    )


@app.route("/api/stocks", methods=["GET"])
def api_stocks_directory():
    """
    Website-only. {symbol, name} list for the admin panel's stock-picker
    search box (Signals tab, Weekly Report tab). Cached in-process for
    _STOCK_DIRECTORY_TTL since it backs a search box, not a live price feed.
    """
    if not _is_authenticated():
        return jsonify({"error": "Login required"}), 403

    now = time.time()
    with _stock_directory_lock:
        cached = _stock_directory_cache["data"]
        if cached is not None and _stock_directory_cache["expires_at"] > now:
            return jsonify({"stocks": cached})

    stocks = _build_stock_directory()
    with _stock_directory_lock:
        _stock_directory_cache["data"] = stocks
        _stock_directory_cache["expires_at"] = now + _STOCK_DIRECTORY_TTL

    return jsonify({"stocks": stocks})


# ─────────────────────────────────────────────────────────────────────────────
# Consumer app: Signals tab (admin-curated stock picks)
# ─────────────────────────────────────────────────────────────────────────────

# Publication bookkeeping that only the admin website sees.
_SIGNAL_ADMIN_ONLY_FIELDS = (
    "published", "published_at", "published_by", "unpublished_at",
    # Phase 4: manual-notification bookkeeping, admin website only.
    "notified_at", "notified_levels", "update_notified_at",
) + tuple(ENTRY_REACHED_ADMIN_FIELDS)   # Phase 6: only the public entry_reached_* facts reach the app


def _signal_push_state(signal: dict) -> dict:
    """
    Admin-only summary of manual notifications for one signal:
    whether it was announced, when, and whether its levels changed since.
    Legacy signals announced by the old automatic push (push_sent_at) count
    as announced; their levels at that time are unknown, so they never show
    "changed".
    """
    announced_at = signal.get("notified_at") or signal.get("push_sent_at")
    levels = signal.get("notified_levels")
    changed = bool(announced_at and levels and _signal_was_revised(levels, signal))
    return {
        "announced"        : bool(announced_at),
        "announced_at"     : announced_at,
        "update_sent_at"   : signal.get("update_notified_at"),
        "changed_since"    : changed,
    }


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
    # Server-side decision (never a client parameter alone): only a signed-in
    # admin session may ask for the full list. Everyone else — including the
    # app — gets Published AND visible signals only. Drafts, unpublished and
    # deactivated signals never leave the admin view.
    include_hidden = request.args.get("all") == "1" and _is_admin()
    signals = load_signals(
        active_only=not include_hidden,
        published_only=not include_hidden,
    )

    quotes: dict[str, dict] = {}
    symbols = [s["symbol"] for s in signals if s.get("symbol")]
    if symbols and _fyers is not None and _fyers_market_data_allowed():
        quotes = fetch_constituents_quotes_bulk(_fyers, symbols)

    enriched = []
    for s in signals:
        q = quotes.get(str(s.get("symbol", "")).upper(), {})
        item = {
            **s,
            "last_price": q.get("last_price"),
            "change_pct": q.get("change_pct"),
        }
        if include_hidden:
            item["push_state"] = _signal_push_state(s)
        else:
            # Publication / notification bookkeeping is admin-only; the app
            # feed keeps its original shape.
            for key in _SIGNAL_ADMIN_ONLY_FIELDS:
                item.pop(key, None)
        enriched.append(item)

    return jsonify({
        "signals"   : enriched,
        "updated_at": datetime.datetime.now(_IST).isoformat(),
    })


@app.route("/api/signals", methods=["POST"])
def api_signals_add():
    """
    Website-only. Creates or updates an admin-curated stock recommendation.

    A NEW signal is always saved as a Draft (not visible in the app). Saving
    or editing never publishes anything and never sends a notification; use
    POST /api/signals/<id>/publish for that. Editing a Published signal
    changes what app users see immediately.

    Body: {"symbol": "RELIANCE", "enabled": true,
           "entry_price": 2850.0, "exit_price": 3050.0, "stop_loss": 2760.0}.
    Pass "id" to edit an existing signal instead of creating one.
    entry_price/exit_price/stop_loss are all optional; each accepts a
    number or null/blank to clear it, but a non-numeric value rejects the
    save with 400.
    """
    if not _is_admin():
        return jsonify({"error": "Admin access required"}), 403

    payload = request.get_json(silent=True) or {}
    signal_id = payload.get("id")
    symbol    = str(payload.get("symbol", "")).strip()
    rationale = str(payload.get("rationale", "")).strip()
    if not symbol:
        return jsonify({"error": "symbol is required"}), 400

    price_fields, price_err = _signal_price_fields(payload)
    if price_err:
        return jsonify({"error": price_err}), 400

    fields = {**_content_fields(payload), **price_fields}
    if signal_id:
        entry = update_signal(str(signal_id), symbol=symbol, rationale=rationale, **fields)
        if entry is None:
            return jsonify({"error": "Signal not found"}), 404
        return jsonify({"signal": entry})

    entry = add_signal(
        symbol=symbol,
        rationale=rationale,
        added_by=session.get("username"),
        **fields,
    )
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


def _set_signal_publication(signal_id: str, publish: bool):
    """
    Shared body of the publish / unpublish endpoints. Admin cookie session
    only (app tokens are refused by the global auth gate for non-GET calls and
    by _is_admin() here). Requires {"confirm": true} so a stray call cannot
    change what app users see. Sends NO notification.
    """
    if not _is_admin():
        return jsonify({"error": "Admin access required"}), 403

    payload = request.get_json(silent=True) or {}
    if payload.get("confirm") is not True:
        return jsonify({"error": "Confirmation required"}), 400

    current = next((s for s in load_signals() if s.get("id") == signal_id), None)
    if current is None:
        return jsonify({"error": "Signal not found"}), 404
    if publish and not current.get("enabled", True):
        return jsonify({"error": "Signal is deactivated. Re-enable it before publishing."}), 409
    if bool(current.get("published")) == publish:
        return jsonify({"signal": current, "changed": False})

    entry = set_published(signal_id, publish, session.get("username"))
    if entry is None:
        return jsonify({"error": "Signal not found"}), 404

    notification = None
    if publish and payload.get("notify") is True:
        # Optional, explicit "also send a phone notification" tick box.
        # Publishing has already succeeded; a refused notification never undoes it.
        notification = _manual_signal_notification(entry, "new", payload)

    print(
        f"   📣  Signal {entry.get('symbol')} ({signal_id}) "
        f"{'PUBLISHED to the app' if publish else 'UNPUBLISHED (hidden from the app)'} "
        f"by {session.get('username')} — "
        f"{'notification requested' if notification else 'no notification sent'}"
    )
    body = {"signal": entry, "changed": True}
    if notification is not None:
        body["notification"] = notification
    return jsonify(body)


def _manual_signal_notification(signal: dict, kind: str, payload: dict) -> dict:
    """
    Send the "new signal" (kind="new") or "update" (kind="update") notification
    for a Published signal through the shared manual-send guard.
    Returns {"ok": True, "audience": N} or {"ok": False, "error", "code"}.
    Only called from admin endpoints.
    """
    admin = session.get("username")
    if not push_is_live_signal(signal):
        return {"ok": False, "code": "not_published",
                "error": "Only a Published, visible signal can be announced."}
    try:
        send = manual_push.send_new_signal if kind == "new" else manual_push.send_revised_signal
        result = send(signal, admin, payload.get("confirm") is True,
                      send_again=payload.get("send_again") is True)
    except manual_push.SendRefused as refused:
        return {"ok": False, "code": refused.code, "error": refused.message,
                "status": refused.status}
    set_notification_state(signal["id"], kind)
    return {"ok": True, "audience": result["audience"], "kind": kind}


def push_is_live_signal(signal: dict) -> bool:
    from data.app_signals import is_live
    return is_live(signal)


@app.route("/api/signals/<string:signal_id>/notify", methods=["POST"])
def api_signals_notify(signal_id: str):
    """
    Website-only. Manually send a phone notification for a Published signal.

    Body: {"confirm": true, "kind": "new" | "update", "send_again": false}
      new    — "new signal" notification (once; "send_again" repeats it)
      update — "signal updated" notification, only if the symbol or price
               levels changed since the last notification (or "send_again")
    A Draft, unpublished or deactivated signal can never be announced.
    """
    if not _is_admin():
        return jsonify({"error": "Admin access required"}), 403

    payload = request.get_json(silent=True) or {}
    kind = str(payload.get("kind") or "new").lower()
    if kind not in ("new", "update"):
        return jsonify({"error": "kind must be \"new\" or \"update\""}), 400
    if payload.get("confirm") is not True:
        return jsonify({"error": "Confirmation required", "code": "confirm_required"}), 400

    signal = next((s for s in load_signals() if s.get("id") == signal_id), None)
    if signal is None:
        return jsonify({"error": "Signal not found"}), 404

    state = _signal_push_state(signal)
    again = payload.get("send_again") is True
    if kind == "new" and state["announced"] and not again:
        return jsonify({
            "error": "This signal was already announced. Confirm \"send again\" to repeat it.",
            "code": "already_sent",
        }), 409
    if kind == "update":
        if not state["announced"] and not again:
            return jsonify({
                "error": "Send the \"new signal\" notification first.",
                "code": "not_announced",
            }), 409
        if not state["changed_since"] and not again:
            return jsonify({
                "error": "Nothing changed since the last notification.",
                "code": "no_change",
            }), 409

    result = _manual_signal_notification(signal, kind, payload)
    if not result["ok"]:
        return jsonify({"error": result["error"], "code": result["code"]}), result.get("status", 409)
    return jsonify({"sent": True, "audience": result["audience"], "kind": kind}), 202


@app.route("/api/signals/<string:signal_id>/publish", methods=["POST"])
def api_signals_publish(signal_id: str):
    """Website-only. Draft -> visible in the app. Body: {"confirm": true}. No notification."""
    return _set_signal_publication(signal_id, True)


@app.route("/api/signals/<string:signal_id>/unpublish", methods=["POST"])
def api_signals_unpublish(signal_id: str):
    """Website-only. Hide a signal from the app (back to Draft). Body: {"confirm": true}. No notification."""
    return _set_signal_publication(signal_id, False)


# ─────────────────────────────────────────────────────────────────────────────
# Consumer app: exit calls (website Signals tab → push to the app's Alerts)
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/exits", methods=["GET"])
def api_exits_list():
    """Website-only. Previously sent exit calls, newest first."""
    if not _is_admin():
        return jsonify({"error": "Admin access required"}), 403
    return jsonify({"exits": load_exits()})


@app.route("/api/exits", methods=["POST"])
def api_exits_add():
    """
    Website-only. Saves an exit call and pushes it to the phones (manual send,
    through the shared guard in alerts/manual_push.py).

    Body: {"symbol": "RELIANCE", "profit": 120.0, "exit_price": 2850.0,
           "confirm": true, "send_again": false}
    profit is per share in ₹; a negative number is a loss. These three values
    are the whole exit call.

    Response: {"exit": {...}, "notified": true|false} — notified is False when
    push isn't configured on the server (the call is still saved).
    """
    if not _is_admin():
        return jsonify({"error": "Admin access required"}), 403

    payload = request.get_json(silent=True) or {}
    symbol = " ".join(str(payload.get("symbol", "")).split()).upper()
    if not symbol:
        return jsonify({"error": "stock is required"}), 400

    try:
        profit = float(payload.get("profit"))
        exit_price = float(payload.get("exit_price"))
    except (TypeError, ValueError):
        return jsonify({"error": "profit and exit price must be numbers"}), 400
    if not (math.isfinite(profit) and math.isfinite(exit_price)):
        return jsonify({"error": "profit and exit price must be numbers"}), 400
    if exit_price <= 0:
        return jsonify({"error": "exit price must be greater than zero"}), 400

    # Manual send through the shared guard. If push is configured the guard
    # runs BEFORE anything is saved, so a refused (duplicate / capped / unconfirmed)
    # exit call leaves no record. If push is not configured the call is still
    # saved, exactly as before, and no phone is notified.
    admin = session.get("username")
    notified = False
    audience = 0
    if push_alerts.is_configured():
        try:
            sent = manual_push.send_exit(
                symbol, profit, exit_price, admin,
                payload.get("confirm") is True,
                send_again=payload.get("send_again") is True,
            )
            notified, audience = True, sent["audience"]
        except manual_push.SendRefused as refused:
            return jsonify({"error": refused.message, "code": refused.code}), refused.status
        except Exception as e:
            print(f"   ⚠️   Push: could not send exit call — {e}")
    entry = add_exit(symbol, profit, exit_price, admin)
    return jsonify({"exit": entry, "notified": notified, "audience": audience}), 201


@app.route("/api/exits/<string:exit_id>", methods=["DELETE"])
def api_exits_delete(exit_id: str):
    """
    Website-only. Removes an exit call from the history list. This does not
    recall the notification that was already sent.
    """
    if not _is_admin():
        return jsonify({"error": "Admin access required"}), 403
    if not delete_exit(exit_id):
        return jsonify({"error": "Exit call not found"}), 404
    return jsonify({"deleted": True, "id": exit_id})


# ─────────────────────────────────────────────────────────────────────────────
# Consumer app: push notifications (FCM)
# ─────────────────────────────────────────────────────────────────────────────

# The fields whose change makes an already-announced pick worth a second
# notification. Notes, ordering, pinning, images and the like are housekeeping,
# not a change to the pick itself.
#
# NOTE (Phase 3): nothing in the backend sends a signal notification
# automatically any more. These helpers are kept only so Phase 4's manual
# "Send update notification" button can tell whether the levels changed since
# the last notification.
_SIGNAL_REVISION_FIELDS = ("symbol", "entry_price", "exit_price", "stop_loss")


def _signal_was_revised(previous: dict | None, entry: dict) -> bool:
    """True when the pick's symbol or one of its price levels actually changed."""
    if not previous:
        return False
    for key in _SIGNAL_REVISION_FIELDS:
        before, after = previous.get(key), entry.get(key)
        if key == "symbol":
            if str(before or "").upper() != str(after or "").upper():
                return True
            continue
        if before is None and after is None:
            continue
        if before is None or after is None:
            return True
        try:
            if abs(float(before) - float(after)) > 1e-9:
                return True
        except (TypeError, ValueError):
            return True
    return False


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
        uid=g.app_user["uid"],
    )
    if entry is None:
        return jsonify({"error": "Invalid token, or device limit reached"}), 400
    return jsonify({"registered": True})


@app.route("/api/devices/unregister", methods=["POST"])
def api_devices_unregister():
    """Called when the user switches push off in Settings."""
    payload = request.get_json(silent=True) or {}
    unregister_device(str(payload.get("token") or ""), uid=g.app_user["uid"])
    return jsonify({"registered": False})


@app.route("/api/push/status", methods=["GET"])
def api_push_status():
    """Website-only. Is push set up, how many phones, today's sends vs the cap, recent audit entries."""
    if not _is_admin():
        return jsonify({"error": "Admin access required"}), 403
    from config.settings import PUSH_DAILY_MAX_MANUAL, PUSH_DUPLICATE_WINDOW_SECONDS
    return jsonify({
        "configured": push_alerts.is_configured(),
        "devices"   : device_count(),
        "signal_devices": len(push_alerts.list_devices(topic="signals")),
        # What the last push actually did (sent / failed / why). Null until a
        # push has been attempted since the server last started.
        "last_send" : push_alerts.last_send(),
        "sends_today"         : push_audit.sends_today(),
        "daily_cap"           : PUSH_DAILY_MAX_MANUAL,
        "duplicate_window_sec": int(PUSH_DUPLICATE_WINDOW_SECONDS),
        "audit"               : push_audit.recent(30),
    })


@app.route("/api/push/send", methods=["POST"])
def api_push_send():
    """
    Website-only. Send a custom notification to every registered phone — also
    the quickest way to test the whole chain end to end.
    Body: {"title": "...", "body": "...", "confirm": true, "send_again": false}
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

    try:
        result = manual_push.send_custom(
            title, body, session.get("username"),
            payload.get("confirm") is True,
            send_again=payload.get("send_again") is True,
        )
    except manual_push.SendRefused as refused:
        return jsonify({"error": refused.message, "code": refused.code}), refused.status
    return jsonify({"queued": True, "devices": result["audience"]}), 202


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
# Admin-entered, not computed from live market data — there is no code
# anywhere in this repo that watches a fired signal afterwards to determine
# whether price later hit a target or a stop loss (see
# IMPLEMENTATION_SPEC_weekly_report_and_sentiment.md §A.4). The owner enters
# the week, its stocks, their entry/exit price, and outcome by hand on the
# website; "profit_pct" and "pnl_amount" (₹ profit/loss per share) are both
# calculated automatically from entry_price/exit_price (POST below), never
# typed in directly. These three routes just store and serve that, following
# the exact same GET/POST/DELETE shape as
# /api/learn.
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
              "stocks": [ { "symbol": "RELIANCE",
                            "entry_price": 64.0, "exit_price": 60.0,
                            # Both always derived server-side from entry/exit
                            # price (see POST below) — never hand-typed.
                            "profit_pct": -6.25, "pnl_amount": -4.0,
                            "outcome": "stop_loss",
                            # Phase 6 (trade-card redesign) — all optional,
                            # see data/app_weekly_report.py's docstring:
                            "name": "", "bullish": true, "trade_label": "",
                            "date_of_recommendation": "",
                            "exit_date": "", "duration_days": null } ],
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
    "stocks": [{"symbol": "RELIANCE", "entry_price": 64.0, "exit_price": 60.0,
    "outcome": "stop_loss"}]}.
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

    "profit_pct" and "pnl_amount" are never accepted as raw manual numbers
    from the client — "entry_price" and "exit_price" are required for every
    row instead, and this route always derives both itself:
      profit_pct = ((exit_price - entry_price) / entry_price) * 100
      pnl_amount = exit_price - entry_price    # ₹ profit/loss per share
    so neither figure can ever drift from the two prices the admin actually
    entered. Any "profit_pct"/"pnl_amount" present in the request body is
    ignored.

    Phase 6 (trade-card redesign) adds a set of further OPTIONAL per-row
    fields — "name", "bullish", "trade_label", "date_of_recommendation",
    "exit_date", "duration_days" — on top of the required
    "symbol"/"entry_price"/"exit_price"/"outcome" above. Any of them may be
    omitted or left blank; only a value that IS supplied but isn't the
    right shape (e.g. a non-numeric duration_days) rejects the save, same
    "small admin form, not a bulk import" reasoning as the required fields.
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

    def _optional_float(row: dict, key: str, symbol: str):
        raw = row.get(key)
        if raw is None or raw == "":
            return None, None
        try:
            return float(raw), None
        except (TypeError, ValueError):
            return None, f"invalid {key} for {symbol}"

    def _optional_int(row: dict, key: str, symbol: str):
        raw = row.get(key)
        if raw is None or raw == "":
            return None, None
        try:
            return int(raw), None
        except (TypeError, ValueError):
            return None, f"invalid {key} for {symbol}"

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

        entry_price, err = _optional_float(row, "entry_price", symbol)
        if err:
            return jsonify({"error": err}), 400
        exit_price, err = _optional_float(row, "exit_price", symbol)
        if err:
            return jsonify({"error": err}), 400
        # Return % and Profit/Share are both calculated automatically from
        # entry/exit price, never accepted as raw manual numbers — so both
        # prices are required, and whatever "profit_pct"/"pnl_amount" the
        # client may have sent is ignored below.
        if entry_price is None or exit_price is None:
            return jsonify({
                "error": f"entry_price and exit_price are required for {symbol} "
                         "to calculate return % and profit per share"
            }), 400
        if entry_price == 0:
            return jsonify({"error": f"entry_price for {symbol} must not be zero"}), 400
        profit_pct = round(((exit_price - entry_price) / entry_price) * 100, 4)
        pnl_amount = round(exit_price - entry_price, 4)

        duration_days, err = _optional_int(row, "duration_days", symbol)
        if err:
            return jsonify({"error": err}), 400

        stocks.append({
            "symbol"                : symbol,
            "profit_pct"            : profit_pct,
            "outcome"               : outcome,
            "name"                  : str(row.get("name") or "").strip(),
            "bullish"               : bool(row.get("bullish", True)),
            "trade_label"           : str(row.get("trade_label") or "").strip(),
            "entry_price"           : entry_price,
            "exit_price"            : exit_price,
            "pnl_amount"            : pnl_amount,
            "date_of_recommendation": str(row.get("date_of_recommendation") or "").strip(),
            "exit_date"             : str(row.get("exit_date") or "").strip(),
            "duration_days"         : duration_days,
        })

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
    if _state.get("scanning") or _heavy_kind is not None:
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

def _scan_report_summary(fetch_report: dict) -> dict:
    """Small, JSON-safe completeness summary of a live scan (no Fyers calls)."""
    keys = (
        "attempted", "valid", "no_data", "failed", "recovered", "persistent_retries",
        "short_history", "stale", "cached_no_data", "evaluated", "failed_symbols",
        "evaluation_errors", "ledger", "universe_total", "universe",
    )
    return _json_safe({k: fetch_report.get(k) for k in keys if k in fetch_report})


def _do_scan(lock_held: bool = False):
    global _fyers, _symbols

    # Take the shared Fyers lock. A scheduled scan has priority: if a backtest
    # is running it is STOPPED (its previous saved result is kept) and the scan
    # starts as soon as the backtest winds down. A manual Rescan has already
    # acquired the lock (and is refused while a backtest runs).
    global _backtest_stop_reason
    if not lock_held:
        _live_cancel.clear()
        _state["scan_waiting"] = _heavy_lock.locked()
        if _state["scan_waiting"]:
            if _heavy_kind == "backtest":
                print("🛑  Scheduled scan is due — stopping the running backtest first …")
                _backtest_stop_reason = "a scheduled scan started"
                _backtest_cancel.set()
                BACKTEST_PROGRESS.request_stop()
            elif _heavy_kind == "history":
                print("🛑  Scheduled scan is due — pausing the history download (previous store kept) …")
                _history_cancel.set()
            else:
                print("⏳  Scheduled scan is waiting for the running scan to finish …")
        try:
            _heavy_acquire_wait("live", _live_cancel)
        except ScanCancelled:
            _state["scan_waiting"] = False
            _state["notice"] = "Scan stopped before it started."
            return
        _state["scan_waiting"] = False

    _sweep_run_lock.acquire()      # waits (seconds) for a published sweep that is mid-evaluation
    _state["scanning"] = True
    _state["error"]    = None
    _state["notice"]   = None
    progress_run = LIVE_PROGRESS.start(total=len(_symbols or []))
    cancelled = False
    _scan_t0 = time.time()
    try:
        if not _fyers_market_data_allowed():
            _state["error"] = "Scan skipped because the market is not source-confirmed open."
            return

        _refresh_symbols(force_if_incomplete=True)
        LIVE_PROGRESS.set_total(len(_symbols or []))
        _uni_warn = None
        if not _symbols_complete():
            _uni_warn = (
                (_symbols_meta.get("error") or "The Nifty 500 list could not be fully loaded.")
                + f" This scan covers only {len(_symbols or [])} stocks; the list is retried on every scan."
            )
            print(f"⚠️   {_uni_warn}")

        watchlist = clean_watchlist(load_watchlist())
        alert_log = clean_alert_log(load_alert_log())
        save_watchlist(watchlist)
        save_alert_log(alert_log)

        for _attempt in (1, 2):
            try:
                signals, watchlist_items, fetch_report, universe_stats = run_scan(
                    fyers     = _fyers,
                    symbols   = _symbols,
                    interval  = "D",
                    watchlist = watchlist,
                    alert_log = alert_log,
                    progress  = LIVE_PROGRESS,
                    cancel    = _live_cancel,
                )
                break
            except FyersAuthError as exc:
                if _attempt == 2:
                    raise
                print(f"⚠️   Fyers rejected the session ({exc}) — reconnecting and restarting the fetch once.")
                _fyers = reconnect_fyers()
                progress_run = LIVE_PROGRESS.start(total=len(_symbols or []))

        _state["signals"]         = signals
        _state["watchlist_items"] = watchlist_items
        _state["scan_time"]       = datetime.datetime.now(_IST).strftime("%d %b %Y %H:%M:%S")
        # total_scanned = symbols actually evaluated (evaluation errors excluded).
        # total_attempted = ground truth len(symbols) — never varies.
        _state["total_scanned"]   = fetch_report.get("evaluated", fetch_report["valid"])
        _state["total_attempted"] = fetch_report["attempted"]
        fetch_report["universe_total"] = len(_symbols or [])
        fetch_report["universe"] = {
            "index": "NIFTY 500",
            "source": _symbols_meta.get("source"),
            "count": len(_symbols or []),
            "complete": _symbols_complete(),
            "built_at": _symbols_meta.get("built_at"),
            "sources_tried": _symbols_meta.get("sources_tried"),
            "fyers_master": {
                k: v for k, v in (_symbols_meta.get("fyers_master") or {}).items()
                if k in ("loaded", "source", "listed_on_fyers", "remapped", "not_in_master")
            },
        }
        _state["scan_report"]     = _scan_report_summary(fetch_report)

        _notices = []
        if _uni_warn:
            _notices.append(_uni_warn)
        if fetch_report.get("failed"):
            _notices.append(
                f"{fetch_report['failed']} stock(s) could not be fetched from Fyers after all retries "
                "(see /api/scan/skipped). Rescan to try them again."
            )
        if _notices:
            _state["notice"] = " ".join(_notices)

        # Insights (spec §2) — persist so a restart doesn't blank the charts
        # until the next scan, and update the in-memory copy the
        # /api/insights/* endpoints read from.
        _state["universe_stats"] = universe_stats
        saved_stats = save_universe_stats(universe_stats)
        _state["universe_stats_as_of"] = saved_stats["as_of"]

        # Persist the day's latest scan so a restart does not lose it.
        try:
            _save_scan_result("live", datetime.datetime.now(_IST).date().isoformat(), _json_safe({
                "signals": signals, "watchlist_items": watchlist_items,
                "scan_time": _state["scan_time"], "total_scanned": _state["total_scanned"],
                "total_attempted": _state["total_attempted"], "scan_report": _state["scan_report"],
            }), partial=bool(fetch_report.get("failed")))
        except Exception as exc:
            print(f"⚠️   Could not save live scan result: {exc}")

        # Shadow mode: compare this hourly scan with the one-minute sweep (log only).
        sweep_mod.compare_with_legacy(signals, watchlist_items, _scan_t0, time.time())

    except ScanCancelled:
        cancelled = True
        _state["error"]  = None
        _state["notice"] = "Scan stopped. The previous results are kept."
        print("🛑  Scan stopped by user — previous results kept.")
    except Exception as e:
        _state["error"] = str(e)
    finally:
        try:
            _sweep_run_lock.release()
        except RuntimeError:
            pass
        # `scanning` flips first so that by the time the website sees the
        # progress tracker go inactive, /api/results already reports the
        # finished scan.
        _state["scanning"] = False
        LIVE_PROGRESS.finish(error=_state.get("error"), run_id=progress_run, cancelled=cancelled)
        _live_cancel.clear()
        _heavy_release()
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
    global _fyers, _last_auth_date

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
            reauth_ok = False
            with _auth_lock:
                if _last_auth_date == today:      # the history-store job logged in meanwhile
                    reauth_ok = True
                else:
                    for attempt in range(3):
                        try:
                            _fyers = reconnect_fyers()
                            _last_auth_date = today
                            reauth_ok = True
                            break
                        except Exception as e:
                            print(f"   ⚠️  Re-auth attempt {attempt + 1}/3 failed: {e}")
                            time.sleep(30)
            if not reauth_ok:
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

        if _sweep.serving_live():
            print(f"\n🟣  Slot {datetime.datetime.now(_IST).strftime('%H:%M')} IST — the one-minute sweep is "
                  "healthy and live, so the hourly history scan is skipped (it runs again if the sweep fails).")
            continue

        print(f"\n🟢  Active check — {datetime.datetime.now(_IST).strftime('%H:%M')} IST")
        _do_scan()


# ─────────────────────────────────────────────────────────────────────────────
# Daily history store (live-entry plan, Phase 1) — independent thread
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_fyers_today() -> "object":
    """
    A Fyers client authenticated today.  Reuses the session the scan loop (or an
    earlier call) already created; otherwise tries today's cached token and only
    then runs the automated login.  Shares _last_auth_date with _scan_loop, so
    the once-per-day login rule is unchanged: whoever needs Fyers first logs in.
    """
    global _fyers, _last_auth_date
    today = datetime.datetime.now(_IST).date()
    with _auth_lock:
        if _fyers is not None and _last_auth_date == today:
            return _fyers
        client = client_from_cached_token()
        if client is not None:
            print("🔑  History store: reusing today's cached Fyers token.")
        else:
            print("🔑  History store: logging in to Fyers ahead of the open …")
            client = reconnect_fyers()
        _fyers = client
        _last_auth_date = today
        return client


def _previous_weekday(day: datetime.date) -> datetime.date:
    d = day - datetime.timedelta(days=1)
    while d.weekday() >= 5:
        d -= datetime.timedelta(days=1)
    return d


def _history_download_time() -> datetime.time:
    try:
        hh, mm = str(_cfg.HISTORY_STORE_DOWNLOAD_TIME).split(":", 1)
        return datetime.time(int(hh), int(mm))
    except Exception:
        return datetime.time(8, 45)


def _history_due(now: datetime.datetime, last_check: datetime.date | None) -> bool:
    """
    True when the job should run now.  At most one completed check per IST day.
      * no valid store            → run now (catch-up after a fresh deploy / bad file)
      * weekday at/after the time → the daily job
      * otherwise                 → catch-up only if the store is older than the
                                    previous weekday (late start / long downtime)
    """
    today = now.date()
    if last_check == today:
        return False
    snap = history_store.get_snapshot()
    if snap is None or not snap.frames:
        return True
    if now.weekday() < 5 and now.time() >= _history_download_time():
        return True
    return snap.as_of < _previous_weekday(today)


def _history_loop() -> None:
    """
    Keeps the on-disk history store current without touching scans.

    Decides "download or skip" with a single probe request (see
    data/history_store.refresh_if_needed), so weekends and holidays cost one
    request and no download.  The download takes the shared heavy-job lock, and a
    due scheduled scan pauses it (the previous store is never touched by a
    paused / failed download); it is retried after HISTORY_STORE_RETRY_MINUTES.
    """
    global _fyers, _last_auth_date
    if not history_store.is_enabled():
        print("📦  History store job disabled (HISTORY_STORE_ENABLED=false).")
        return
    print(f"📦  History store job started (daily at {_history_download_time().strftime('%H:%M')} IST on weekdays, "
          "plus catch-up after a late start).")
    last_check: datetime.date | None = None
    retry_not_before = 0.0
    while True:
        try:
            now = datetime.datetime.now(_IST)
            if time.time() >= retry_not_before and _history_due(now, last_check):
                if not _heavy_try_acquire("history"):
                    time.sleep(30)          # a scan / backtest holds Fyers; try again shortly
                    continue
                _history_cancel.clear()
                failed = False
                try:
                    if not _symbols_complete():
                        _refresh_symbols(force_if_incomplete=True)
                    client = _ensure_fyers_today()
                    for _attempt in (1, 2):
                        try:
                            result = history_store.refresh_if_needed(
                                client, list(_symbols or []),
                                cancel=_history_cancel,
                                universe_complete=_symbols_complete(),
                            )
                            break
                        except FyersAuthError as exc:
                            if _attempt == 2:
                                raise
                            print(f"⚠️   History store: Fyers rejected the session ({exc}) — logging in again.")
                            with _auth_lock:
                                _fyers = client = reconnect_fyers()
                                _last_auth_date = datetime.datetime.now(_IST).date()
                    if result.get("action") in ("downloaded", "skipped_up_to_date", "disabled"):
                        last_check = now.date()
                    else:
                        failed = True
                except ScanCancelled:
                    failed = True
                except Exception as exc:
                    failed = True
                    print(f"⚠️   History store job failed: {type(exc).__name__}: {exc}")
                finally:
                    _history_cancel.clear()
                    _heavy_release()
                    _state["last_heavy_fyers_op_at"] = time.time()
                if failed:
                    retry_not_before = time.time() + float(_cfg.HISTORY_STORE_RETRY_MINUTES) * 60.0
                    print(f"📦  History store: will retry in {_cfg.HISTORY_STORE_RETRY_MINUTES:.0f} min "
                          "(the previous store, if any, is untouched).")
        except Exception as exc:
            print(f"⚠️   History store loop error: {exc}")
        time.sleep(30)


# ─────────────────────────────────────────────────────────────────────────────
# One-minute quote sweep (live-entry plan, Phase 2) — independent thread
# ─────────────────────────────────────────────────────────────────────────────

_sweep_fail_log_at = 0.0


def _sweep_note_failure(why: str) -> None:
    """Count a failed sweep; log the first one at once, then at most every 10 minutes."""
    global _sweep_fail_log_at
    _sweep.mark_failed(why)
    if time.time() - _sweep_fail_log_at > 600 or _sweep.consecutive_failures == 1:
        _sweep_fail_log_at = time.time()
        print(f"⚠️   Sweep failed ({_sweep.consecutive_failures} in a row): {why}")
        if _sweep.consecutive_failures == _cfg.SWEEP_MAX_CONSECUTIVE_FAILURES and _cfg.SWEEP_MODE == "live":
            print("   ↪ The hourly history scan takes over until the sweep recovers.")


def _sweep_publish(out, *, final: bool) -> None:
    """Publish a live sweep exactly where the hourly scan publishes (same fields, same files)."""
    fr = out.fetch_report
    fr["universe_total"] = len(_symbols or [])
    fr["universe"] = {
        "index": "NIFTY 500",
        "source": _symbols_meta.get("source"),
        "count": len(_symbols or []),
        "complete": _symbols_complete(),
        "built_at": _symbols_meta.get("built_at"),
        "sources_tried": _symbols_meta.get("sources_tried"),
        "fyers_master": {
            k: v for k, v in (_symbols_meta.get("fyers_master") or {}).items()
            if k in ("loaded", "source", "listed_on_fyers", "remapped", "not_in_master")
        },
    }
    report = _scan_report_summary(fr)
    report["source"] = "sweep"
    notice = None
    if fr.get("failed"):
        notice = (f"{fr['failed']} stock(s) had no live quote in the latest sweep; "
                  "they are retried automatically on the next one.")
    now_s = datetime.datetime.now(_IST).strftime("%d %b %Y %H:%M:%S")

    # build everything first, then assign (readers never see a half-updated result)
    _state["signals"]         = out.signals
    _state["watchlist_items"] = out.watchlist_items
    _state["scan_time"]       = now_s
    _state["total_scanned"]   = fr.get("evaluated", fr["valid"])
    _state["total_attempted"] = fr["attempted"]
    _state["scan_report"]     = report
    _state["universe_stats"]  = out.universe_stats
    _state["error"]           = None
    _state["notice"]          = notice
    _state["next_scan_time"]  = _format_ist(
        datetime.datetime.now(_IST) + datetime.timedelta(seconds=_cfg.SWEEP_INTERVAL_SECONDS))

    membership = (
        tuple(sorted(d["symbol"] for d in out.signals)),
        tuple(sorted(d["symbol"] for d in out.watchlist_items)),
    )
    with _sweep.lock:
        due = (
            final
            or membership != _sweep.last_saved_membership
            or time.time() - _sweep.last_saved_at >= _cfg.SWEEP_SAVE_INTERVAL_SECONDS
        )
        if due:
            _sweep.last_saved_at = time.time()
            _sweep.last_saved_membership = membership
    if due:
        try:
            saved_stats = save_universe_stats(out.universe_stats)
            _state["universe_stats_as_of"] = saved_stats["as_of"]
        except Exception as exc:
            print(f"⚠️   Sweep: could not save insights stats: {exc}")
        try:
            _save_scan_result("live", datetime.datetime.now(_IST).date().isoformat(), _json_safe({
                "signals": out.signals, "watchlist_items": out.watchlist_items,
                "scan_time": now_s, "total_scanned": _state["total_scanned"],
                "total_attempted": _state["total_attempted"], "scan_report": report,
            }), partial=bool(fr.get("failed")))
        except Exception as exc:
            print(f"⚠️   Sweep: could not save live scan result: {exc}")


def _sweep_tick() -> None:
    global _fyers, _last_auth_date
    mode = _cfg.SWEEP_MODE
    now = datetime.datetime.now(_IST)
    today = now.date()
    t = now.time()
    start_t = sweep_mod.parse_hhmm(_cfg.SWEEP_START_TIME, datetime.time(9, 16))
    final_t = sweep_mod.parse_hhmm(_cfg.SWEEP_FINAL_TIME, datetime.time(15, 46))

    closing = False
    if _is_market_open() and t >= start_t and now.weekday() < 5:
        if not _fyers_market_data_allowed():
            return                                  # holiday / not source-confirmed open
    elif (mode == "live" and final_t <= t <= datetime.time(16, 30)
          and _sweep.sweeps_today_day == today and _sweep.sweeps_today > 0
          and _sweep.closing_done_day != today):
        closing = True                              # one closing sweep after the bell
    else:
        return

    if time.time() < _sweep.backoff_until:
        _sweep.mark_skipped("backing off after a Fyers rate limit")
        return
    if _fyers is None or _last_auth_date != today:
        _sweep.mark_skipped("waiting for today's Fyers login")
        return
    _refresh_symbols()                              # once per day (cheap otherwise)
    ok, why = sweep_mod.store_ready(list(_symbols or []))
    if not ok or not _symbols_complete():
        _sweep.mark_skipped(why or "stock universe incomplete")
        return

    publish = mode == "live"
    if publish:
        if _state.get("scanning") or _heavy_kind == "live":
            _sweep.mark_skipped("an hourly scan is running")
            return
        if not _sweep_run_lock.acquire(blocking=False):
            _sweep.mark_skipped("an hourly scan is running")
            return
    try:
        try:
            out = sweep_mod.run_sweep(_fyers, list(_symbols), publish=publish, cancel=_sweep_cancel)
        except FyersAuthError as exc:
            _sweep_note_failure(f"Fyers rejected the session ({exc})")
            if not _state.get("scanning"):
                try:
                    with _auth_lock:
                        _fyers = reconnect_fyers()
                        _last_auth_date = today
                except Exception as exc2:
                    print(f"⚠️   Sweep: re-login failed: {exc2}")
            return
        except ScanCancelled:
            return
        if not out.ok:
            if out.rate_limited:
                _sweep.backoff_until = time.time() + _cfg.SWEEP_RATE_LIMIT_BACKOFF_SECONDS
                print(f"⚠️   Sweep: Fyers rate limit — sweeps pause for {_cfg.SWEEP_RATE_LIMIT_BACKOFF_SECONDS:.0f}s "
                      "(the hourly scan is never paused).")
            _sweep_note_failure(out.why)
            return
        if publish:
            _sweep_publish(out, final=closing)
            if closing:
                _sweep.closing_done_day = today
                print("🔔  Closing sweep saved — final result of the session.")
        else:
            sweep_mod.remember_shadow(out)
        summary = {
            "signals": len(out.signals), "watchlist": len(out.watchlist_items),
            "evaluated": out.fetch_report.get("evaluated"), "failed_quotes": out.fetch_report.get("failed"),
            "published": publish,
        }
        _sweep.mark_ok(out.duration, out.calls, summary)
        if publish and not closing and _sweep.serving_live():
            # Admin-only entry detection (Phase 5). Uses this sweep's data, makes
            # no Fyers calls and can never notify or publish anything.
            entry_detect.process_sweep(out, _fyers, now)
        n = _sweep.sweeps_ok
        if n == 1 or n % max(1, _cfg.SWEEP_LOG_EVERY) == 0:
            print(
                f"📡  Sweep #{n} ({mode}): {summary['evaluated']} stocks evaluated, "
                f"{summary['signals']} signals / {summary['watchlist']} watchlist, "
                f"{out.calls} quote calls, {out.duration:.1f}s — "
                f"Fyers calls last minute: {sweep_mod.fyers_calls_last_minute()}/"
                f"{_cfg.FYERS_MAX_REQUESTS_PER_MINUTE}"
            )
    finally:
        if publish:
            try:
                _sweep_run_lock.release()
            except RuntimeError:
                pass


def _sweep_loop() -> None:
    """
    Layer 1: evaluates the whole universe every SWEEP_INTERVAL_SECONDS from ~10
    quote requests.  Independent thread; never overlaps itself; any failure only
    means the hourly scan keeps (or resumes) doing the job.
    """
    if _cfg.SWEEP_MODE == "off":
        print("📡  Quote sweep: off (SWEEP_MODE=off) — the hourly scan runs as before.")
        return
    print(f"📡  Quote sweep started in {_cfg.SWEEP_MODE.upper()} mode "
          f"(every {_cfg.SWEEP_INTERVAL_SECONDS:.0f}s from {_cfg.SWEEP_START_TIME} IST"
          + ("; results are NOT published, they are compared with each hourly scan)" if _cfg.SWEEP_MODE == "shadow" else ")"))
    next_t = time.monotonic()
    while True:
        try:
            _sweep_tick()
        except Exception as exc:
            _sweep_note_failure(f"{type(exc).__name__}: {exc}")
        next_t += _cfg.SWEEP_INTERVAL_SECONDS
        delay = next_t - time.monotonic()
        if delay < 1.0:                 # running behind: never burst to catch up
            next_t = time.monotonic() + 1.0
            delay = 1.0
        time.sleep(delay)


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

    result = _sweep.latest_breadth()          # from the one-minute sweep: 0 Fyers calls
    if result is not None:
        print("   📊  Breadth taken from the one-minute sweep (no extra Fyers calls).")
    else:
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

    raw = _sweep.latest_ltp(list(sym_map.keys()))      # sweep prices: no Fyers call
    if raw is None:
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

    _set_universe(*fetch_nifty500_with_meta())
    print("   Auth will run automatically after Yahoo/NSE confirm a trading day, before the first scan slot.")

    # Restore cached Insights stats and full-breadth snapshot from disk so
    # neither screen is blank between process restart and the next
    # scan / breadth slot.
    loaded_stats = load_universe_stats()
    _state["universe_stats"]       = loaded_stats["stats"]
    _state["universe_stats_as_of"] = loaded_stats["as_of"]
    _breadth_full_cache = load_full_breadth()
    _close_snapshot     = load_close_snapshot()

    # Restore today's latest saved scheduled scan so a restart does not blank
    # the Scanner page until the next slot.
    try:
        _saved_live = _load_scan_result("live", datetime.datetime.now(_IST).date().isoformat())
        if _saved_live and not _state["signals"]:
            _p = _saved_live["payload"]
            _state["signals"]         = _p.get("signals", [])
            _state["watchlist_items"] = _p.get("watchlist_items", [])
            _state["scan_time"]       = _p.get("scan_time")
            _state["total_scanned"]   = _p.get("total_scanned", 0)
            _state["total_attempted"] = _p.get("total_attempted", 0)
            _state["scan_report"]     = _p.get("scan_report")
            print(f"📂  Restored today's saved scan ({_state['scan_time']})")
    except Exception as exc:
        print(f"⚠️   Could not restore saved live scan: {exc}")

    # A backtest that was running when the server stopped can never finish —
    # clear it so every user sees the last completed result instead of a
    # spinner that never ends. The previous results are kept untouched.
    with _backtest_lock:
        if _backtest_state.get("running_job"):
            interrupted = _backtest_state["running_job"].get("date")
            _backtest_state["running_job"] = None
            _backtest_state["error"] = (
                f"The backtest for {interrupted} was interrupted by a server restart. "
                "Run it again if you need it; the previous results are shown."
            )
            _bt_commit_locked()

    history_store.load_on_startup()          # disk → memory, no Fyers calls

    summary = get_log_summary()
    print(f"\n📋  Signal log : {summary['total_signals']} signals across {summary['days_logged']} day(s)")
    print(f"    Stocks     : {len(_symbols)}")

    slot_labels = [f"{h:02d}:{ACTIVE_CHECK_MINUTE:02d}" for h in ACTIVE_CHECK_HOURS]
    print(
        f"\n🔄  Starting fixed-slot scan loop "
        f"({slot_labels[0]}–{slot_labels[-1]} IST via {', '.join(slot_labels)}) …"
    )
    threading.Thread(target=_scan_loop, daemon=True, name="scan-loop").start()
    threading.Thread(target=_history_loop, daemon=True, name="history-store-loop").start()
    threading.Thread(target=_sweep_loop, daemon=True, name="sweep-loop").start()

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
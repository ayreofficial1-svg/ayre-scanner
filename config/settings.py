"""
config/settings.py
──────────────────
Configuration for the simplified 3-condition scanner.

Legend:
  ✅  = actively used as a hard gate
  ℹ   = informational / logged but not a gate
  ❌  = intentionally removed from the new scanner

Conditions implemented:
  C1  — Daily SMA44 trend passes point, regression, or recovery sub-test,
        plus medium-term slope and recent-half consistency checks.
  C2  — Latest bar touches SMA44 within the support buffer and closes
        at or above SMA44.
  C3  — Bullish MACD crossover within the last N bars, or an imminent
        crossover promoted to signal.
"""

import os
from dotenv import load_dotenv

from config.persistence import resolve as _persist

# ── Load .env file ─────────────────────────────────────────────────────────────
load_dotenv()


def _env_str(name: str, default: str = "") -> str:
    """Read an environment variable as a trimmed string."""
    return str(os.getenv(name, default) or "").strip()


def _normalize_fyers_app_id(raw_app_id: str, raw_app_type: str) -> tuple[str, str, str]:
    """
    Normalize Fyers app identifiers.

    The environment sometimes stores the bare app id (e.g. "ABCD1234"), while
    the SDK expects the full app client id (e.g. "ABCD1234-100"). Accept both.
    """
    app_id = (raw_app_id or "").strip()
    app_type = (raw_app_type or "100").strip() or "100"

    if ":" in app_id:
        app_id = app_id.split(":", 1)[0].strip()

    if "-" in app_id:
        maybe_base, maybe_type = app_id.rsplit("-", 1)
        if maybe_base and maybe_type.isdigit():
            return maybe_base, maybe_type, app_id

    if not app_id:
        return "", app_type, ""

    return app_id, app_type, f"{app_id}-{app_type}"

# ── Fyers Credentials ──────────────────────────────────────────────────────────
FYERS_APP_ID_RAW     = _env_str("FYERS_APP_ID")
FYERS_SECRET_KEY     = _env_str("FYERS_SECRET_KEY")
FYERS_CLIENT_ID      = _env_str("FYERS_CLIENT_ID")
FYERS_PIN            = _env_str("FYERS_PIN")
FYERS_TOTP_KEY       = _env_str("FYERS_TOTP_KEY")
FYERS_REDIRECT_URI   = _env_str("FYERS_REDIRECT_URI", "http://127.0.0.1")
FYERS_APP_ID_BASE, FYERS_APP_TYPE, FYERS_APP_ID_FULL = _normalize_fyers_app_id(
    FYERS_APP_ID_RAW,
    _env_str("FYERS_APP_TYPE", "100"),
)

# Backward-compatible aliases kept for older imports.
FYERS_APP_ID         = FYERS_APP_ID_BASE
FYERS_CLIENT_ID_FULL = FYERS_APP_ID_FULL

# ── Token cache ────────────────────────────────────────────────────────────────
TOKEN_FILE = ".fyers_token"

# ── Indicator Periods ✅ ───────────────────────────────────────────────────────
SMA_PERIOD   = 44   # SMA44 — only SMA used
MACD_FAST    = 12
MACD_SLOW    = 26
MACD_SIGNAL  = 9

SMA_SLOPE_LOOKBACK = 44            # bars back for slope comparison

# ── C1a: Short-window rising check (replaces single-bar comparison) ✅ ──────────
C1A_LOOKBACK = 10       # point-to-point check over N bars (one trading week)

# ── C1a: Linear regression window and minimum slope ✅ ────────────────────────
C1A_LINREG_WINDOW = 15          # regression fitted over last N SMA44 bars (two weeks)
C1A_LINREG_SLOPE_MIN = -0.00030 # change, from 15 to 30
                                # minimum normalised slope per bar; negative allows
                                 # marginal consolidation noise without permitting
                                 # genuine declines (-0.005% of SMA level per bar)

# ── Legacy C1a tolerance retained for older imports ℹ ───────────────────────
# SMA44[today] >= SMA44[yesterday] * (1 - SLOPE_TOLERANCE)
#
# Rationale: A strict today > yesterday check rejects stocks where the SMA44
# dips by a fraction of a point (e.g. -0.08) within an otherwise clear uptrend.
# A 0.1% tolerance absorbs micro-dips (noise) while still catching genuine
# downtrends, which produce deltas far larger than 0.1%.
#
# Example: SMA44 = 574.58, yesterday = 574.67 → delta = -0.09 (-0.016%)
#   Strict check → FAIL  (delta < 0)
#   Tolerance    → PASS  (delta > -574.67 * 0.001 = -0.57)
SLOPE_TOLERANCE = 0.001            # 0.1% tolerance on C1a day-over-day comparison

# ── C1b: SMA44 slope over SMA_SLOPE_LOOKBACK bars ✅ ─────────────────────────
# PCT_SLOPE_MIN: minimum % growth of SMA44 over the last 44 bars.
#   Set to -0.01 (−1%) to accept flat or very gently declining SMAs.
#   These represent consolidation-then-bounce setups where price is respecting
#   the SMA44 as support even though the average itself is not yet rising.
#
# PCT_SLOPE_MAX: max % growth — still rejects overextended parabolic moves.
#
# ATR_SLOPE_MIN: ATR-normalised slope minimum.
#   Set to 0.0 to remove the ATR floor for flat/recovering trends.
#   The original 0.30 threshold was calibrated for clear uptrends; setting it
#   to 0.0 lets consolidating stocks through without sacrificing the PCT bounds.
PCT_SLOPE_MIN = -0.01              # allow up to -1% decline over 44 bars (was 0.02)
PCT_SLOPE_MAX = 0.80               # max % growth (excludes overextended)
ATR_SLOPE_MIN = 0.0                # remove ATR floor for flat trends (was 0.30)

# ── C1: SMA44 Trend ✅ ───────────────────────────────────────────────────────
# Definition:
#   C1a) any of point-check, regression, or recovery sub-test passes
#   C1b) PCT_SLOPE_MIN <= (SMA44[t] / SMA44[t-44]) - 1 <= PCT_SLOPE_MAX
#   C1b) and (SMA44[t] - SMA44[t-44]) / ATR14[t] > ATR_SLOPE_MIN
#   C1c) recent half of the 44-bar SMA slope is not materially declining

# ── C2: SMA44 Support Interaction ✅ ─────────────────────────────────────────
# Definition: Two sub-conditions all must pass on today's bar.
#
#   C2a) abs(Low[today] - SMA44[today]) / SMA44[today] <= SMA44_SUPPORT_BUFFER_PCT
#   C2b) Close[today] >= SMA44[today]
#
# NOTE: C2c (Close > Open, bullish body / doji filter) has been removed.
SMA44_SUPPORT_BUFFER_PCT = 0.02   # 2% proximity window -- |low - SMA44| / SMA44

# ── C3: Bullish MACD Crossover ✅ ─────────────────────────────────────────────
MACD_CROSSOVER_LOOKBACK = 3   # bars: 1 = today only, 3 = today + prior 2 days

# ── C3 Watchlist: Imminent Crossover ✅ ───────────────────────────────────────
IMMINENT_HIST_MIN      = 2     # minimum consecutive rising histogram bars
IMMINENT_GAP_THRESHOLD = 0.20  # gap <= 20% of |Signal| to qualify as imminent

# ── Minimum data requirement ✅ ────────────────────────────────────────────────
MIN_BARS = 60   # conservative floor; newly listed stocks with < 60 bars skipped

# ── Watchlist TTL ✅ ───────────────────────────────────────────────────────────
WATCHLIST_TTL_DAYS = 5

# ── Persistent storage (Phase O) ───────────────────────────────────────────────
# The small files wrapped in _persist(...) below (watchlist, alert log, admin
# content, exit calls, push-device list) live on the Railway Volume when
# PERSISTENT_DATA_DIR (or Railway's own RAILWAY_VOLUME_MOUNT_PATH) is set;
# otherwise they stay at these relative paths exactly as before.
# Everything else (scan_results/, logs/, caches, tokens, uploads) stays on the
# normal disk. See config/persistence.py.

# ── Alert deduplication ✅ ─────────────────────────────────────────────────────
WATCHLIST_FILE = _persist("watchlist.json")
ALERT_LOG_FILE = _persist("alert_log.json")
SIGNAL_LOG_DIR = "logs"

# ── Consumer app: admin-curated signals, learn content, insights ─────────────
APP_SIGNALS_FILE       = _persist("app_signals.json")
APP_LEARN_FILE         = _persist("app_learn.json")
APP_INSIGHTS_FILE      = _persist("app_insights.json")
APP_WEEKLY_REPORT_FILE = _persist("app_weekly_report.json")
APP_EXITS_FILE         = _persist("app_exits.json")   # exit calls sent from the Signals tab
APP_ASSET_DIR          = "static/uploads"
# Sentiment is computed automatically from Nifty-500 breadth as of Phase 2
# (see data/app_sentiment.py) — APP_SENTIMENT_FILE/APP_SENTIMENT_DEFAULT (the
# old hand-set-value file and its placeholder score) are retired; nothing
# else in the repo referenced them (verified by repo-wide search).

# Optional: restrict POST/DELETE on /api/signals and /api/learn to a subset of
# SCANNER_USERS. Comma-separated usernames, e.g. "raghav,admin".
# If unset, any authenticated session may write (matches today's single-tier auth).
APP_ADMIN_USERS_ENV = "SCANNER_ADMIN_USERS"

# ── Double Bottom Pattern Detection ℹ ──────────────────────────────────────────
DOUBLE_BOTTOM_LOOKBACK = 20    # bars to search for a prior SMA44 support touch (double-bottom pattern)

# ── Weekly Rising Pre-Filter ✅ ────────────────────────────────────────────────
WEEKLY_RISING_FILTER = True      # If True, weekly SMA44 trend is computed (from daily bars, 0 API calls)
                                 # and reported as the informational "weekly_rising" field on every result.
WEEKLY_FILTER_EXCLUDES = False   # If True, symbols whose weekly SMA44 is NOT rising are removed from the
                                 # scan universe before evaluation (old behaviour — drops most stocks).
                                 # If False (default), the FULL universe is evaluated and the weekly trend
                                 # is only attached as metadata.
WEEKLY_C1A_LOOKBACK = 10         # same point-check logic applied to weekly bars

# ── Quality Stock Whitelist ℹ ──────────────────────────────────────────────────
QUALITY_STOCK_WHITELIST = []     # If non-empty, only these symbols are scanned
                                 # e.g. ["NSE:ITC-EQ", "NSE:TCS-EQ", "NSE:HDFCBANK-EQ"]
                                 # Populate manually from screener.in export

# ── Alert channels ℹ  ─────────────────────────────────────────────────────────
ALERT_EMAIL_FROM  = os.getenv("ALERT_EMAIL_FROM",  "")
ALERT_EMAIL_TO    = os.getenv("ALERT_EMAIL_TO",    "")
ALERT_EMAIL_PASS  = os.getenv("ALERT_EMAIL_PASS",  "")
ALERT_SMTP_HOST   = os.getenv("ALERT_SMTP_HOST",   "smtp.gmail.com")
ALERT_SMTP_PORT   = int(os.getenv("ALERT_SMTP_PORT", "587"))

# ── SEBI RA compliance ℹ  ─────────────────────────────────────────────────────
RA_REGISTRATION_NUMBER = os.getenv("RA_REG_NUMBER", "INH000XXXXXX")
DISCLAIMER = (
    "This research report is published by a SEBI-registered Research Analyst "
    f"(Registration No: {RA_REGISTRATION_NUMBER}). "
    "Investments in securities market are subject to market risks. "
    "Read all related documents carefully before investing. "
    "Past performance is not indicative of future results. "
    "This is not an offer or solicitation to buy or sell any securities."
)

# ── Scheduler ✅ ──────────────────────────────────────────────────────────────
# Fixed hourly Active Check slots (IST, half past each hour).
# Scans fire at exactly 09:30, 10:30 … 15:30. No rolling intervals.
ACTIVE_CHECK_HOURS = [9, 10, 11, 12, 13, 14, 15]
ACTIVE_CHECK_MINUTE = 30

# Passive market-status check interval while market is closed (seconds).
# Checks are aligned to fixed clock boundaries, not scheduled relative to the
# previous check. After market close they run hourly from 16:00 IST onward.
PASSIVE_CHECK_INTERVAL = 3600

# ── Insights: volatility / momentum / volume-surge ✅ ─────────────────────────
# Per-symbol stats captured as a free byproduct of run_scan() (see
# scanner/engine.py). Persisted here so a restart doesn't blank the
# Insights charts until the next scan.
APP_UNIVERSE_STATS_FILE = "universe_stats.json"

# ── Shared Backtest state ✅ ───────────────────────────────────────────────────
# One backtest state for the whole website (selected date, filter, running
# status, last completed results). Written by main.py's backtest endpoints,
# read back on page load / after a restart. Zero Fyers cost — see
# data/backtest_store.py.
BACKTEST_STATE_FILE = "backtest_state.json"

# ── Full Nifty-500 breadth poller ✅ ───────────────────────────────────────────
# Fixed hourly slots, offset 15 minutes after each scanner slot (HH:30) so
# the two schedules are visibly independent. No 15 — market closes 15:30,
# so there's no 15:45 slot. 6 refreshes/day; see main.py::_breadth_loop.
BREADTH_CHECK_HOURS  = [9, 10, 11, 12, 13, 14]
BREADTH_CHECK_MINUTE = 45

# Cached full-universe breadth snapshot, written by _breadth_loop, read by
# GET /api/breadth/full. Persisted so a restart doesn't blank the Home
# donut until the next hourly slot.
BREADTH_FULL_FILE = "breadth_full.json"

# ── End-of-session closing snapshot ✅ ─────────────────────────────────────────
# The last full reading of the index board, movers, index constituents,
# advance/decline counts and signal prices. Refreshed once a minute while the
# market is open and finalised shortly after the 15:30 IST close (see
# main.py::_close_snapshot_loop), then served untouched until the next session
# so the app keeps showing the closing values instead of an empty state.
# Persisted so a restart after the close doesn't lose them.
MARKET_CLOSE_FILE = "market_close.json"

# ── Push notifications (Firebase Cloud Messaging) ✅ ───────────────────────────
# Device registry for the mobile app. Same single-JSON-file pattern as the
# other app_*.json stores.
APP_DEVICES_FILE = _persist("app_devices.json")

# Credentials for the Firebase Admin SDK. Provide ONE of these in the
# environment (Railway → Variables):
#   FIREBASE_SERVICE_ACCOUNT_JSON    the service-account key file's full JSON text
#   FIREBASE_SERVICE_ACCOUNT_BASE64  the same JSON, base64-encoded (use this if
#                                    your host mangles multi-line values)
#   GOOGLE_APPLICATION_CREDENTIALS   path to the key file on disk
# With none of them set, push is simply disabled — every other feature keeps
# working and signal creation never fails because of it.
FIREBASE_SERVICE_ACCOUNT_JSON   = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "")
FIREBASE_SERVICE_ACCOUNT_BASE64 = os.getenv("FIREBASE_SERVICE_ACCOUNT_BASE64", "")

# Android notification channel the app creates at startup (see MainActivity.kt).
# Must match on both sides or Android silently drops to a low-priority channel.
PUSH_ANDROID_CHANNEL_ID = "ayre_signals"

# Hard cap on stored device tokens — the registration endpoint is anonymous
# (the app runs without its login gate), so it must not be able to grow the
# file without bound.
PUSH_MAX_DEVICES = 50000


# ── Scan completeness / persistence ──────────────────────────────────────────
# Per-symbol Fyers resolution cache (which suffix worked; confirmed "no data").
SYMBOL_CACHE_FILE = "symbol_cache.json"
# A live scan re-verifies a confirmed "no Fyers data" symbol after this many
# days. Kept short on purpose so a symbol Fyers adds later is never skipped
# for long. (Backtests never read this cache.)
SYMBOL_NO_DATA_TTL_DAYS = 1
# Saved scan results (one JSON file per scanned date).
SCAN_RESULTS_DIR = "scan_results"
# A stock whose newest candle is older than this many calendar days before the
# scan date is treated as stale (suspended / halted), not evaluated on old prices.
STALE_BAR_MAX_DAYS = 10


# ── Fyers request budget & mapping (data/candles.py, data/fyers_master.py) ───
# Fyers v3 allows 10 requests/second AND 200 requests/minute.  The fetcher keeps
# a rolling 60-second window below the per-minute cap so a full-universe scan
# never runs into 429s (every 429 is a wasted request AND a delayed stock).
FYERS_MAX_REQUESTS_PER_MINUTE = 190
# Public Fyers symbol master (static CSV on public.fyers.in — NOT an API call,
# not counted against the request limits).  Used to map NSE symbols to the exact
# Fyers ticker (series suffix, renamed tickers).  If it cannot be loaded the
# scanner falls back to the previous -EQ/-BE/-BZ/-SM/-ST probing.
FYERS_MASTER_ENABLED = True
FYERS_MASTER_URL = "https://public.fyers.in/sym_details/NSE_CM.csv"
FYERS_MASTER_FILE = "fyers_nse_cm_master.csv"
# If a fetch is aborted by a dead Fyers session, the symbols already fetched are
# kept for this long so the automatic reconnect-and-restart does not re-request
# them.
SCAN_RESUME_MAX_AGE_SECONDS = 600


# ── Mobile-app user authentication (Firebase ID tokens) ──────────────────────
# When true, app-readable endpoints also require the Firebase account's email
# to be verified (403 "email_not_verified" otherwise). Off by default; enforcing
# it later is just this setting plus the app's existing verify prompt.
APP_REQUIRE_VERIFIED_EMAIL = os.getenv("APP_REQUIRE_VERIFIED_EMAIL", "false").strip().lower() in {"1", "true", "yes", "on"}


# ── Daily history store (Phase 1 of the live-entry plan; data/history_store.py) ─
# One on-disk generation of daily candles for the whole universe, downloaded once
# per trading day before the open and loaded into memory.  Phase 1 only builds the
# store; scans, breadth, quotes, the WebSocket and backtests behave as before.
# Every value can be overridden with an environment variable (Railway → Variables).
def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.getenv(name, "")).strip() or default)
    except ValueError:
        return default


HISTORY_STORE_ENABLED        = _env_bool("HISTORY_STORE_ENABLED", True)
# IST wall-clock time (HH:MM) of the daily download on weekdays.
HISTORY_STORE_DOWNLOAD_TIME  = _env_str("HISTORY_STORE_DOWNLOAD_TIME", "08:45")
# A new generation is published only if at least this share of the universe is stored ...
HISTORY_STORE_MIN_COVERAGE   = _env_float("HISTORY_STORE_MIN_COVERAGE", 0.95)
# ... and at least this share of the stored stocks end on the expected session date.
HISTORY_STORE_MIN_FRESH_SHARE = _env_float("HISTORY_STORE_MIN_FRESH_SHARE", 0.90)
# A download is not started with less free space than this on the data volume.
HISTORY_STORE_MIN_FREE_MB    = _env_float("HISTORY_STORE_MIN_FREE_MB", 200.0)
# Sub-folder (of the persistent data dir, or of the working dir when persistence is off).
HISTORY_STORE_DIR_NAME       = _env_str("HISTORY_STORE_DIR_NAME", "history_store")
# Liquid instruments used by the one-request "has a new session completed?" probe.
HISTORY_STORE_PROBE_SYMBOLS  = [
    s.strip() for s in _env_str(
        "HISTORY_STORE_PROBE_SYMBOLS", "NSE:NIFTY50-INDEX,NSE:RELIANCE-EQ"
    ).split(",") if s.strip()
]
# After a failed or cancelled attempt, wait this long before trying again.
HISTORY_STORE_RETRY_MINUTES  = _env_float("HISTORY_STORE_RETRY_MINUTES", 15.0)


# ── One-minute quote sweep (Phase 2 of the live-entry plan; scanner/sweep.py) ─
# Every SWEEP_INTERVAL_SECONDS the whole universe is priced with ~10 paced Fyers
# quote requests, today's candle is built from the quote (open / day high / day
# low / last / volume) and appended to the stored history (Phase 1), and the
# SAME scan code (scanner/engine.run_scan) evaluates it.
#   off     — nothing changes (default)
#   shadow  — sweep runs and is compared with every hourly scan; nothing published
#   live    — sweep results are published; the hourly scan is skipped while the
#             sweep is healthy and runs exactly as before when it is not
SWEEP_MODE = _env_str("SWEEP_MODE", "off").lower()
if SWEEP_MODE not in {"off", "shadow", "live"}:
    SWEEP_MODE = "off"
SWEEP_INTERVAL_SECONDS       = max(20.0, _env_float("SWEEP_INTERVAL_SECONDS", 60.0))
# First sweep of the day (IST HH:MM) — keeps the opening-auction noise out.
SWEEP_START_TIME             = _env_str("SWEEP_START_TIME", "09:16")
# One closing sweep after the bell so the saved result is the final one.
SWEEP_FINAL_TIME             = _env_str("SWEEP_FINAL_TIME", "15:46")
# A sweep is discarded when more than this share of stocks got no quote.
SWEEP_MAX_FAILED_SHARE       = _env_float("SWEEP_MAX_FAILED_SHARE", 0.20)
# This many failed sweeps in a row → the sweep is "unhealthy" and the hourly scan takes over.
SWEEP_MAX_CONSECUTIVE_FAILURES = int(_env_float("SWEEP_MAX_CONSECUTIVE_FAILURES", 3))
# A sweep result older than this no longer counts as healthy.
SWEEP_HEALTHY_MAX_AGE_SECONDS = _env_float("SWEEP_HEALTHY_MAX_AGE_SECONDS", 180.0)
# A stock whose quote batch failed re-uses its previous quote for at most this long.
SWEEP_QUOTE_CARRY_SECONDS    = _env_float("SWEEP_QUOTE_CARRY_SECONDS", 180.0)
# Wait this long after Fyers answers "rate limit" before sweeping again.
SWEEP_RATE_LIMIT_BACKOFF_SECONDS = _env_float("SWEEP_RATE_LIMIT_BACKOFF_SECONDS", 120.0)
# Saved scan result / insights file are rewritten at most this often (or when membership changes).
SWEEP_SAVE_INTERVAL_SECONDS  = _env_float("SWEEP_SAVE_INTERVAL_SECONDS", 300.0)
# While the sweep is healthy, breadth and signal prices come from it (0 extra Fyers calls).
SWEEP_REUSE_FOR_BREADTH      = _env_bool("SWEEP_REUSE_FOR_BREADTH", True)
SWEEP_REUSE_FOR_QUOTES       = _env_bool("SWEEP_REUSE_FOR_QUOTES", True)
# One summary line in the log every N sweeps (problems are always logged at once).
SWEEP_LOG_EVERY              = int(_env_float("SWEEP_LOG_EVERY", 10))
# Shadow-mode comparison log (bounded; newest kept).
SWEEP_SHADOW_LOG_FILE        = _persist("sweep_shadow_log.json")
SWEEP_SHADOW_LOG_MAX         = int(_env_float("SWEEP_SHADOW_LOG_MAX", 300))


# ── Manual notification centre (Phase 4) ─────────────────────────────────────
# Every notification is sent by an explicit admin button. These only tune the
# safety guard; there is deliberately NO setting that turns automatic pushing on.
PUSH_DAILY_MAX_MANUAL         = max(1, int(_env_float("PUSH_DAILY_MAX_MANUAL", 30)))
PUSH_DUPLICATE_WINDOW_SECONDS = max(0.0, _env_float("PUSH_DUPLICATE_WINDOW_SECONDS", 60.0))
PUSH_AUDIT_LOG_FILE           = _persist(_env_str("PUSH_AUDIT_LOG_FILE", "push_audit_log.json"))
PUSH_AUDIT_LOG_MAX            = max(50, int(_env_float("PUSH_AUDIT_LOG_MAX", 500)))


# ── Entry detection, admin-only (Phase 5) ────────────────────────────────────
# Detects when an admin signal's entry price or a scanner stock's SMA44 line is
# touched and records it in an ADMIN-ONLY file. It can never notify anyone or
# change what app users see (one-way wall, see scanner/entry_detect.py).
# Off by default; needs SWEEP_MODE=live and a healthy sweep.
ENTRY_DETECTION_ENABLED       = _env_bool("ENTRY_DETECTION_ENABLED", False)
ENTRY_SCANNER_STOCKS_ENABLED  = _env_bool("ENTRY_SCANNER_STOCKS_ENABLED", True)
# No detection before this IST time (opening-auction noise).
ENTRY_NOISE_UNTIL_TIME        = _env_str("ENTRY_NOISE_UNTIL_TIME", "09:15:30")
# A hit is flagged "extended" when the price is already this many % past the level.
ENTRY_EXTENDED_PCT            = _env_float("ENTRY_EXTENDED_PCT", 0.5)
# Admin signals are armed only while younger than this many days.
ENTRY_ARM_MAX_AGE_DAYS        = int(_env_float("ENTRY_ARM_MAX_AGE_DAYS", 10))
ENTRY_STORE_RETENTION_DAYS    = max(1, int(_env_float("ENTRY_STORE_RETENTION_DAYS", 7)))
ENTRY_MAX_HITS_PER_DAY        = max(1, int(_env_float("ENTRY_MAX_HITS_PER_DAY", 200)))
# Optional e-mail to the admin address (same ALERT_EMAIL_* settings as scan alerts).
ENTRY_ADMIN_EMAIL_ENABLED     = _env_bool("ENTRY_ADMIN_EMAIL_ENABLED", False)
ENTRY_ADMIN_EMAIL_MAX_PER_DAY = max(1, int(_env_float("ENTRY_ADMIN_EMAIL_MAX_PER_DAY", 20)))
# Optional: one paced 1-minute history call per admin-signal hit to record the exact touch minute.
ENTRY_EXACT_MINUTE_ENABLED    = _env_bool("ENTRY_EXACT_MINUTE_ENABLED", False)
ENTRY_HITS_FILE               = _persist("entry_hits.json")

# ── Entry-reached publication (Phase 6) ──────────────────────────────────────
# A hit older than this many minutes needs an extra "I understand" confirmation
# before the admin may publish it as "entry reached".
ENTRY_STALE_MINUTES           = max(1, int(_env_float("ENTRY_STALE_MINUTES", 15)))

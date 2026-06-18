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

# ── Alert deduplication ✅ ─────────────────────────────────────────────────────
WATCHLIST_FILE = "watchlist.json"
ALERT_LOG_FILE = "alert_log.json"
SIGNAL_LOG_DIR = "logs"

# ── Double Bottom Pattern Detection ℹ ──────────────────────────────────────────
DOUBLE_BOTTOM_LOOKBACK = 20    # bars to search for a prior SMA44 support touch (double-bottom pattern)

# ── Weekly Rising Pre-Filter ✅ ────────────────────────────────────────────────
WEEKLY_RISING_FILTER = True      # If True, require weekly SMA44 also rising before signaling
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

# Passive check interval while market is closed (seconds).
PASSIVE_CHECK_INTERVAL = 300   # 5 minutes

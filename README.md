# Nifty 500 Swing Scanner — Fyers API

## Project Structure

```
nifty_scanner/
├── main.py                  ← Entry point — run this
├── .env                     ← Your credentials (never share this)
├── requirements.txt         ← pip install -r requirements.txt
│
├── config/
│   ├── __init__.py
│   └── settings.py          ← All configuration constants
│
├── auth/
│   ├── __init__.py
│   └── fyers_auth.py        ← Fyers auto-login (TOTP + PIN, fully automated)
│
├── data/
│   ├── __init__.py
│   ├── symbols.py           ← Fetches Nifty 500 symbol list from NSE
│   └── candles.py           ← Fetches daily/weekly OHLCV candles from Fyers
│
├── indicators/
│   ├── __init__.py
│   └── technical.py         ← SMA44 + MACD computation
│
├── scanner/
│   ├── __init__.py
│   ├── conditions.py        ← The 3 signal conditions
│   ├── watchlist.py         ← Persistent watchlist (JSON)
│   └── engine.py            ← Main scan loop
│
├── alerts/
│   ├── __init__.py
│   └── notify.py            ← Terminal + sound + desktop + email alerts
│
├── reports/
│   ├── __init__.py
│   └── html_report.py       ← HTML report builder
│
└── utils/
    ├── __init__.py
    └── logger.py            ← Signal log for SEBI 5-year record keeping
```

## Setup

### 1. Install dependencies
Use Python 3.11 or 3.12. The Fyers SDK currently pins an `aiohttp` version that does not install cleanly on Windows with Python 3.14.

```bash
pip install -r requirements.txt
```

### 2. Create your Fyers app
1. Go to https://myapi.fyers.in/dashboard/
2. Click **Create App**
3. Fill in:
   - App Name: `NiftyScanner`
   - Redirect URL: `https://www.google.com`
   - Permissions: check **Data APIs**
4. Note your **App ID** and **Secret Key**

### 3. Enable TOTP on your Fyers account
1. Go to https://myaccount.fyers.in/ManageAccount
2. Enable **External 2FA TOTP**
3. Copy the **TOTP Key** (the text string, not just the QR code)
4. Scan the QR with Google Authenticator too (for your own login)

### 4. Set up your .env file
```
FYERS_APP_ID=XXXXXX
FYERS_SECRET_KEY=XXXXXX
FYERS_CLIENT_ID=TK01234
FYERS_PIN=1234
FYERS_TOTP_KEY=ABCDEFGHIJKLMNOP
FYERS_REDIRECT_URI=https://www.google.com

# Optional email alerts
ALERT_EMAIL_FROM=you@gmail.com
ALERT_EMAIL_TO=you@gmail.com
ALERT_EMAIL_PASS=your_gmail_app_password
```

### 5. Run
```bash
python main.py
```

## How it works

The scanner runs continuously and automatically, but scan execution is fixed to
hourly IST slots: 9:30 AM, 10:30 AM, 11:30 AM, 12:30 PM, 1:30 PM, 2:30 PM,
and 3:30 PM. Slots are clock-aligned, so a slow scan does not push the next
scan to one hour after completion.

Fyers is used only after free market-status sources confirm that the Indian
market is open. Outside market hours, and on holidays, the app uses Yahoo
Finance/NSE checks instead of spending Fyers requests.

After market close, passive market-status checks are fixed to clock times:
hourly from 4:00 PM IST onward, plus a 9:15 AM IST pre-open check so the app
can authenticate before the first 9:30 AM scan. These passive checks use only
Yahoo Finance/NSE and never call Fyers.

**Three conditions must ALL be true:**
1. SMA44 passes the daily C1 trend checks, optionally after the weekly SMA44 rising pre-filter.
2. The latest daily candle touches SMA44 within the configured buffer and closes at or above SMA44.
3. MACD (12/26/9) has a confirmed or imminent bullish crossover.

Stocks passing 1+2 but not 3 go into the **watchlist** while MACD remains pending. Payloads include informational tags such as `ma_type`, `is_double_bottom`, `price_interaction_type`, and `weekly_rising`.

## Token refresh
Fyers token is refreshed **automatically each source-confirmed trading day**
using your TOTP key + PIN before the first 9:30 AM scan. No manual steps needed
after initial setup, provided the FYERS credentials and TOTP secret remain valid.

## Legal
This tool is intended for use by SEBI-registered Research Analysts (RA).
All signals are logged to `logs/signal_log.json` for 5-year SEBI record-keeping compliance.


## Authentication: two separate identity systems

- **Website admin** — Flask cookie session via `/api/auth/login`, credentials from Railway env (`SCANNER_USERS`, `SCANNER_ADMIN_USERS`). Unchanged. Only this system can satisfy `_is_admin()`.
- **Mobile app users** — Firebase Authentication (email/password). The app sends `Authorization: Bearer <Firebase ID token>`; the backend verifies it with the Firebase Admin SDK (`auth/app_auth.py`, shared credentials in `auth/firebase_app.py`, same `FIREBASE_SERVICE_ACCOUNT_*` variables as push).
  - App tokens may only call the read-only GETs listed in `_APP_READABLE_RULES` in `main.py` (plus `GET /api/app/me`). Everything else is admin-only by default (403 `forbidden`).
  - Error codes: 401 `app_auth_required` / `app_token_invalid` / `app_token_expired`; 403 `forbidden` / `email_not_verified`; 503 `auth_unavailable` (Firebase not configured — fails closed).
  - `APP_REQUIRE_VERIFIED_EMAIL=true` (default off) requires a verified email for app endpoints.
  - **No anonymous access.** `AUTH_REQUIRED` is retired and ignored (delete it from Railway). Anonymous `/api/*` calls always get 401 `app_auth_required`.
  - `POST /api/devices/register` and `/unregister` need a valid app token (an admin cookie does not satisfy them). The Firebase `uid` is stored on the device record; an account cannot unregister another account's token.
  - Website login (`/api/auth/login`) is throttled: 10 failed attempts per IP per 15 min → 429 (in-memory, resets on restart).
  - Railway variables to set: `SCANNER_ADMIN_USERS` (your admin username), `FLASK_SECRET_KEY` (64+ random chars), `SESSION_COOKIE_SECURE=true`.

## Persistent storage (optional Railway Volume)

Only these small files can live on a Railway Volume: `app_signals.json`, `app_learn.json`, `app_insights.json`, `app_weekly_report.json`, `app_exits.json`, `app_devices.json`, `watchlist.json`, `alert_log.json`.

- Directory is taken from `PERSISTENT_DATA_DIR`, else Railway's automatic `RAILWAY_VOLUME_MOUNT_PATH`. With neither set, files stay at their old relative paths (unchanged behaviour). If the directory is not writable, the backend logs a warning and falls back to local disk.
- First start on an empty volume: existing local copies of those files are copied in once (never overwriting what is already on the volume).
- Everything else — `scan_results/`, `logs/`, `backtest_state.json`, caches, `.fyers_token`, `static/uploads/` — stays on the normal disk.
- Code: `config/persistence.py`; paths wired in `config/settings.py`.

## Live entry detection and the publication workflow

**Nothing reaches app users automatically.** The system may *detect* things by itself and show them to the admin on the website; only an explicit, confirmed admin action publishes anything to the app or sends a phone notification.

### Two sides with a wall between them

- **Detection side (automatic, admin-only).** Once per trading day the daily history is downloaded and stored on the Volume (`data/history_store.py`). Every minute a *sweep* (`scanner/sweep.py`, about 10 Fyers requests through the shared pacer) reads the price list of all 500 stocks and adds today's live bar on top of the stored history. After each healthy live sweep, `scanner/entry_detect.py` checks (a) every armed admin signal's entry price and (b) the sweep's own scanner signals, and records each touch **once per day** in `entry_hits.json` (`data/entry_hits.py`). These modules never import the push modules or the app signals feed, and no app-facing endpoint reads the hit store.
- **Publication side (manual).** The admin presses buttons on the website: *Publish to app*, *Send notification*, *Send update notification*, *Publish entry reached*, exit calls, custom messages. Every notification goes through one server-side guard (`alerts/manual_push.py`): admin login, explicit `confirm`, duplicate window, daily cap, per-day rule for entry-reached, audit log (`push_audit_log.json`). The only place the two sides meet is the admin's button press, which reads a hit and copies a few facts onto a signal.

### Publication states

A signal is **Draft** (admin only), **Published** (in the app feed) or **Hidden** (deactivated). New signals start as Draft. Legacy records without a `published` field count as Published. The app feed is decided on the server from the admin login, never from a client parameter. Saving or editing never notifies. Editing a Published signal changes what users see immediately (the website warns). Changing a signal's entry price or stock withdraws its published "entry reached" facts; unpublishing does too.

### Where to look

`GET /api/system/overview` (admin, signed in on the website): Fyers calls per minute, mode, history store, sweep timing, armed count, last hit, push summary, signal counts, and the size of every file this work added. `GET /api/status` has the basic health.

### Settings (environment variables; all have safe defaults)

| Setting | Default | Purpose |
|---|---|---|
| `SWEEP_MODE` | off | `off` / `shadow` / `live`; detection needs `live` |
| `SWEEP_INTERVAL_SECONDS` | 60 | Sweep interval (raise to 120 if CPU is high) |
| `ENTRY_DETECTION_ENABLED` | false | Master switch for detection |
| `ENTRY_SCANNER_STOCKS_ENABLED` | true | Also record scanner-signal hits |
| `ENTRY_NOISE_UNTIL_TIME` | 09:15:30 | No detection before this IST time |
| `ENTRY_EXTENDED_PCT` | 0.5 | % past the level that marks a hit "extended" |
| `ENTRY_ARM_MAX_AGE_DAYS` | 10 | Admin signals are armed only this long |
| `ENTRY_STORE_RETENTION_DAYS` | 7 | Days of hits kept |
| `ENTRY_MAX_HITS_PER_DAY` | 200 | Cap on stored hits per day |
| `ENTRY_ADMIN_EMAIL_ENABLED` / `ENTRY_ADMIN_EMAIL_MAX_PER_DAY` | false / 20 | Optional e-mail to the admin on a hit |
| `ENTRY_EXACT_MINUTE_ENABLED` | false | One paced 1-minute call per admin hit for the exact minute |
| `ENTRY_STALE_MINUTES` | 15 | Age after which publishing a hit needs an extra confirmation |
| `PUSH_DAILY_MAX_MANUAL` | 30 | Daily cap on manual notifications |
| `PUSH_DUPLICATE_WINDOW_SECONDS` | 60 | Same type + stock within this window is refused |
| `PUSH_AUDIT_LOG_FILE` / `PUSH_AUDIT_LOG_MAX` | push_audit_log.json / 500 | Audit log file and entry cap |

There is deliberately **no** setting that turns automatic publishing or automatic push on.

### Rollback

Turn detection off with `ENTRY_DETECTION_ENABLED=false` (hits stop; everything else is unchanged). `SWEEP_MODE=off` returns to the hourly scan only. Do **not** roll the backend code back below the publication gate once drafts exist: older code would show drafts to app users and push automatically. First publish or deactivate every draft.

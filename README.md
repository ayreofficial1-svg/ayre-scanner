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

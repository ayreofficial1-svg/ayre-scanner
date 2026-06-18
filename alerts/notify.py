"""
alerts/notify.py
────────────────
Multi-layer alert system.

Fires all of the following simultaneously when a signal is generated:
  1. Terminal  — ASCII banner
  2. Sound     — cross-platform beep
  3. Desktop   — OS notification popup (via plyer, optional)
  4. Email     — HTML email (optional; configure via .env)

Signal payloads no longer carry entry/target/stop (those have been removed
from the scanner logic). Alerts now surface:
  - Symbol and close price
  - SMA44 value and distance
  - MACD cross type (confirmed / imminent)
  - How many bars ago the crossover occurred (confirmed only)
  - Day change %
"""

import platform
import subprocess
import threading
import smtplib
import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from config.settings import (
    ALERT_EMAIL_FROM, ALERT_EMAIL_TO, ALERT_EMAIL_PASS,
    ALERT_SMTP_HOST, ALERT_SMTP_PORT,
    RA_REGISTRATION_NUMBER, DISCLAIMER,
)

try:
    from plyer import notification as _plyer_notify
    _DESKTOP_NOTIFY = True
except ImportError:
    _DESKTOP_NOTIFY = False


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cross_age_label(crossover_bars_ago: int) -> str:
    if crossover_bars_ago == 0:
        return "today"
    if crossover_bars_ago == 1:
        return "yesterday"
    return f"{crossover_bars_ago} days ago"


# ── 1. Terminal alert ─────────────────────────────────────────────────────────

def _terminal_alert(
    symbol, close, sma44, change_pct, cross_type,
    crossover_bars_ago, source, now_str
):
    age = (
        f"  📅  Crossover   :  {_cross_age_label(crossover_bars_ago)}"
        if cross_type == "confirmed"
        else "  📅  Crossover   :  Imminent (not yet confirmed)"
    )
    print("\n" + "🚨" * 30)
    print(f"  ✅  SIGNAL:  {symbol}  [{source}]")
    print(f"  🕐  Time       :  {now_str}")
    print(f"  💰  Close      :  ₹{close:,.2f}")
    print(f"  📈  SMA44      :  ₹{sma44:,.2f}")
    print(f"  📊  Change     :  {change_pct:+.2f}%")
    print(f"  🔀  MACD Type  :  {cross_type}")
    print(age)
    print("🚨" * 30 + "\n")


# ── 2. Sound alert ────────────────────────────────────────────────────────────

def _sound_alert():
    try:
        system = platform.system()
        if system == "Windows":
            import winsound
            for _ in range(3):
                winsound.Beep(1000, 250)
        elif system == "Darwin":
            subprocess.run(
                ["afplay", "/System/Library/Sounds/Glass.aiff"],
                capture_output=True,
            )
        else:
            subprocess.run(
                ["paplay", "/usr/share/sounds/freedesktop/stereo/complete.oga"],
                capture_output=True,
            )
    except Exception:
        print("\a", end="", flush=True)


# ── 3. Desktop notification ───────────────────────────────────────────────────

def _desktop_alert(symbol, close, change_pct, cross_type, source):
    if not _DESKTOP_NOTIFY:
        return
    try:
        _plyer_notify.notify(
            title   = f"🚨 SIGNAL: {symbol}",
            message = (
                f"Close ₹{close:,.2f}  |  {change_pct:+.2f}%\n"
                f"MACD: {cross_type}"
                + (f"\n{source}" if source else "")
            ),
            timeout = 15,
        )
    except Exception:
        pass


# ── 4. Email alert ────────────────────────────────────────────────────────────

def _email_alert(
    symbol, close, sma44, change_pct,
    cross_type, crossover_bars_ago, source, now_str
):
    if not all([ALERT_EMAIL_FROM, ALERT_EMAIL_TO, ALERT_EMAIL_PASS]):
        return

    cross_detail = (
        f"Confirmed — {_cross_age_label(crossover_bars_ago)}"
        if cross_type == "confirmed"
        else "Imminent (converging, not yet crossed)"
    )
    source_html = f"<p><b>Note:</b> {source}</p>" if source else ""
    html_body = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:0 auto;
                background:#0b0f1a;color:#e2eaf6;padding:24px;border-radius:12px;">
      <div style="background:#00e5a0;color:#000;padding:12px 20px;
                  border-radius:8px;margin-bottom:20px;">
        <h2 style="margin:0;font-size:1.3rem;">📈 SIGNAL: {symbol}</h2>
        <p style="margin:4px 0 0;font-size:0.8rem;opacity:0.8;">{now_str}</p>
      </div>
      <table style="width:100%;border-collapse:collapse;font-size:0.9rem;">
        <tr><td style="padding:8px 0;color:#5a7090;">Close Price</td>
            <td style="padding:8px 0;color:#60a5fa;font-weight:700;text-align:right;">₹{close:,.2f}</td></tr>
        <tr><td style="padding:8px 0;color:#5a7090;">SMA44</td>
            <td style="padding:8px 0;color:#60a5fa;font-weight:700;text-align:right;">₹{sma44:,.2f}</td></tr>
        <tr><td style="padding:8px 0;color:#5a7090;">Day Change</td>
            <td style="padding:8px 0;color:#00e5a0;font-weight:700;text-align:right;">{change_pct:+.2f}%</td></tr>
        <tr><td style="padding:8px 0;color:#5a7090;">MACD Crossover</td>
            <td style="padding:8px 0;color:#f5c542;font-weight:700;text-align:right;">{cross_detail}</td></tr>
      </table>
      {source_html}
      <p style="margin-top:16px;font-size:0.7rem;color:#5a7090;">
        Conditions: Rising SMA44 · Close ≥ SMA44 · Bullish MACD Crossover<br>
        {DISCLAIMER}
      </p>
    </div>"""

    try:
        msg            = MIMEMultipart("alternative")
        msg["Subject"] = f"🚨 Signal: {symbol} @ ₹{close:.2f}"
        msg["From"]    = ALERT_EMAIL_FROM
        msg["To"]      = ALERT_EMAIL_TO
        msg.attach(MIMEText(html_body, "html"))
        with smtplib.SMTP(ALERT_SMTP_HOST, ALERT_SMTP_PORT) as server:
            server.starttls()
            server.login(ALERT_EMAIL_FROM, ALERT_EMAIL_PASS)
            server.sendmail(ALERT_EMAIL_FROM, ALERT_EMAIL_TO, msg.as_string())
        print(f"   📧  Email sent to {ALERT_EMAIL_TO}")
    except Exception as e:
        print(f"   ⚠️   Email failed: {e}")


# ── Public entry point ────────────────────────────────────────────────────────

def fire_alert(
    symbol             : str,
    close              : float,
    sma44              : float,
    change_pct         : float = 0.0,
    cross_type         : str   = "confirmed",
    crossover_bars_ago : int   = 0,
    source             : str   = "",
) -> None:
    """
    Fire all alert layers simultaneously.
    Sound, desktop, and email run in background threads.
    """
    now_str = datetime.datetime.now().strftime("%d %b %Y  %H:%M:%S")

    # Terminal is synchronous
    _terminal_alert(
        symbol, close, sma44, change_pct,
        cross_type, crossover_bars_ago, source, now_str,
    )

    # Everything else in background
    threading.Thread(target=_sound_alert, daemon=True).start()
    threading.Thread(
        target=_desktop_alert,
        args=(symbol, close, change_pct, cross_type, source),
        daemon=True,
    ).start()
    threading.Thread(
        target=_email_alert,
        args=(symbol, close, sma44, change_pct,
              cross_type, crossover_bars_ago, source, now_str),
        daemon=True,
    ).start()
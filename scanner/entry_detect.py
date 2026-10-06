"""
scanner/entry_detect.py
───────────────────────
Phase 5 — automatic entry detection, ADMIN-ONLY.

Runs once per healthy LIVE sweep (hooked from main._sweep_tick) and uses only
data the sweep already holds: the latest quote per stock (last, day high, day
low) and the sweep's own evaluation result. It adds NO Fyers requests, except
the optional one-minute "exact minute" call (ENTRY_EXACT_MINUTE_ENABLED,
through the shared pacer).

What counts as a hit
────────────────────
  Admin signal : the day's high (price must RISE to the entry) or low (price
                 must FALL to it) reaches the typed entry price. Direction is
                 fixed when the signal is armed. A touch that happened BEFORE
                 arming is never reported (baseline day high/low are stored).
                 Drafts are armed too, so the admin sees a touch before
                 deciding to publish.
  Scanner stock: the sweep's full evaluation (C1 + C2 + C3, which already
                 includes "day's low within the SMA44 buffer") says SIGNAL.
                 The sweep's result is REUSED — nothing is evaluated twice, so
                 there can never be two different answers. A stock that only
                 reaches the watchlist (C3 not crossed) records nothing.

One hit per stock per day (see data/entry_hits.py keys). Editing an admin
signal's entry price, or pressing "Re-arm", starts a new arm.

ONE-WAY WALL (Ground rule 10)
─────────────────────────────
This module and data/entry_hits.py import NOTHING from alerts.push,
alerts.manual_push or data.app_signals. The admin signals are handed in by
main.py through configure(signals_provider=...), read-only. Nothing here sends
a notification or changes what app users see.
"""

from __future__ import annotations

import datetime
import queue
import smtplib
import threading
import time
from email.mime.text import MIMEText

from config import settings as cfg
from data import entry_hits as store
from data import history_store

_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

_signals_provider = None
_lock = threading.Lock()
_status: dict = {
    "enabled": False, "last_sweep_used": None, "armed_admin": 0, "armed_scanner": 0,
    "admin_without_quote": [], "last_skip": None, "last_hit": None, "hits_today": 0,
    "sweeps_used": 0,
}
_log_at: dict[str, float] = {}


def configure(signals_provider) -> None:
    """main.py hands in a read-only callable returning the admin signal list."""
    global _signals_provider
    _signals_provider = signals_provider


def _log(key: str, text: str, every: float = 600.0) -> None:
    """Rate-limited log line (the sweep runs every minute)."""
    if time.time() - _log_at.get(key, 0.0) >= every:
        _log_at[key] = time.time()
        print(text)


def _parse_time(text: str, default: datetime.time) -> datetime.time:
    try:
        parts = [int(p) for p in str(text).split(":")]
        return datetime.time(parts[0], parts[1], parts[2] if len(parts) > 2 else 0)
    except Exception:
        return default


def _bare(sym: str) -> str:
    return sym.replace("NSE:", "").replace("-EQ", "")


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def status() -> dict:
    with _lock:
        out = dict(_status)
    out["enabled"] = bool(cfg.ENTRY_DETECTION_ENABLED)
    out["hits_today"] = store.hits_today_count()
    return out


def _skip(why: str) -> None:
    with _lock:
        _status["last_skip"] = why


# ── Admin signal eligibility ─────────────────────────────────────────────────
def _parse_iso(text) -> datetime.datetime | None:
    try:
        dt = datetime.datetime.fromisoformat(str(text))
        return dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        return None


def _eligible(sig: dict, now: datetime.datetime) -> bool:
    if not sig.get("enabled", sig.get("active", True)):
        return False
    entry = _f(sig.get("entry_price"))
    if entry is None or entry <= 0 or not sig.get("symbol"):
        return False
    added = str(sig.get("date_added") or "")[:10]
    try:
        age = (now.date() - datetime.date.fromisoformat(added)).days
        if age > cfg.ENTRY_ARM_MAX_AGE_DAYS:
            return False
    except ValueError:
        pass
    end = _parse_iso(sig.get("end_at"))
    if end is not None and end <= now.astimezone(datetime.timezone.utc):
        return False
    return True


# ── Admin e-mail (optional, admin address only) ──────────────────────────────
def _email_admin(subject: str, body: str) -> None:
    def _run():
        if not all([cfg.ALERT_EMAIL_FROM, cfg.ALERT_EMAIL_TO, cfg.ALERT_EMAIL_PASS]):
            return
        try:
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"], msg["From"], msg["To"] = subject, cfg.ALERT_EMAIL_FROM, cfg.ALERT_EMAIL_TO
            with smtplib.SMTP(cfg.ALERT_SMTP_HOST, cfg.ALERT_SMTP_PORT, timeout=20) as server:
                server.starttls()
                server.login(cfg.ALERT_EMAIL_FROM, cfg.ALERT_EMAIL_PASS)
                server.sendmail(cfg.ALERT_EMAIL_FROM, cfg.ALERT_EMAIL_TO, msg.as_string())
        except Exception as exc:
            print(f"   ⚠️   Entry hit e-mail failed: {exc}")
    threading.Thread(target=_run, daemon=True, name="entry-hit-email").start()


def _maybe_email(hit: dict) -> None:
    if not cfg.ENTRY_ADMIN_EMAIL_ENABLED:
        return
    day = store.today_ist()
    allowed = {"ok": False}

    def _do(state):
        d = state["meta"]["days"].setdefault(day, {})
        if int(d.get("emails", 0)) >= cfg.ENTRY_ADMIN_EMAIL_MAX_PER_DAY:
            return False
        d["emails"] = int(d.get("emails", 0)) + 1
        allowed["ok"] = True
        return True
    store.mutate(_do)
    if not allowed["ok"]:
        return
    kind = "Admin signal entry reached" if hit["kind"] == "admin" else "Scanner signal"
    _email_admin(
        f"Entry hit: {hit['symbol']} ({kind})",
        f"{kind}: {hit['symbol']}\n"
        f"Level: {hit.get('level')}  ({hit.get('direction')})\n"
        f"Price at detection: {hit.get('price_at_detection')}\n"
        f"Detected: {hit.get('detected_at')}\n"
        f"Extended past level: {'yes' if hit.get('extended') else 'no'}\n\n"
        "Open the website's admin area to review it. Nothing was sent to app users.",
    )


# ── Optional exact-minute correction (admin hits only; one paced call each) ──
_minute_q: "queue.Queue" = queue.Queue(maxsize=50)
_minute_thread: threading.Thread | None = None
_fyers_ref = {"client": None}


def _minute_worker() -> None:
    from data.candles import PACER, fetch_intraday_candles
    while True:
        hit_id, ticker, level, direction, since_iso = _minute_q.get()
        try:
            client = _fyers_ref["client"]
            if client is None:
                continue
            PACER.before(None)
            try:
                df = fetch_intraday_candles(client, ticker, datetime.datetime.now(_IST).date())
            finally:
                PACER.after()
            if df is None or df.empty:
                continue
            since = _parse_iso(since_iso)
            for ts, bar in df.iterrows():
                if since is not None and ts.to_pydatetime().replace(tzinfo=_IST) < since.astimezone(_IST) - datetime.timedelta(minutes=1):
                    continue
                touched = bar["High"] >= level if direction == "up" else bar["Low"] <= level
                if touched:
                    store.update_hit(hit_id, exact_minute=ts.strftime("%Y-%m-%dT%H:%M:00+05:30"))
                    break
        except Exception as exc:
            _log("minute_err", f"⚠️   Entry exact-minute lookup failed: {exc}")


def _queue_exact_minute(hit: dict, ticker: str, arm: dict) -> None:
    global _minute_thread
    if not cfg.ENTRY_EXACT_MINUTE_ENABLED or hit["kind"] != "admin":
        return
    if _minute_thread is None or not _minute_thread.is_alive():
        _minute_thread = threading.Thread(target=_minute_worker, daemon=True, name="entry-minute")
        _minute_thread.start()
    since = arm.get("armed_at") if arm.get("baseline_high") is not None or arm.get("baseline_low") is not None else None
    try:
        _minute_q.put_nowait((hit["id"], ticker, hit["level"], hit["direction"], since))
    except queue.Full:
        pass


# ── The per-sweep hook ───────────────────────────────────────────────────────
def process_sweep(out, fyers, now: datetime.datetime | None = None) -> None:
    """
    Called by main._sweep_tick after a healthy LIVE sweep has been published.
    Never raises, never sends or publishes anything.
    """
    try:
        _process(out, fyers, now)
    except Exception as exc:
        _log("proc_err", f"⚠️   Entry detection error (ignored): {type(exc).__name__}: {exc}", 300.0)


def _process(out, fyers, now) -> None:
    if not cfg.ENTRY_DETECTION_ENABLED:
        return
    from scanner.sweep import _quote_is_today          # same freshness rule as the sweep

    now = now or datetime.datetime.now(_IST)
    today = now.date()
    day = today.isoformat()
    if now.time() < _parse_time(cfg.ENTRY_NOISE_UNTIL_TIME, datetime.time(9, 15, 30)):
        _skip("before the opening-noise cut-off")
        return

    _fyers_ref["client"] = fyers
    rows = {_bare(sym): r for sym, r in (out.quote_rows or {}).items()}
    universe_sym = {_bare(sym): sym for sym in (out.quote_rows or {})}
    now_iso = now.isoformat(timespec="seconds")

    # First detection sweep of the day: remember when, so a late start is labelled.
    first = {}

    def _first(state):
        d = state["meta"]["days"].setdefault(day, {})
        if "first_sweep" not in d:
            d["first_sweep"] = now.strftime("%H:%M:%S")
            first["new"] = True
        first["time"] = d["first_sweep"]
        state["meta"]["last_sweep_at"] = now_iso
        return True if first.get("new") else False
    store.mutate(_first)
    first_t = _parse_time(first.get("time", "00:00:00"), datetime.time(0, 0))
    late_start = first_t > datetime.time(9, 20, 0)

    new_hits: list[tuple[dict, str, dict]] = []

    # ── Admin signals ────────────────────────────────────────────────────────
    signals = _signals_provider() if _signals_provider else []
    eligible = {s["id"]: s for s in signals if s.get("id") and _eligible(s, now)}
    no_quote: list[str] = []
    session_open = datetime.datetime.combine(today, datetime.time(9, 15), tzinfo=_IST)

    def _arms(state):
        arms = state["arms"]
        changed = False
        for sid in [k for k in arms if k not in eligible]:      # deactivated / deleted / aged out
            del arms[sid]
            changed = True
            _log(f"disarm{sid}", f"   🎯  Entry detection: disarmed signal {sid[:8]}", 60.0)

        for sid, sig in eligible.items():
            sym = str(sig["symbol"]).upper()
            entry = float(sig["entry_price"])
            row = rows.get(sym)
            fresh = row is not None and _quote_is_today(row, today)
            arm = arms.get(sid)

            if arm is None or abs(float(arm.get("entry", 0)) - entry) > 1e-9 or arm.get("symbol") != sym:
                seq = int((arm or {}).get("seq", 0)) + 1
                updated = _parse_iso(sig.get("updated_at") or sig.get("created_at"))
                pre_open = updated is not None and updated.astimezone(_IST) < session_open
                arm = {"symbol": sym, "entry": entry, "seq": seq, "day": None, "direction": None,
                       "baseline_high": None, "baseline_low": None, "done": False,
                       "armed_at": now_iso, "pre_open": pre_open}
                arms[sid] = arm
                changed = True
                print(f"   🎯  Entry detection: armed {sym} at {entry} "
                      f"({'edited/new' if seq > 1 else 'new'} arm #{seq})")
            elif arm.get("rearm"):
                arm.update({"rearm": False, "day": None, "direction": None, "pre_open": False,
                            "baseline_high": None, "baseline_low": None,
                            "seq": int(arm.get("seq", 1)) + 1, "armed_at": now_iso})
                changed = True
                print(f"   🎯  Entry detection: re-armed {sym} at {entry}")

            if not fresh:
                no_quote.append(sym)
                continue
            if arm.get("day") != day:
                # First quote of this arm today: fix direction and baseline.
                lp, prev = row["lp"], row.get("prev_close")
                if arm.get("pre_open") or arm.get("day") is not None:
                    # Armed before today's session: any touch today counts (gap-ups too).
                    ref = prev if prev else lp
                    arm["baseline_high"] = arm["baseline_low"] = None
                else:
                    # Armed during the session: only touches AFTER now count.
                    ref = lp
                    arm["baseline_high"], arm["baseline_low"] = row.get("high"), row.get("low")
                if ref is None or abs(ref - entry) < 1e-9:
                    arm["direction"] = None               # exactly at the level: wait for it to move away
                    arm["day"] = None
                else:
                    arm["direction"] = "up" if ref < entry else "down"
                    arm["day"] = day
                changed = True
        return changed
    store.mutate(_arms)

    arms_now = store.arms_snapshot()
    max_today = cfg.ENTRY_MAX_HITS_PER_DAY
    for sid, sig in eligible.items():
        arm = arms_now.get(sid)
        sym = str(sig["symbol"]).upper()
        row = rows.get(sym)
        if not arm or arm.get("done") or arm.get("direction") is None or arm.get("day") != day:
            continue
        if row is None or not _quote_is_today(row, today):
            continue
        entry, direction = float(arm["entry"]), arm["direction"]
        high, low, lp = row.get("high"), row.get("low"), row.get("lp")
        if direction == "up":
            touched = high is not None and high >= entry and (
                arm.get("baseline_high") is None or arm["baseline_high"] < entry or high > arm["baseline_high"])
            ext_pct = (lp - entry) / entry * 100.0 if lp else 0.0
        else:
            touched = low is not None and low <= entry and (
                arm.get("baseline_low") is None or arm["baseline_low"] > entry or low < arm["baseline_low"])
            ext_pct = (entry - lp) / entry * 100.0 if lp else 0.0
        if not touched:
            continue
        key = f"adm:{sid}:{entry:g}:{day}:{arm.get('seq', 1)}"
        if store.has_key(key):
            continue
        if store.hits_today_count() >= max_today:
            _log("maxhits", f"⚠️   Entry detection: daily limit of {max_today} hits reached — not recording more today.")
            continue
        hit = {
            "key": key, "day": day, "kind": "admin", "symbol": sym, "signal_id": sid,
            "level": entry, "direction": direction, "detected_at": now_iso,
            "price_at_detection": lp, "day_high": high, "day_low": low, "source": "sweep",
            "extended": ext_pct > cfg.ENTRY_EXTENDED_PCT, "extended_pct": round(max(ext_pct, 0.0), 2),
            "late_start": late_start,
        }
        rec = store.add_hit(hit)
        if rec:
            new_hits.append((rec, universe_sym.get(sym, sym), arm))

    # ── Scanner stocks (re-uses the sweep's own evaluation) ──────────────────
    armed_scanner = len(out.signals or []) + len(out.watchlist_items or [])
    if cfg.ENTRY_SCANNER_STOCKS_ENABLED:
        for d in out.signals or []:
            sym = str(d.get("symbol") or "").upper()
            if not sym:
                continue
            key = f"scn:{sym}:{day}"
            if store.has_key(key):
                continue
            if store.hits_today_count() >= max_today:
                _log("maxhits", f"⚠️   Entry detection: daily limit of {max_today} hits reached — not recording more today.")
                break
            row = rows.get(sym) or {}
            level = _f(d.get("sma44"))
            lp = row.get("lp") or _f(d.get("close"))
            ext_pct = ((lp - level) / level * 100.0) if (level and lp) else 0.0
            hit = {
                "key": key, "day": day, "kind": "scanner", "symbol": sym, "signal_id": None,
                "level": level, "direction": "touch", "detected_at": now_iso,
                "price_at_detection": lp, "day_high": row.get("high"), "day_low": row.get("low"),
                "source": "sweep", "extended": ext_pct > cfg.ENTRY_EXTENDED_PCT,
                "extended_pct": round(max(ext_pct, 0.0), 2), "late_start": late_start,
                "change_pct": d.get("change_pct"), "cross_type": d.get("cross_type"),
                "promoted": bool(d.get("promoted")),
            }
            if cfg.ENTRY_EXACT_MINUTE_ENABLED and d.get("trade_ready_at"):
                hit["exact_minute"] = d["trade_ready_at"]      # already reconstructed by the engine; free
            rec = store.add_hit(hit)
            if rec:
                new_hits.append((rec, universe_sym.get(sym, sym), {}))

    for rec, ticker, arm in new_hits:
        print(f"   🎯  ENTRY HIT ({rec['kind']}): {rec['symbol']} level {rec.get('level')} "
              f"price {rec.get('price_at_detection')}"
              f"{' [extended]' if rec.get('extended') else ''}{' [late start]' if rec.get('late_start') else ''} "
              "— admin only, nothing sent to the app")
        _maybe_email(rec)
        _queue_exact_minute(rec, history_store.get_resolved_symbol(ticker) or ticker, arm)

    armed_admin = sum(1 for a in store.arms_snapshot().values() if not a.get("done") and a.get("direction"))
    with _lock:
        _status.update({
            "last_sweep_used": now_iso, "armed_admin": armed_admin, "armed_scanner": armed_scanner,
            "admin_without_quote": sorted(set(no_quote))[:20], "last_skip": None,
            "sweeps_used": _status["sweeps_used"] + 1,
        })
        if new_hits:
            last = new_hits[-1][0]
            _status["last_hit"] = {"symbol": last["symbol"], "kind": last["kind"], "at": last["detected_at"]}

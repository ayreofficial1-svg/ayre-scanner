"""
scanner/sweep.py
────────────────
Layer 1 of the live-entry plan: the one-minute quote sweep of the whole universe.

Every sweep
───────────
  1. ~10 paced Fyers quote requests (50 symbols each) price every stock.  Each
     reply carries the day's open / high / low, last price and volume, so a touch
     between two sweeps is never missed.
  2. Today's candle is built from that quote and appended to the stored daily
     history (data/history_store.py, Phase 1).
  3. scanner.engine.run_scan() — the SAME evaluation, weekly filter, alerting,
     watchlist and trade-ready code as the hourly scan — evaluates the result.
     Only the candle download is replaced (run_scan's `candle_provider`).

The module holds no scanner conditions of its own.

Modes (config.settings.SWEEP_MODE): off | shadow | live — see settings.py.
Shadow runs the evaluation on COPIES with dry_run=True (no file written, no alert,
no intraday call) and compares it with every hourly scan.

Everything here is best-effort: any failure leaves the previous published result
in place and the hourly scan keeps working.
"""

from __future__ import annotations

import collections
import copy
import datetime
import json
import os
import threading
import time

import pandas as pd

from config import settings as cfg
from config.persistence import atomic_write_json
from data import history_store
from data.candles import AUTH_CODES, CATEGORY_FETCH_FAILED, PACER, _MIN_BARS, fyers_calls_last_minute
from scanner.engine import run_scan
from scanner.watchlist import (
    clean_alert_log, clean_watchlist, load_alert_log, load_watchlist,
    save_alert_log, save_watchlist,
)
from utils.scan_control import FyersAuthError, ScanCancelled

_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
_COLS = ["Open", "High", "Low", "Close", "Volume"]
_BATCH = 50


def _today() -> datetime.date:
    return datetime.datetime.now(_IST).date()


def _bare(sym: str) -> str:
    return sym.replace("NSE:", "").replace("-EQ", "")


def parse_hhmm(text: str, default: datetime.time) -> datetime.time:
    try:
        hh, mm = str(text).split(":", 1)
        return datetime.time(int(hh), int(mm))
    except Exception:
        return default


# ── Runtime state ────────────────────────────────────────────────────────────
class _Runtime:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.rows: dict[str, dict] = {}            # universe symbol -> latest quote row
        self.row_at: dict[str, float] = {}         # universe symbol -> monotonic time of that row
        self.rows_at: float = 0.0                  # monotonic time of the last full sweep
        self.last_ok_at: float = 0.0               # time.time() of the last good sweep
        self.last_ok_iso: str | None = None
        self.last_ok_day: datetime.date | None = None
        self.consecutive_failures = 0
        self.sweeps_ok = 0
        self.sweeps_failed = 0
        self.sweeps_skipped = 0
        self.last_duration = 0.0
        self.last_calls = 0
        self.last_error: str | None = None
        self.last_skip: str | None = None
        self.backoff_until = 0.0
        self.closing_done_day: datetime.date | None = None
        self.sweeps_today_day: datetime.date | None = None
        self.sweeps_today = 0
        self.last_saved_at = 0.0
        self.last_saved_membership: tuple | None = None
        self.last_summary: dict = {}
        self.shadow: collections.deque = collections.deque(maxlen=14)

    # ── health ───────────────────────────────────────────────────────────────
    def healthy(self) -> bool:
        """Fresh good sweep today and not failing repeatedly (mode-independent)."""
        with self.lock:
            return (
                self.last_ok_day == _today()
                and self.consecutive_failures < cfg.SWEEP_MAX_CONSECUTIVE_FAILURES
                and (time.time() - self.last_ok_at) <= cfg.SWEEP_HEALTHY_MAX_AGE_SECONDS
            )

    def serving_live(self) -> bool:
        """True when the published results come from the sweep (so the hourly scan can step aside)."""
        return cfg.SWEEP_MODE == "live" and self.healthy()

    def mark_ok(self, duration: float, calls: int, summary: dict) -> None:
        with self.lock:
            today = _today()
            self.last_ok_at = time.time()
            self.last_ok_iso = datetime.datetime.now(_IST).isoformat(timespec="seconds")
            self.last_ok_day = today
            self.consecutive_failures = 0
            self.sweeps_ok += 1
            if self.sweeps_today_day != today:
                self.sweeps_today_day, self.sweeps_today = today, 0
            self.sweeps_today += 1
            self.last_duration, self.last_calls = duration, calls
            self.last_error = None
            self.last_summary = summary

    def mark_failed(self, why: str) -> None:
        with self.lock:
            self.consecutive_failures += 1
            self.sweeps_failed += 1
            self.last_error = why[:300]

    def mark_skipped(self, why: str) -> None:
        with self.lock:
            self.sweeps_skipped += 1
            self.last_skip = why

    def health(self) -> dict:
        with self.lock:
            return {
                "mode": cfg.SWEEP_MODE,
                "interval_seconds": cfg.SWEEP_INTERVAL_SECONDS,
                "healthy": self.healthy(),
                "serving_live": self.serving_live(),
                "last_ok_at": self.last_ok_iso,
                "consecutive_failures": self.consecutive_failures,
                "sweeps_ok": self.sweeps_ok,
                "sweeps_failed": self.sweeps_failed,
                "sweeps_skipped": self.sweeps_skipped,
                "sweeps_today": self.sweeps_today if self.sweeps_today_day == _today() else 0,
                "last_duration_seconds": round(self.last_duration, 1),
                "last_sweep_calls": self.last_calls,
                "fyers_calls_last_minute": fyers_calls_last_minute(),
                "fyers_calls_per_minute_cap": cfg.FYERS_MAX_REQUESTS_PER_MINUTE,
                "backoff_seconds_left": max(0, int(self.backoff_until - time.time())),
                "last_error": self.last_error,
                "last_skip": self.last_skip,
                "last_summary": self.last_summary,
            }

    # ── reuse of sweep data by other features (0 extra Fyers calls) ──────────
    def _fresh_rows(self, max_age: float = 150.0) -> dict | None:
        with self.lock:
            if not self.serving_live() or not self.rows:
                return None
            if time.monotonic() - self.rows_at > max_age:
                return None
            return self.rows

    def latest_ltp(self, symbols: list[str]) -> dict[str, float | None] | None:
        """{symbol: last price} straight from the latest sweep, or None when it can't be trusted."""
        if not cfg.SWEEP_REUSE_FOR_QUOTES:
            return None
        rows = self._fresh_rows()
        if rows is None:
            return None
        with self.lock:
            out = {s: (rows.get(s) or {}).get("lp") for s in symbols}
        return out if any(v is not None for v in out.values()) else None

    def latest_breadth(self) -> dict | None:
        """Same shape as data.quotes.fetch_full_market_breadth, computed from the latest sweep."""
        if not cfg.SWEEP_REUSE_FOR_BREADTH:
            return None
        rows = self._fresh_rows()
        if rows is None:
            return None
        with self.lock:
            chps = [r["chp"] for r in rows.values() if r.get("chp") is not None]
        if not chps:
            return None
        adv = sum(1 for c in chps if c > 0)
        dec = sum(1 for c in chps if c < 0)
        return {
            "advances": adv, "declines": dec, "unchanged": len(chps) - adv - dec,
            "avg_change_pct": round(sum(round(c, 2) for c in chps) / len(chps), 2),
            "coverage": len(chps),
        }


RUNTIME = _Runtime()


# ── Preconditions ────────────────────────────────────────────────────────────
def store_ready(symbols: list[str]) -> tuple[bool, str]:
    """The sweep needs a valid, current history store covering the universe."""
    if not history_store.is_enabled():
        return False, "history store disabled"
    snap = history_store.get_snapshot()
    if snap is None or not snap.frames:
        return False, "no valid history store"
    if not history_store.is_current_for(_today()):
        return False, f"history store (as of {snap.as_of}) is not confirmed current for today"
    if not symbols or len(symbols) < 400:
        return False, "stock universe is incomplete"
    covered = sum(1 for s in set(symbols) if s in snap.frames)
    if covered < 0.9 * len(set(symbols)):
        return False, f"history store covers only {covered}/{len(set(symbols))} stocks"
    return True, ""


# ── Quotes ───────────────────────────────────────────────────────────────────
def _f(v) -> float | None:
    if v is None:
        return None
    try:
        x = float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def _parse_row(v: dict) -> dict:
    return {
        "lp": _f(v.get("lp", v.get("ltp"))),
        "open": _f(v.get("open_price", v.get("open"))),
        "high": _f(v.get("high_price", v.get("high"))),
        "low": _f(v.get("low_price", v.get("low"))),
        "volume": _f(v.get("volume", v.get("vol_traded_today"))),
        "chp": _f(v.get("chp")),
        "prev_close": _f(v.get("prev_close_price")),
        "tt": _f(v.get("tt")),
    }


class QuoteResult:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}       # universe symbol -> row (from THIS sweep)
        self.failed: set[str] = set()         # symbols whose batch failed
        self.calls = 0
        self.rate_limited = False
        self.errors: list[str] = []


def collect_quotes(fyers, symbols: list[str], resolved: dict[str, str], cancel=None) -> QuoteResult:
    """~10 paced quote requests.  Raises FyersAuthError / ScanCancelled."""
    res = QuoteResult()
    ticker_of = {s: (resolved.get(s) or s) for s in symbols}
    sym_of = {t: s for s, t in ticker_of.items()}
    tickers = list(dict.fromkeys(ticker_of.values()))
    batches = [tickers[i:i + _BATCH] for i in range(0, len(tickers), _BATCH)]
    auth_hits = 0
    for bi, batch in enumerate(batches):
        PACER.before(cancel)
        res.calls += 1
        try:
            resp = fyers.quotes(data={"symbols": ",".join(batch)})
        except Exception as exc:
            PACER.after()
            res.errors.append(f"batch {bi}: {type(exc).__name__}")
            res.failed.update(sym_of[t] for t in batch if t in sym_of)
            continue
        PACER.after()
        code = resp.get("code") if isinstance(resp, dict) else None
        msg = str((resp or {}).get("message", "") if isinstance(resp, dict) else "").lower()
        try:
            code_i = int(code) if code is not None else None
        except (TypeError, ValueError):
            code_i = None
        if code_i == 429 or code_i == -50 or "limit" in msg:
            PACER.rate_limited(2.0)
            res.rate_limited = True
            res.errors.append(f"batch {bi}: rate limit")
            for rest in batches[bi:]:
                res.failed.update(sym_of[t] for t in rest if t in sym_of)
            break
        if code_i in AUTH_CODES:
            auth_hits += 1
            res.failed.update(sym_of[t] for t in batch if t in sym_of)
            if auth_hits >= 2:
                raise FyersAuthError(f"Fyers rejected the session (code {code_i}: {msg[:80]})")
            continue
        if not isinstance(resp, dict) or resp.get("s") != "ok":
            res.errors.append(f"batch {bi}: bad response")
            res.failed.update(sym_of[t] for t in batch if t in sym_of)
            continue
        PACER.ok()
        got: set[str] = set()
        for item in resp.get("d", []) or []:
            if not isinstance(item, dict):
                continue
            t = str(item.get("n") or item.get("symbol") or "")
            v = item.get("v") if isinstance(item.get("v"), dict) else item
            if t in sym_of and isinstance(v, dict) and item.get("s", "ok") == "ok":
                res.rows[sym_of[t]] = _parse_row(v)
                got.add(t)
        # symbols the reply did not mention are simply "no quote today" — not a failure
    return res


# ── Candle building ──────────────────────────────────────────────────────────
def _quote_is_today(row: dict, today: datetime.date) -> bool:
    lp, o, h, l = row.get("lp"), row.get("open"), row.get("high"), row.get("low")
    if not lp or lp <= 0 or not o or o <= 0 or not h or h <= 0 or not l or l <= 0:
        return False
    vol = row.get("volume")
    if vol is not None and vol <= 0:
        return False
    tt = row.get("tt")
    if tt and tt > 1_000_000_000:
        try:
            day = datetime.datetime.fromtimestamp(tt, _IST).date()
            # tolerate a feed that already adds the +5:30 offset to the epoch
            day2 = datetime.datetime.fromtimestamp(tt - 19800, _IST).date()
            if today not in (day, day2):
                return False
        except (OverflowError, OSError, ValueError):
            pass
    return True


def build_candle_data(snap, symbols: list[str], rows: dict, failed: set[str], today: datetime.date):
    """
    (candle_data, fetch_report) in the same shape fetch_candles_bulk_persistent
    returns: candle_data[symbol] = stored history + today's bar from the quote.
    """
    unique = list(dict.fromkeys(symbols))
    candle_data: dict[str, pd.DataFrame] = {}
    ledger: dict[str, dict] = {}
    no_data: list[str] = []
    stale: list[str] = []
    failed_syms: list[str] = []
    short = 0
    ts_today = pd.Timestamp(today)
    for sym in unique:
        hist = snap.frames.get(sym)
        if hist is None:
            no_data.append(sym)
            ledger[_bare(sym)] = {
                "status": "no_data",
                "category": "Not analysed — not yet in the daily history store",
                "detail": "Added to the index after the last history download; included after the next one.",
            }
            continue
        row = rows.get(sym)
        if row is None and sym in failed:
            failed_syms.append(sym)
            ledger[_bare(sym)] = {
                "status": "fetch_failed", "code": None, "category": CATEGORY_FETCH_FAILED,
                "detail": "The live quote for this stock could not be fetched in this sweep (Fyers-side).",
            }
            continue
        df = hist
        if row is not None and _quote_is_today(row, today):
            o, h, l, c = row["open"], row["high"], row["low"], row["lp"]
            h, l = max(h, c, o), min(l, c, o)
            new = pd.DataFrame(
                [[o, h, l, c, row.get("volume") or 0.0]],
                index=pd.DatetimeIndex([ts_today], name="Timestamp"), columns=_COLS,
            )
            df = pd.concat([hist, new])
        last_bar = df.index[-1].date()
        if (today - last_bar).days > cfg.STALE_BAR_MAX_DAYS:
            stale.append(sym)
            ledger[_bare(sym)] = {
                "status": "stale_data", "last_bar": last_bar.isoformat(),
                "category": "Not analysed — price data is stale",
                "detail": (f"Newest candle is {last_bar.isoformat()}, more than {cfg.STALE_BAR_MAX_DAYS} days "
                           "before the scan date (suspended or halted). Not evaluated so old prices "
                           "cannot produce a false signal."),
            }
            continue
        candle_data[sym] = df
        if len(df) < _MIN_BARS:
            short += 1
            ledger[_bare(sym)] = {
                "status": "short_history", "bars": len(df),
                "category": "Not evaluated — not enough price history",
                "detail": f"Only {len(df)} daily candles exist (need at least {_MIN_BARS}); recently listed.",
            }
    report = {
        "attempted": len(unique),
        "duplicates_removed": len(symbols) - len(unique),
        "valid": len(candle_data),
        "no_data": len(no_data) + len(stale),
        "failed": len(failed_syms),
        "recovered": 0,
        "persistent_retries": 0,
        "persistent_recovered": 0,
        "missing": sorted(_bare(s) for s in no_data + stale + failed_syms),
        "failed_symbols": sorted(_bare(s) for s in failed_syms),
        "short_history": short,
        "stale": len(stale),
        "cached_no_data": 0,
        "ledger": ledger,
        "source": "sweep",
    }
    return candle_data, report


# ── One sweep ────────────────────────────────────────────────────────────────
class SweepOutcome:
    def __init__(self, ok: bool, why: str = "") -> None:
        self.ok = ok
        self.why = why
        self.signals: list[dict] = []
        self.watchlist_items: list[dict] = []
        self.fetch_report: dict = {}
        self.universe_stats: dict = {}
        self.calls = 0
        self.duration = 0.0
        self.rate_limited = False
        self.quote_rows: dict = {}


def run_sweep(fyers, symbols: list[str], *, publish: bool, cancel=None) -> SweepOutcome:
    """
    One full sweep.  publish=True evaluates with the real watchlist / alert log
    (alerts, files, trade-ready time exactly as a scan); publish=False evaluates
    copies with dry_run=True.  Raises FyersAuthError / ScanCancelled.
    """
    t0 = time.time()
    snap = history_store.get_snapshot()
    ok, why = store_ready(symbols)
    if not ok or snap is None:
        return SweepOutcome(False, why or "history store not ready")
    today = _today()

    resolved = {s: (history_store.get_resolved_symbol(s) or s) for s in dict.fromkeys(symbols)}
    q = collect_quotes(fyers, symbols, resolved, cancel)

    # Stocks whose batch failed re-use their previous quote for a short while.
    now_m = time.monotonic()
    with RUNTIME.lock:
        prev, prev_at = RUNTIME.rows, RUNTIME.row_at
        carried = 0
        for s in list(q.failed):
            r = prev.get(s)
            if r is not None and now_m - prev_at.get(s, 0.0) <= cfg.SWEEP_QUOTE_CARRY_SECONDS:
                q.rows[s] = r
                q.failed.discard(s)
                carried += 1
    fail_share = len(q.failed) / max(1, len(set(symbols)))
    out = SweepOutcome(True)
    out.calls, out.rate_limited = q.calls, q.rate_limited
    if q.rate_limited:
        out.ok, out.why = False, "Fyers rate limit"
        return out
    if fail_share > cfg.SWEEP_MAX_FAILED_SHARE or not q.rows:
        out.ok = False
        out.why = f"{len(q.failed)} of {len(set(symbols))} stocks had no quote ({'; '.join(q.errors[:3]) or 'no data'})"
        return out

    candle_data, report = build_candle_data(snap, symbols, q.rows, q.failed, today)

    if publish:
        watchlist = clean_watchlist(load_watchlist())
        alert_log = clean_alert_log(load_alert_log())
        save_watchlist(watchlist)
        save_alert_log(alert_log)
    else:
        try:
            watchlist = clean_watchlist(copy.deepcopy(load_watchlist()))
            alert_log = clean_alert_log(copy.deepcopy(load_alert_log()))
        except Exception as exc:
            out.ok, out.why = False, f"could not read watchlist/alert log ({exc})"
            return out

    signals, wl_items, fetch_report, universe_stats = run_scan(
        fyers=fyers, symbols=symbols, interval="D",
        watchlist=watchlist, alert_log=alert_log,
        progress=None, cancel=cancel,
        candle_provider=lambda: (candle_data, report),
        dry_run=not publish, quiet=True, yield_cpu=True,
    )
    fetch_report["carried_quotes"] = carried

    # Remember the quotes for reuse by breadth / signal prices (carried rows keep their age).
    with RUNTIME.lock:
        new_at = {s: (prev_at.get(s, now_m) if prev.get(s) is r else now_m) for s, r in q.rows.items()}
        RUNTIME.rows, RUNTIME.row_at, RUNTIME.rows_at = dict(q.rows), new_at, now_m

    out.signals, out.watchlist_items = signals, wl_items
    out.fetch_report, out.universe_stats = fetch_report, universe_stats
    out.duration = time.time() - t0
    out.quote_rows = q.rows
    return out


# ── Shadow comparison with the hourly scan ───────────────────────────────────
def remember_shadow(out: SweepOutcome) -> None:
    RUNTIME.shadow.append({
        "ts": time.time(),
        "iso": datetime.datetime.now(_IST).isoformat(timespec="seconds"),
        "signals": {d["symbol"]: _lite(d) for d in out.signals},
        "watchlist": {d["symbol"]: _lite(d) for d in out.watchlist_items},
        "evaluated": out.fetch_report.get("evaluated"),
    })


def _lite(d: dict) -> dict:
    return {k: d.get(k) for k in ("close", "sma44", "low_today", "cross_type", "change_pct", "macd", "macd_signal")}


def _read_log() -> list:
    try:
        with open(cfg.SWEEP_SHADOW_LOG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def shadow_log(limit: int = 50) -> list:
    return _read_log()[-limit:][::-1]


def compare_with_legacy(signals: list[dict], watchlist_items: list[dict], started_ts: float, finished_ts: float) -> dict | None:
    """
    Called after every hourly scan while SWEEP_MODE == 'shadow'.  Compares it with
    the shadow sweep closest to the middle of the scan and appends one entry to the
    bounded shadow log.  Never raises.
    """
    try:
        if cfg.SWEEP_MODE != "shadow":
            return None
        mid = (started_ts + finished_ts) / 2.0
        with RUNTIME.lock:
            cands = list(RUNTIME.shadow)
        if not cands:
            return None
        best = min(cands, key=lambda c: abs(c["ts"] - mid))
        if abs(best["ts"] - mid) > 600:
            return None
        l_sig = {d["symbol"]: _lite(d) for d in signals}
        l_wl = {d["symbol"]: _lite(d) for d in watchlist_items}
        s_sig, s_wl = best["signals"], best["watchlist"]
        only_l_sig, only_s_sig = sorted(set(l_sig) - set(s_sig)), sorted(set(s_sig) - set(l_sig))
        only_l_wl, only_s_wl = sorted(set(l_wl) - set(s_wl)), sorted(set(s_wl) - set(l_wl))
        details = {}
        for sym in (only_l_sig + only_s_sig + only_l_wl + only_s_wl)[:15]:
            details[sym] = {
                "legacy": l_sig.get(sym) or l_wl.get(sym),
                "sweep": s_sig.get(sym) or s_wl.get(sym),
            }
        entry = {
            "at": datetime.datetime.now(_IST).isoformat(timespec="seconds"),
            "legacy_scan_window_s": round(finished_ts - started_ts),
            "sweep_at": best["iso"],
            "sweep_offset_s": round(best["ts"] - mid),
            "legacy": {"signals": len(l_sig), "watchlist": len(l_wl)},
            "sweep": {"signals": len(s_sig), "watchlist": len(s_wl)},
            "signals_only_in_legacy": only_l_sig, "signals_only_in_sweep": only_s_sig,
            "watchlist_only_in_legacy": only_l_wl, "watchlist_only_in_sweep": only_s_wl,
            "match": not (only_l_sig or only_s_sig or only_l_wl or only_s_wl),
            "details": details,
        }
        log = _read_log()
        log.append(entry)
        atomic_write_json(cfg.SWEEP_SHADOW_LOG_FILE, log[-cfg.SWEEP_SHADOW_LOG_MAX:], indent=1)
        print(
            f"🔍  Sweep shadow vs hourly scan: signals {entry['legacy']['signals']}/{entry['sweep']['signals']} "
            f"(legacy/sweep), watchlist {entry['legacy']['watchlist']}/{entry['sweep']['watchlist']} — "
            + ("identical" if entry["match"] else
               f"differences: signals −{len(only_l_sig)}/+{len(only_s_sig)}, watchlist −{len(only_l_wl)}/+{len(only_s_wl)} "
               "(see /api/sweep/shadow)")
        )
        return entry
    except Exception as exc:
        print(f"⚠️   Sweep shadow comparison failed: {exc}")
        return None

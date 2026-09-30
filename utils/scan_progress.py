"""
utils/scan_progress.py
──────────────────────
Live progress for the scheduled/manual scan and for Backtest scans.

Why this exists
───────────────
The scan pipeline already knows, at every moment, how many symbols it has
processed, how many returned usable data, and how many are queued for retry —
that is exactly what the Railway log line

    📥  400/501 processed — 396 valid, 4 to retry …

prints. This module simply records the same counters in memory so the website
can read them.  It makes NO Fyers calls and no network calls of any kind:
the scan loops call a few cheap in-memory methods as they go, and the website
reads a snapshot through GET /api/scan/progress.

Two independent trackers exist because a scheduled scan and a backtest can be
running at the same time:

    LIVE_PROGRESS      — _do_scan()          (scheduled slots + manual Rescan)
    BACKTEST_PROGRESS  — _run_backtest_job() (Backtest page)

Symbol accounting
─────────────────
Every symbol is in exactly one state at any time:

    valid    — usable candles received
    retry    — failed on the first pass, queued for a retry
    failed   — still failing after a retry (transient / rate-limit)
    no_data  — Fyers has no history for it (expected for ~90 symbols)

So   valid + retry + failed + no_data == processed   (always).

While the scan is running, "to retry" = retry + failed (they are still being
worked on).  Once the scan finishes, anything left in `failed` is reported as
"not scanned" together with `no_data`.

Stages
──────
    fetch   — first pass over every symbol      (done = processed, of = total)
    retry   — re-fetching unresolved symbols    (done/of = symbols in this pass)
    analyse — evaluating the fetched candles    (done/of = symbols evaluated)

`percent` is the true completed fraction of the CURRENT stage — a counter
divided by a counter, never a timer or an estimate.

Robustness: every public method swallows its own exceptions. Progress
reporting must never be able to break, slow down or alter a scan.
"""

from __future__ import annotations

import datetime
import threading
import time

VALID = "valid"
RETRY = "retry"
FAILED = "failed"
NO_DATA = "no_data"

STAGE_LABELS = {
    "idle":    "Idle",
    "fetch":   "Fetching price data",
    "retry":   "Retrying unresolved stocks",
    "analyse": "Evaluating setups",
    "done":    "Scan complete",
    "error":   "Scan stopped",
}

_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


class ScanProgress:
    def __init__(self, kind: str):
        self.kind = kind                    # "live" | "backtest"
        self._lock = threading.RLock()
        self._run_id = 0
        self._reset_locked()

    # ── internal ─────────────────────────────────────────────────────────────
    def _reset_locked(self) -> None:
        self._active = False
        self._stage = "idle"
        self._total = 0
        self._states: dict[str, str] = {}
        self._counts = {VALID: 0, RETRY: 0, FAILED: 0, NO_DATA: 0}
        self._recovered = 0
        self._retry_total = 0
        self._retry_done = 0
        self._retry_pass = 0
        self._analyse_total = 0
        self._analyse_done = 0
        self._target_date: str | None = None
        self._started_at: float | None = None
        self._finished_at: float | None = None
        self._error: str | None = None

    def _set_state_locked(self, symbol: str, state: str) -> None:
        old = self._states.get(symbol)
        if old == state:
            return
        if old is not None:
            self._counts[old] -= 1
        self._states[symbol] = state
        self._counts[state] += 1

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self, total: int = 0, target_date: str | None = None) -> int:
        """Begin a new run. Returns its run id (pass it back to finish())."""
        try:
            with self._lock:
                self._reset_locked()
                self._run_id += 1
                self._active = True
                self._stage = "fetch"
                self._total = max(int(total or 0), 0)
                self._target_date = target_date
                self._started_at = time.time()
                return self._run_id
        except Exception:
            return 0

    def set_total(self, total: int) -> None:
        try:
            with self._lock:
                self._total = max(int(total or 0), 0)
        except Exception:
            pass

    def finish(self, error: str | None = None, run_id: int | None = None) -> None:
        """
        End the run. If `run_id` is given and a newer run has since started,
        this is a no-op so a late finish can never close a fresh run.
        """
        try:
            with self._lock:
                if not self._active:
                    return
                if run_id is not None and run_id != self._run_id:
                    return
                self._active = False
                self._finished_at = time.time()
                self._error = str(error) if error else None
                self._stage = "error" if error else "done"
        except Exception:
            pass

    # ── stage 1: first pass ──────────────────────────────────────────────────
    def fetch_result(self, symbol: str, ok: bool) -> None:
        """One symbol finished the first pass (ok = usable candles returned)."""
        try:
            with self._lock:
                self._stage = "fetch"
                self._set_state_locked(symbol, VALID if ok else RETRY)
        except Exception:
            pass

    # ── stage 2: retries ─────────────────────────────────────────────────────
    def begin_retry(self, count: int, retry_pass: int = 1) -> None:
        try:
            with self._lock:
                self._stage = "retry"
                self._retry_total = max(int(count or 0), 0)
                self._retry_done = 0
                self._retry_pass = int(retry_pass or 1)
        except Exception:
            pass

    def retry_result(self, symbol: str, outcome: str) -> None:
        """
        One symbol finished a retry.
        outcome: "valid" (recovered) | "no_data" | "failed"
        """
        try:
            with self._lock:
                state = outcome if outcome in (VALID, NO_DATA, FAILED) else FAILED
                if state == VALID and self._states.get(symbol) != VALID:
                    self._recovered += 1
                self._set_state_locked(symbol, state)
                self._retry_done += 1
        except Exception:
            pass

    # ── stage 3: evaluation ──────────────────────────────────────────────────
    def begin_analyse(self, total: int) -> None:
        try:
            with self._lock:
                self._stage = "analyse"
                self._analyse_total = max(int(total or 0), 0)
                self._analyse_done = 0
        except Exception:
            pass

    def analyse_done(self, done: int) -> None:
        try:
            with self._lock:
                self._analyse_done = min(max(int(done), 0), self._analyse_total or int(done))
        except Exception:
            pass

    # ── read side ────────────────────────────────────────────────────────────
    def snapshot(self) -> dict:
        try:
            with self._lock:
                return self._snapshot_locked()
        except Exception:
            return {"kind": self.kind, "active": False, "stage": "idle"}

    def _snapshot_locked(self) -> dict:
        c = self._counts
        active = self._active
        processed = len(self._states)

        if self._stage == "retry":
            stage_done, stage_total = self._retry_done, self._retry_total
        elif self._stage == "analyse":
            stage_done, stage_total = self._analyse_done, self._analyse_total
        elif self._stage in ("done", "error"):
            stage_done, stage_total = processed, self._total or processed
        else:
            stage_done, stage_total = processed, self._total

        if self._stage == "done":
            percent = 100.0
        elif stage_total > 0:
            percent = max(0.0, min(100.0, stage_done / stage_total * 100.0))
        else:
            percent = 0.0

        # While running, `failed` symbols are still being retried; once the
        # scan has finished they become "not scanned" alongside `no_data`.
        to_retry = (c[RETRY] + c[FAILED]) if active else 0
        not_scanned = c[NO_DATA] + (0 if active else c[FAILED])

        now = time.time()
        elapsed = 0.0
        if self._started_at:
            elapsed = (self._finished_at or now) - self._started_at

        def _iso(ts: float | None) -> str | None:
            return (
                datetime.datetime.fromtimestamp(ts, _IST).isoformat()
                if ts else None
            )

        return {
            "kind": self.kind,
            "run_id": self._run_id,
            "active": active,
            "stage": self._stage,
            "stage_label": STAGE_LABELS.get(self._stage, self._stage),
            "percent": round(percent, 1),
            "stage_done": stage_done,
            "stage_total": stage_total,
            "total": self._total,
            "processed": processed,
            "valid": c[VALID],
            "to_retry": to_retry,
            "no_data": c[NO_DATA],
            "failed": c[FAILED],
            "not_scanned": not_scanned,
            "recovered": self._recovered,
            "retry_pass": self._retry_pass,
            "analysed": self._analyse_done,
            "analyse_total": self._analyse_total,
            "target_date": self._target_date,
            "started_at": _iso(self._started_at),
            "finished_at": _iso(self._finished_at),
            "elapsed_seconds": round(elapsed, 1),
            "error": self._error,
        }


LIVE_PROGRESS = ScanProgress("live")
BACKTEST_PROGRESS = ScanProgress("backtest")

"""
data/history_store.py
─────────────────────
Daily candle history for the scanner universe, kept as ONE small on-disk
generation, downloaded once per trading day and held in memory.

Phase 1 of the live-entry plan.  Nothing here changes scanner behaviour: scans,
breadth, the quotes poller, the WebSocket and backtests do not read this store.

Layout (under <persistent data dir>/<HISTORY_STORE_DIR_NAME>, or ./<name> when
persistence is off — a loud log line says which):

    manifest.json              the ONLY pointer to the live generation
    gen_<id>/history.npz       all stocks: dates, OHLCV, Fyers ticker, universe
    staging_<id>/              a download in progress (never read by anyone)

Safety rules
────────────
* Exactly one generation is live.  A download goes to staging_<id>/, is
  validated, renamed to gen_<id>/, and only then does manifest.json (atomic
  write) point to it.  The previous generation is deleted AFTER the new one is
  published and loaded.  Peak disk use is two generations for a moment.
* Readers never touch files.  They use an immutable in-memory snapshot that is
  swapped by a single reference assignment, so replacing files can never break a
  scan that is running.
* A failed / cancelled / invalid download leaves the previous generation intact.
* Orphans (staging folders, generations the manifest does not name) are removed
  at startup and after every publish.
* NOT used by backtests: the store holds only the latest generation and cannot
  serve a past date.  Backtests keep their own fetch path
  (data/candles.fetch_candles_bulk_at_date).

What a stored frame is
──────────────────────
The same table a live fetch returns (naive-IST midnight DatetimeIndex named
"Timestamp"; columns Open, High, Low, Close, Volume as float64), but ONLY
completed sessions: bars dated today (IST) or later are cut, so a download made
during the session never stores a forming bar.  Phase 2 appends the live "today"
bar on top.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
import threading
import time

import numpy as np
import pandas as pd

from config.persistence import atomic_write_json, data_dir
from config.settings import (
    HISTORY_STORE_DIR_NAME,
    HISTORY_STORE_ENABLED,
    HISTORY_STORE_MIN_COVERAGE,
    HISTORY_STORE_MIN_FRESH_SHARE,
    HISTORY_STORE_MIN_FREE_MB,
    HISTORY_STORE_PROBE_SYMBOLS,
)
from data.candles import fetch_history_for_store, probe_latest_daily_bar
from utils.scan_control import ScanCancelled

_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]
_MANIFEST = "manifest.json"
_DATA_FILE = "history.npz"
_FORMAT_VERSION = 1
_MIN_MEDIAN_BARS = 150          # plausibility floor for the median stored row count


def _today_ist() -> datetime.date:
    return datetime.datetime.now(_IST).date()


def _now_iso() -> str:
    return datetime.datetime.now(_IST).isoformat(timespec="seconds")


def is_enabled() -> bool:
    return bool(HISTORY_STORE_ENABLED)


# ── Location ─────────────────────────────────────────────────────────────────
def store_dir() -> str:
    """Folder of the store.  Persistent volume when configured, else local disk."""
    base = data_dir()
    return os.path.join(base, HISTORY_STORE_DIR_NAME) if base else HISTORY_STORE_DIR_NAME


def _persistent() -> bool:
    return bool(data_dir())


# ── In-memory snapshot (immutable once published) ────────────────────────────
class HistorySnapshot:
    __slots__ = ("frames", "resolved", "universe", "as_of", "generation",
                 "created_at", "loaded_at", "file_bytes", "rows")

    def __init__(self, frames, resolved, universe, as_of, generation, created_at, file_bytes):
        self.frames: dict[str, pd.DataFrame] = frames
        self.resolved: dict[str, str] = resolved
        self.universe: list[str] = universe
        self.as_of: datetime.date = as_of
        self.generation: str = generation
        self.created_at: str = created_at
        self.loaded_at: str = _now_iso()
        self.file_bytes: int = file_bytes
        self.rows: int = sum(len(f) for f in frames.values())


_lock = threading.Lock()
_snapshot: HistorySnapshot | None = None
_job: dict = {
    "state": "idle",            # idle | running | ok | skipped | failed | cancelled
    "action": None,
    "message": "",
    "started_at": None,
    "finished_at": None,
    "probe_date": None,
}
_last_load_error: str | None = None
_confirmed_on: datetime.date | None = None   # day the daily job confirmed the store covers the newest session


def _previous_weekday(day: datetime.date) -> datetime.date:
    d = day - datetime.timedelta(days=1)
    while d.weekday() >= 5:
        d -= datetime.timedelta(days=1)
    return d


def is_current_for(day: datetime.date) -> bool:
    """
    True when the stored history holds every completed session before `day`:
    it ends on the previous weekday, or today's daily job probed Fyers and found
    nothing newer (holiday).  The one-minute sweep relies on this.
    """
    snap = _snapshot
    if snap is None or not snap.frames:
        return False
    return snap.as_of >= _previous_weekday(day) or _confirmed_on == day


def get_snapshot() -> HistorySnapshot | None:
    """The live snapshot (a reference — never mutate the frames; copy first)."""
    return _snapshot


def is_valid() -> bool:
    snap = _snapshot
    return bool(snap and snap.frames)


def get_history(symbol: str) -> pd.DataFrame | None:
    """A COPY of the stored daily frame for `symbol` (e.g. 'NSE:TCS-EQ'), or None."""
    snap = _snapshot
    if snap is None:
        return None
    df = snap.frames.get(symbol)
    return df.copy() if df is not None else None


def get_resolved_symbol(symbol: str) -> str | None:
    """The Fyers ticker that answered for `symbol` when the store was built."""
    snap = _snapshot
    if snap is None:
        return None
    return snap.resolved.get(symbol) or (symbol if symbol in snap.frames else None)


def get_symbols() -> list[str]:
    snap = _snapshot
    return list(snap.frames) if snap else []


def _set_job(**kw) -> None:
    with _lock:
        _job.update(kw)


def health() -> dict:
    """Small JSON-safe status block for /api/status."""
    snap = _snapshot
    with _lock:
        job = dict(_job)
    out = {
        "enabled": is_enabled(),
        "valid": bool(snap and snap.frames),
        "as_of": snap.as_of.isoformat() if snap else None,
        "age_days": (_today_ist() - snap.as_of).days if snap else None,
        "symbol_count": len(snap.frames) if snap else 0,
        "universe_count": len(snap.universe) if snap else 0,
        "rows": snap.rows if snap else 0,
        "generation": snap.generation if snap else None,
        "created_at": snap.created_at if snap else None,
        "loaded_at": snap.loaded_at if snap else None,
        "size_mb": round(snap.file_bytes / 1_048_576, 2) if snap else None,
        "folder": store_dir(),
        "persistent": _persistent(),
        "confirmed_current_on": _confirmed_on.isoformat() if _confirmed_on else None,
        "last_job": job,
    }
    if _last_load_error:
        out["load_error"] = _last_load_error
    return out


# ── File helpers ─────────────────────────────────────────────────────────────
def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_npz(path: str, frames: dict[str, pd.DataFrame], resolved: dict[str, str],
               universe: list[str]) -> None:
    syms = list(frames)
    offsets = [0]
    date_parts, value_parts = [], []
    for s in syms:
        df = frames[s]
        d = df.index.values.astype("datetime64[D]").astype("int64")
        v = df[_COLUMNS].to_numpy(dtype="float64")
        date_parts.append(d)
        value_parts.append(v)
        offsets.append(offsets[-1] + len(d))
    dates = np.concatenate(date_parts) if date_parts else np.zeros(0, dtype="int64")
    values = np.concatenate(value_parts) if value_parts else np.zeros((0, 5), dtype="float64")
    with open(path, "wb") as f:
        np.savez_compressed(
            f,
            sym=np.array(syms, dtype=str),
            resolved=np.array([resolved.get(s, "") for s in syms], dtype=str),
            universe=np.array(universe, dtype=str),
            offsets=np.array(offsets, dtype="int64"),
            dates=dates,
            values=values,
        )
        f.flush()
        os.fsync(f.fileno())


def _read_npz(path: str) -> tuple[dict[str, pd.DataFrame], dict[str, str], list[str]]:
    with np.load(path, allow_pickle=False) as z:
        syms = [str(x) for x in z["sym"]]
        res = [str(x) for x in z["resolved"]]
        universe = [str(x) for x in z["universe"]]
        offsets = z["offsets"]
        dates = z["dates"]
        values = z["values"]
    if len(offsets) != len(syms) + 1 or int(offsets[-1]) != len(dates) or len(values) != len(dates):
        raise ValueError("history file is internally inconsistent")
    frames: dict[str, pd.DataFrame] = {}
    resolved: dict[str, str] = {}
    for i, s in enumerate(syms):
        a, b = int(offsets[i]), int(offsets[i + 1])
        idx = pd.DatetimeIndex(dates[a:b].astype("datetime64[D]").astype("datetime64[ns]"),
                               name="Timestamp")
        frames[s] = pd.DataFrame(values[a:b].copy(), index=idx, columns=_COLUMNS)
        if res[i]:
            resolved[s] = res[i]
    return frames, resolved, universe


def _read_manifest(base: str) -> dict | None:
    try:
        with open(os.path.join(base, _MANIFEST), encoding="utf-8") as f:
            m = json.load(f)
        if isinstance(m, dict) and m.get("version") == _FORMAT_VERSION and m.get("dir") and m.get("as_of"):
            return m
    except (OSError, ValueError, TypeError):
        pass
    return None


def _remove_tree(path: str) -> None:
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


def cleanup_orphans(base: str | None = None) -> list[str]:
    """
    Delete staging folders and generations the manifest does not name.
    Generations are removed only when the manifest is valid, so a damaged
    manifest can never cost the only copy of the data.
    """
    base = base or store_dir()
    removed: list[str] = []
    if not os.path.isdir(base):
        return removed
    manifest = _read_manifest(base)
    keep = manifest["dir"] if manifest else None
    for name in os.listdir(base):
        path = os.path.join(base, name)
        if name.startswith("staging_") or name == _MANIFEST + ".tmp":
            if os.path.isdir(path):
                _remove_tree(path)
            else:
                try:
                    os.remove(path)
                except OSError:
                    pass
            removed.append(name)
        elif name.startswith("gen_") and os.path.isdir(path) and manifest and name != keep:
            _remove_tree(path)
            removed.append(name)
    return removed


# ── Loading ──────────────────────────────────────────────────────────────────
def load_from_disk() -> bool:
    """
    Load the generation named by the manifest into memory (checksum verified).
    No Fyers calls.  Returns True when a valid snapshot is now live.
    """
    global _snapshot, _last_load_error
    base = store_dir()
    manifest = _read_manifest(base)
    if manifest is None:
        _last_load_error = None if not os.path.exists(os.path.join(base, _MANIFEST)) else "manifest unreadable"
        return False
    path = os.path.join(base, manifest["dir"], manifest.get("file", _DATA_FILE))
    try:
        size = os.path.getsize(path)
        if size != int(manifest.get("bytes", -1)):
            raise ValueError(f"size {size} != manifest {manifest.get('bytes')}")
        if _sha256(path) != manifest.get("sha256"):
            raise ValueError("checksum mismatch")
        frames, resolved, universe = _read_npz(path)
        if not frames:
            raise ValueError("no stocks in file")
        as_of = datetime.date.fromisoformat(manifest["as_of"])
        snap = HistorySnapshot(frames, resolved, universe, as_of,
                               str(manifest.get("generation")), str(manifest.get("created_at")), size)
    except Exception as exc:
        _last_load_error = f"{type(exc).__name__}: {exc}"
        print(f"⚠️   History store: stored generation could not be loaded ({_last_load_error}).")
        return False
    _last_load_error = None
    _snapshot = snap
    return True


def load_on_startup() -> dict:
    """Clean orphans, then load the stored generation (no Fyers calls)."""
    base = store_dir()
    if not is_enabled():
        print("📦  History store: disabled (HISTORY_STORE_ENABLED=false).")
        return health()
    if _persistent():
        print(f"📦  History store folder: {base} (persistent volume)")
    else:
        print(f"⚠️   History store folder: {os.path.abspath(base)} — NO persistent volume is configured, "
              "so the store is lost on every redeploy and re-downloaded. "
              "Set PERSISTENT_DATA_DIR (or attach a Railway Volume).")
    try:
        os.makedirs(base, exist_ok=True)
        removed = cleanup_orphans(base)
        if removed:
            print(f"📦  History store: removed leftovers {removed}")
    except Exception as exc:
        print(f"⚠️   History store: could not prepare {base!r}: {exc}")
    if load_from_disk():
        s = _snapshot
        print(f"📦  History store loaded: {len(s.frames)} stocks, as of {s.as_of}, "
              f"{s.file_bytes / 1_048_576:.1f} MB (no Fyers calls).")
    else:
        print("📦  History store: nothing valid on disk yet — it will be built by the daily job.")
    return health()


# ── Validation ───────────────────────────────────────────────────────────────
def validate_frames(frames: dict[str, pd.DataFrame], universe: list[str],
                    expected_as_of: datetime.date | None) -> tuple[bool, list[str], dict]:
    """(ok, reasons, stats).  Reasons are human-readable and logged on failure."""
    reasons: list[str] = []
    n_uni = len(set(universe))
    n = len(frames)
    stats: dict = {"stored": n, "universe": n_uni}
    if n == 0:
        return False, ["no stock has any history"], stats

    bad = [s for s, f in frames.items()
           if f is None or f.empty or not list(f.columns) == _COLUMNS
           or not np.isfinite(f[_COLUMNS].to_numpy(dtype="float64")).all()]
    if bad:
        reasons.append(f"{len(bad)} stock(s) have empty/corrupt frames (e.g. {bad[:3]})")

    coverage = n / n_uni if n_uni else 0.0
    stats["coverage"] = round(coverage, 4)
    if coverage < HISTORY_STORE_MIN_COVERAGE:
        reasons.append(f"coverage {coverage:.1%} is below the minimum {HISTORY_STORE_MIN_COVERAGE:.0%}")

    lasts = {s: f.index[-1].date() for s, f in frames.items() if f is not None and not f.empty}
    if not lasts:
        return False, reasons + ["no last-bar dates"], stats
    as_of = max(lasts.values())
    stats["as_of"] = as_of.isoformat()
    if expected_as_of and as_of < expected_as_of:
        reasons.append(f"newest bar {as_of} is older than the expected session {expected_as_of}")
    fresh = sum(1 for d in lasts.values() if d == as_of)
    fresh_share = fresh / len(lasts)
    stats["fresh_share"] = round(fresh_share, 4)
    if fresh_share < HISTORY_STORE_MIN_FRESH_SHARE:
        reasons.append(f"only {fresh_share:.1%} of stocks end on {as_of} "
                       f"(minimum {HISTORY_STORE_MIN_FRESH_SHARE:.0%})")

    median_rows = int(np.median([len(f) for f in frames.values() if f is not None] or [0]))
    stats["median_rows"] = median_rows
    if median_rows < _MIN_MEDIAN_BARS:
        reasons.append(f"median history length {median_rows} bars is implausibly short")
    return (not reasons), reasons, stats


# ── Download + atomic publish ────────────────────────────────────────────────
def _free_mb(path: str) -> float:
    probe = path if os.path.isdir(path) else (os.path.dirname(os.path.abspath(path)) or ".")
    return shutil.disk_usage(probe).free / 1_048_576


def _probe_date(fyers, cancel) -> datetime.date | None:
    """ONE paced request (a second only if the first gives nothing): newest completed session."""
    for sym in HISTORY_STORE_PROBE_SYMBOLS:
        d = probe_latest_daily_bar(fyers, sym, cancel)
        if d is not None:
            return d
    return None


def _publish(base: str, frames, resolved, universe, as_of: datetime.date) -> HistorySnapshot:
    """Write → verify → rename → manifest → swap → clean.  Raises on any problem."""
    global _snapshot
    gen_id = f"{as_of.isoformat()}_{int(time.time())}"
    staging = os.path.join(base, f"staging_{gen_id}")
    final = os.path.join(base, f"gen_{gen_id}")
    os.makedirs(staging, exist_ok=True)
    try:
        fpath = os.path.join(staging, _DATA_FILE)
        _write_npz(fpath, frames, resolved, universe)
        size, digest = os.path.getsize(fpath), _sha256(fpath)
        # Read it back exactly as a restart would, before anyone depends on it.
        chk_frames, chk_resolved, chk_universe = _read_npz(fpath)
        if set(chk_frames) != set(frames) or sum(map(len, chk_frames.values())) != sum(map(len, frames.values())):
            raise ValueError("verification read-back does not match what was written")
        os.replace(staging, final)           # atomic directory rename (same filesystem)
    except Exception:
        _remove_tree(staging)
        raise

    created = _now_iso()
    manifest = {
        "version": _FORMAT_VERSION,
        "generation": gen_id,
        "dir": f"gen_{gen_id}",
        "file": _DATA_FILE,
        "as_of": as_of.isoformat(),
        "created_at": created,
        "symbol_count": len(chk_frames),
        "universe_count": len(set(universe)),
        "rows": int(sum(map(len, chk_frames.values()))),
        "bytes": size,
        "sha256": digest,
    }
    try:
        atomic_write_json(os.path.join(base, _MANIFEST), manifest, indent=2)
    except Exception:
        _remove_tree(final)
        raise
    snap = HistorySnapshot(chk_frames, chk_resolved, list(chk_universe), as_of, gen_id, created, size)
    _snapshot = snap                          # single reference swap — readers keep their old snapshot
    removed = cleanup_orphans(base)           # previous generation goes only now
    if removed:
        print(f"📦  History store: removed previous generation(s) {removed}")
    return snap


def _mark_confirmed() -> None:
    global _confirmed_on
    _confirmed_on = _today_ist()


def refresh_if_needed(fyers, symbols: list[str], *, cancel=None, force: bool = False,
                      universe_complete: bool = True) -> dict:
    """
    One run of the daily job.

      1. probe (1 request): newest completed session on a liquid instrument;
      2. if the store already covers it → skip (weekend / holiday / already done);
      3. else download the full history, validate, publish atomically.

    Returns {"action": ..., "message": ...}.  Raises ScanCancelled (Stop pressed /
    a scan is due) and FyersAuthError (dead session) for the caller to handle; the
    previous generation is untouched in every failure case.
    """
    if not is_enabled():
        return {"action": "disabled", "message": "history store disabled"}

    started = _now_iso()
    _set_job(state="running", action="probe", message="checking for a new completed session",
             started_at=started, finished_at=None)
    base = store_dir()
    try:
        snap = _snapshot
        probe = _probe_date(fyers, cancel)
        _set_job(probe_date=probe.isoformat() if probe else None)

        if not force and snap is not None:
            if probe is None:
                res = {"action": "skipped_probe_failed",
                       "message": "probe returned nothing; keeping the current store"}
                print(f"📦  History store: {res['message']} (as of {snap.as_of}).")
                _set_job(state="skipped", action=res["action"], message=res["message"],
                         finished_at=_now_iso())
                return res
            if probe <= snap.as_of:
                res = {"action": "skipped_up_to_date",
                       "message": f"store as of {snap.as_of} already covers the newest session {probe}"}
                print(f"📦  History store: {res['message']} — no download.")
                _mark_confirmed()
                _set_job(state="skipped", action=res["action"], message=res["message"],
                         finished_at=_now_iso())
                return res

        if not universe_complete or not symbols:
            res = {"action": "aborted_universe", "message": "stock universe is incomplete; not building a store from it"}
            print(f"⚠️   History store: {res['message']}.")
            _set_job(state="failed", action=res["action"], message=res["message"], finished_at=_now_iso())
            return res

        os.makedirs(base, exist_ok=True)
        free = _free_mb(base)
        if free < HISTORY_STORE_MIN_FREE_MB:
            res = {"action": "aborted_disk",
                   "message": f"only {free:.0f} MB free (< {HISTORY_STORE_MIN_FREE_MB:.0f} MB); previous store kept"}
            print(f"❌  History store: {res['message']}.")
            _set_job(state="failed", action=res["action"], message=res["message"], finished_at=_now_iso())
            return res

        today = _today_ist()
        print(f"📦  History store: new session {probe or 'unknown'} → downloading {len(symbols)} stocks "
              f"(~{2 * len(symbols)} paced Fyers requests) …")
        _set_job(action="download", message=f"downloading {len(symbols)} stocks")
        results, report = fetch_history_for_store(fyers, symbols, today, cancel=cancel)

        # Completed sessions only: never store today's (forming) bar.
        cutoff = pd.Timestamp(today)
        frames: dict[str, pd.DataFrame] = {}
        for sym, df in results.items():
            part = df[df.index < cutoff][_COLUMNS].astype("float64")
            part.index.name = "Timestamp"
            if not part.empty:
                frames[sym] = part
        resolved = {s: r for s, r in (report.get("resolved") or {}).items() if s in frames}

        ok, reasons, stats = validate_frames(frames, symbols, probe)
        if not ok:
            res = {"action": "rejected", "message": "; ".join(reasons), "stats": stats}
            print(f"❌  History store: downloaded data rejected — {res['message']}. Previous store kept.")
            _set_job(state="failed", action="rejected", message=res["message"], finished_at=_now_iso())
            return res

        as_of = datetime.date.fromisoformat(stats["as_of"])
        snap = _publish(base, frames, resolved, list(dict.fromkeys(symbols)), as_of)
        res = {"action": "downloaded",
               "message": f"published {len(snap.frames)} stocks as of {snap.as_of} ({snap.file_bytes / 1_048_576:.1f} MB)",
               "stats": stats, "fetch": {k: report.get(k) for k in ("attempted", "valid", "no_data", "failed")}}
        print(f"✅  History store: {res['message']}.")
        _mark_confirmed()
        _set_job(state="ok", action="downloaded", message=res["message"], finished_at=_now_iso())
        return res
    except ScanCancelled:
        _set_job(state="cancelled", action="cancelled",
                 message="download stopped; previous store kept", finished_at=_now_iso())
        print("🛑  History store: download stopped — previous store kept.")
        raise
    except Exception as exc:
        _set_job(state="failed", action="error", message=f"{type(exc).__name__}: {exc}"[:300],
                 finished_at=_now_iso())
        raise
    finally:
        # A crash anywhere above can leave a staging folder; never keep one.
        try:
            for name in os.listdir(base) if os.path.isdir(base) else []:
                if name.startswith("staging_"):
                    _remove_tree(os.path.join(base, name))
        except Exception:
            pass

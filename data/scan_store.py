"""
data/scan_store.py
──────────────────
Persistent saved scan results, one JSON file per scanned date:

    scan_results/backtest/2026-09-29.json
    scan_results/live/2026-09-29.json      (latest scheduled/manual scan of the day)

Reading or writing these files never touches Fyers.

File shape:
    {"date", "saved_at", "session_final", "partial", "payload": {...}}

session_final — the trading session was over when the scan ran, so the result
                is safe to reuse without rescanning.
partial       — some stocks could not be fetched from Fyers at that time; the
                UI tells the user and offers Rescan.
"""

from __future__ import annotations

import datetime
import json
import os
import threading

from config.settings import SCAN_RESULTS_DIR

_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
_lock = threading.RLock()


def _dir(kind: str) -> str:
    d = os.path.join(SCAN_RESULTS_DIR, kind)
    os.makedirs(d, exist_ok=True)
    return d


def _path(kind: str, day: str) -> str:
    safe = "".join(c for c in str(day) if c.isdigit() or c == "-")[:10]
    return os.path.join(_dir(kind), f"{safe}.json")


def session_is_final(day: str) -> bool:
    """True once the day's candle can no longer change (past day, or after 15:45 IST)."""
    now = datetime.datetime.now(_IST)
    today = now.date().isoformat()
    if day < today:
        return True
    if day == today:
        return (now.hour, now.minute) >= (15, 45)
    return False


def save_result(kind: str, day: str, payload: dict, partial: bool = False) -> None:
    doc = {
        "date": day,
        "saved_at": datetime.datetime.now(_IST).isoformat(),
        "session_final": session_is_final(day),
        "partial": bool(partial),
        "payload": payload,
    }
    with _lock:
        path = _path(kind, day)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        os.replace(tmp, path)
        idx = _read_index(kind)
        idx[doc["date"]] = {k: doc[k] for k in ("date", "saved_at", "session_final", "partial")}
        _write_index(kind, idx)


def load_result(kind: str, day: str) -> dict | None:
    with _lock:
        try:
            path = _path(kind, day)
            if not os.path.exists(path):
                return None
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
            return doc if isinstance(doc, dict) and isinstance(doc.get("payload"), dict) else None
        except Exception:
            return None


def _index_path(kind: str) -> str:
    return os.path.join(_dir(kind), "_index.json")


def _read_index(kind: str) -> dict:
    try:
        with open(_index_path(kind), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_index(kind: str, idx: dict) -> None:
    tmp = _index_path(kind) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(idx, f)
    os.replace(tmp, _index_path(kind))


def list_saved(kind: str) -> list[dict]:
    """Metadata only. Uses a small index file; rebuilds it from the files if missing."""
    with _lock:
        idx = _read_index(kind)
        files = {n[:-5] for n in os.listdir(_dir(kind)) if n.endswith(".json") and not n.startswith("_")}
        if set(idx) != files:
            idx = {d: m for d, m in idx.items() if d in files}
            for day in files - set(idx):
                doc = load_result(kind, day)
                if doc:
                    idx[day] = {k: doc.get(k) for k in ("date", "saved_at", "session_final", "partial")}
            _write_index(kind, idx)
        return sorted(idx.values(), key=lambda m: m.get("date") or "", reverse=True)

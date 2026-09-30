"""
data/symbol_cache.py
────────────────────
Remembers, per symbol, what Fyers told us last time so a scan does not repeat
the same futile calls.  It only ever REDUCES Fyers calls.

  resolved  — "NSE:X-EQ" actually lives at "NSE:X-BE" (used as a hint only;
              if the hint stops working the full suffix list is tried again).
  no_data   — every series (-EQ/-BE/-BZ/-SM/-ST) answered "invalid symbol".
              Only written/read by LIVE scans and expires after
              SYMBOL_NO_DATA_TTL_DAYS, so a symbol Fyers adds later is never
              skipped for long.
"""

from __future__ import annotations

import datetime
import json
import os
import threading

from config.settings import SYMBOL_CACHE_FILE, SYMBOL_NO_DATA_TTL_DAYS


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


class SymbolCache:
    def __init__(self, path: str = SYMBOL_CACHE_FILE, ttl_days: float = SYMBOL_NO_DATA_TTL_DAYS):
        self._path = path
        self._ttl = datetime.timedelta(days=ttl_days)
        self._lock = threading.RLock()
        self._loaded = False
        self._dirty = False
        self._resolved: dict[str, dict] = {}
        self._no_data: dict[str, dict] = {}

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            if os.path.exists(self._path):
                with open(self._path, encoding="utf-8") as f:
                    data = json.load(f)
                self._resolved = dict(data.get("resolved") or {})
                self._no_data = dict(data.get("no_data") or {})
        except Exception:
            self._resolved, self._no_data = {}, {}

    def hint(self, symbol: str) -> str | None:
        with self._lock:
            self._load()
            e = self._resolved.get(symbol)
            return e.get("symbol") if e else None

    def record_resolved(self, symbol: str, resolved: str) -> None:
        with self._lock:
            self._load()
            if resolved == symbol:
                if symbol in self._resolved:
                    del self._resolved[symbol]
                    self._dirty = True
            elif self._resolved.get(symbol, {}).get("symbol") != resolved:
                self._resolved[symbol] = {"symbol": resolved, "at": _now().isoformat()}
                self._dirty = True
            if symbol in self._no_data:
                del self._no_data[symbol]
                self._dirty = True

    def no_data_entry(self, symbol: str) -> dict | None:
        """Return the confirmed no-data entry if it is still within its TTL."""
        with self._lock:
            self._load()
            e = self._no_data.get(symbol)
            if not e:
                return None
            try:
                at = datetime.datetime.fromisoformat(e["at"])
            except Exception:
                return None
            return e if _now() - at < self._ttl else None

    def record_no_data(self, symbol: str, detail: str) -> None:
        with self._lock:
            self._load()
            self._no_data[symbol] = {"at": _now().isoformat(), "detail": detail}
            self._dirty = True

    def save(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            try:
                tmp = self._path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"resolved": self._resolved, "no_data": self._no_data}, f)
                os.replace(tmp, self._path)
                self._dirty = False
            except Exception:
                pass


SYMBOL_CACHE = SymbolCache()

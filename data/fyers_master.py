"""
data/fyers_master.py
────────────────────
Maps NSE symbols to the exact Fyers ticker using Fyers' own public symbol master.

Why
───
The scanner used to assume every Nifty 500 stock is "NSE:<SYMBOL>-EQ" and then
probe -BE/-BZ/-SM/-ST when Fyers answered "invalid symbol".  That costs up to
five requests per stock that is not on -EQ, and it can never find a stock whose
Fyers ticker was renamed.  The symbol master lists every ticker Fyers serves, so
the right one can be chosen up front with ZERO Fyers API calls (the master is a
static file on public.fyers.in, it is not an API request).

Safety rules
────────────
* Pure helper.  Any failure (download, parse, unknown layout) returns
  loaded=False and changes nothing — the fetcher then behaves exactly as before.
* The master never REMOVES a stock from the universe.  A stock missing from the
  master is still requested from Fyers (full suffix probing) and only excluded if
  Fyers itself says it has no data.
* The master file has no header row, so columns are detected from their content
  (ticker "NSE:XXX-EQ", ISIN "INE…") instead of by fixed position.
* A ticker with the SAME bare symbol as the NSE symbol always wins.  The ISIN is
  used only to rescue a renamed ticker (NSE symbol absent from the master but its
  ISIN present), so one stock can never be silently swapped for another.
"""

from __future__ import annotations

import csv
import datetime
import io
import os
import re
import threading

import requests

from config.settings import FYERS_MASTER_ENABLED, FYERS_MASTER_FILE, FYERS_MASTER_URL
from data.symbol_cache import SYMBOL_CACHE

_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
_TICKER_RE = re.compile(r"^NSE:[A-Z0-9&_\-]+-[A-Z0-9]{1,3}$")
_ISIN_RE = re.compile(r"^IN[A-Z0-9]{9}[0-9]$")
# Series in order of preference for a normal listed equity.
_SERIES_PREF = ["EQ", "BE", "BZ", "SM", "ST", "IL", "BL", "SO", "P1", "P2"]
_EXCLUDED_SERIES = {"INDEX", "GB", "GS", "TB", "N1", "N2", "N3", "N4", "N5", "N6", "N7", "N8", "N9"}

_lock = threading.Lock()
_parsed: dict | None = None        # {"tickers": set, "by_isin": dict, "rows": int, "source": str, "day": str}


def _today() -> str:
    return datetime.datetime.now(_IST).date().isoformat()


def _download() -> str | None:
    try:
        resp = requests.get(FYERS_MASTER_URL, timeout=40)
        resp.raise_for_status()
        text = resp.text
        if text.count("\n") < 1000:
            return None
        return text
    except Exception:
        return None


def _write_disk(text: str) -> None:
    try:
        tmp = FYERS_MASTER_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, FYERS_MASTER_FILE)
    except Exception:
        pass


def _read_disk() -> str | None:
    try:
        with open(FYERS_MASTER_FILE, encoding="utf-8") as f:
            text = f.read()
        return text if text.count("\n") >= 1000 else None
    except Exception:
        return None


def _parse(text: str) -> dict | None:
    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 1000:
        return None
    sample = rows[:400]
    width = max(len(r) for r in sample)
    ticker_col = isin_col = None
    best_t = best_i = 0
    for c in range(width):
        vals = [r[c].strip() for r in sample if len(r) > c]
        t_hits = sum(1 for v in vals if _TICKER_RE.match(v))
        i_hits = sum(1 for v in vals if _ISIN_RE.match(v))
        if t_hits > best_t:
            best_t, ticker_col = t_hits, c
        if i_hits > best_i:
            best_i, isin_col = i_hits, c
    if ticker_col is None or best_t < 50:
        return None

    tickers: set[str] = set()
    by_isin: dict[str, list[str]] = {}
    for r in rows:
        if len(r) <= ticker_col:
            continue
        t = r[ticker_col].strip()
        if not _TICKER_RE.match(t):
            continue
        if t.rsplit("-", 1)[1] in _EXCLUDED_SERIES:
            continue
        tickers.add(t)
        if isin_col is not None and len(r) > isin_col:
            isin = r[isin_col].strip()
            if _ISIN_RE.match(isin):
                by_isin.setdefault(isin, []).append(t)
    if len(tickers) < 500:
        return None
    return {"tickers": tickers, "by_isin": by_isin, "rows": len(rows)}


def _load(force: bool = False) -> tuple[dict | None, str]:
    """(parsed master | None, source) — source: download | memory | disk_cache | unavailable."""
    global _parsed
    with _lock:
        if _parsed and not force and _parsed.get("day") == _today():
            return _parsed, "memory"
        text = _download()
        source = "download"
        if text is not None:
            parsed = _parse(text)
            if parsed:
                _write_disk(text)
        else:
            parsed = None
        if not parsed:
            disk = _read_disk()
            if disk:
                parsed = _parse(disk)
                source = "disk_cache"
        if not parsed:
            return None, "unavailable"
        parsed["source"] = source
        parsed["day"] = _today() if source == "download" else (_parsed or {}).get("day", "")
        _parsed = parsed
        return parsed, source


def _rank(ticker: str) -> int:
    series = ticker.rsplit("-", 1)[1]
    return _SERIES_PREF.index(series) if series in _SERIES_PREF else len(_SERIES_PREF)


def _bare_of(ticker: str) -> str:
    return ticker[len("NSE:"):].rsplit("-", 1)[0]


def resolve(master: dict, bare: str, isin: str | None) -> str | None:
    """Best Fyers ticker for an NSE symbol, or None when the master does not list it."""
    tickers = master["tickers"]
    same_bare = [f"NSE:{bare}-{s}" for s in _SERIES_PREF if f"NSE:{bare}-{s}" in tickers]
    if same_bare:
        return same_bare[0]
    # Any other series Fyers uses for exactly this bare symbol.
    prefix = f"NSE:{bare}-"
    other = sorted((t for t in tickers if t.startswith(prefix) and "-" not in t[len(prefix):]), key=_rank)
    if other:
        return other[0]
    # Renamed ticker: same ISIN, different symbol.
    if isin:
        pool = sorted(master["by_isin"].get(isin, []), key=lambda t: (_rank(t), t))
        if pool:
            return pool[0]
    return None


def build_hints(symbol_isin: dict[str, str | None]) -> dict:
    """
    symbol_isin: {bare NSE symbol: ISIN or None} for the whole universe.

    Installs the mapping into SYMBOL_CACHE and returns a report dict for the
    universe meta / audit.  Never raises.
    """
    report: dict = {"enabled": bool(FYERS_MASTER_ENABLED), "loaded": False}
    universe_keys = {f"NSE:{b}-EQ" for b in symbol_isin}
    if not FYERS_MASTER_ENABLED:
        SYMBOL_CACHE.update_master(universe_keys, {}, set())
        return report
    try:
        master, source = _load()
        if not master:
            # Keep whatever mapping an earlier build installed; the fetcher also
            # still probes the series suffixes, so nothing is lost.
            report["source"] = "unavailable"
            return report

        hints: dict[str, str] = {}
        known: set[str] = set()
        not_listed: list[str] = []
        remapped: list[dict] = []
        for bare, isin in symbol_isin.items():
            key = f"NSE:{bare}-EQ"
            resolved = resolve(master, bare, isin)
            if resolved is None:
                not_listed.append(bare)
                continue
            known.add(key)
            if resolved != key:
                hints[key] = resolved
                remapped.append({"nse": bare, "fyers": resolved})

        SYMBOL_CACHE.update_master(universe_keys, hints, known)
        report.update({
            "loaded": True,
            "source": source,
            "master_rows": master["rows"],
            "universe": len(symbol_isin),
            "listed_on_fyers": len(known),
            "remapped": remapped,
            "not_in_master": sorted(not_listed),
        })
        return report
    except Exception as exc:                      # never let mapping break a scan
        report["error"] = f"{type(exc).__name__}: {exc}"
        return report

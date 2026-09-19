"""
data/universe_stats.py
───────────────────────
Persistence for the per-symbol ATR% / MACD-tilt / volume-surge stats
captured as a free byproduct of run_scan() (see scanner/engine.py and spec
§2). Written once per scan by main.py::_do_scan; read by the
/api/insights/volatility, /api/insights/momentum and
/api/insights/volume-surge endpoints.

Mirrors scanner/watchlist.py's plain load/save-JSON-file pattern. Persisted
to disk so a restart doesn't blank the Insights charts until the next scan
(up to 7×/day) refreshes them.

File shape
──────────
{
  "as_of": "18 Sep 2026 11:30:12",
  "stats": {
    "RELIANCE": {"atr_pct": 1.42, "macd_bullish": true,  "volume_surge": 1.8, "close": 1401.35},
    "TCS":      {"atr_pct": 0.91, "macd_bullish": false, "volume_surge": 0.6, "close": 3820.10},
    ...
  }
}
"""

import os
import json
import datetime

from config.settings import APP_UNIVERSE_STATS_FILE

# Fixed UTC+5:30 offset — mirrors main.py's _IST; no pytz/zoneinfo dependency.
_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def load_universe_stats() -> dict:
    """
    Returns {"as_of": str | None, "stats": dict}. Falls back to an empty,
    well-shaped dict if the file doesn't exist yet or is unreadable — this
    is a cache read, so it must never raise.
    """
    if os.path.exists(APP_UNIVERSE_STATS_FILE):
        try:
            with open(APP_UNIVERSE_STATS_FILE, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and isinstance(data.get("stats"), dict):
                    return {"as_of": data.get("as_of"), "stats": data["stats"]}
        except Exception:
            pass
    return {"as_of": None, "stats": {}}


def save_universe_stats(stats: dict) -> dict:
    """Persist `stats` (as returned by run_scan()) with a fresh timestamp."""
    data = {
        "as_of": datetime.datetime.now(_IST).strftime("%d %b %Y %H:%M:%S"),
        "stats": stats,
    }
    with open(APP_UNIVERSE_STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return data

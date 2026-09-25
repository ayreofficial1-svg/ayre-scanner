"""
data/app_sentiment.py
───────────────────────
Automatic market-sentiment score for the consumer app's Home tab gauge.

Phase 1 removed the manual hand-set write path (formerly `save_sentiment`,
wired to a website POST form). This module (Phase 2) replaces the other
half — the old `load_sentiment()`, which read a stored admin value from
`app_sentiment.json` — with a pure computation from the cached full
Nifty-500 breadth snapshot that `main.py::_breadth_loop` /
`_refresh_full_breadth` already collect six times a day for
`GET /api/breadth/full` (see `data/breadth.py::load_full_breadth`).

No new Fyers calls, no new polling loop: `main.py` passes in the same
in-memory `_breadth_full_cache` dict `/api/breadth/full` itself already
serves, so this stays a pure, fast, in-memory computation with no disk
read on every request.

Formula (`_score_from_breadth`)
────────────────────────────────
    score = round(100 * advances / (advances + declines))

`unchanged` is deliberately excluded from the denominator: a stock that
didn't move isn't evidence of either direction, so it shouldn't dilute
the ratio toward 50 — only advances vs. declines should decide it.

Why no extra "dead zone" math on top of the ratio: the Flutter app's own
bucketing (`_moodFor`, ~35/65 in `home_tab.dart`) already gives a 30-point
-wide Neutral band, so a mildly lopsided day doesn't reach a directional
Bullish/Bearish label on its own — only a genuinely lopsided one (>65% or
<35% advancing) does. Piling a second dead zone on top of that would just
duplicate a boundary the UI already enforces. Instead:

- A **coverage floor** (`COVERAGE_FLOOR`) refuses to score a snapshot
  built from too few of the ~500 symbols — better to show "no sentiment
  reading yet" than a number derived from a thin, unrepresentative
  sample.
- A **flat-session note** uses `avg_change_pct` (data the snapshot
  already carries — no new fetch) purely to annotate the `note` string
  when the average move is tiny, so a technically-advancing-but-barely
  day doesn't silently read as confidently bullish without context. This
  never changes the score itself, only the explanatory text.

No VIX, FII/DII flows, or options data is used — none of that is fetched
anywhere in this codebase today (confirmed by repo-wide search), so no
formula here depends on it.

No-snapshot / low-coverage behaviour (see B.4 in the implementation spec)
────────────────────────────────────────────────────────────────────────
`compute_sentiment()` returns `sentiment: None` (with `updated_at: None`,
`note: None`) rather than a fabricated placeholder when there is no
breadth snapshot yet (fresh install, before the first 09:45 IST refresh
of the day) or when `coverage` is under `COVERAGE_FLOOR`. This is a
deliberate choice, not an oversight: `Sentiment.tryParse` (Flutter,
`lib/services/market_models.dart`) already treats a missing/null score as
"no reading" and `_SentimentCard` already has a dedicated
"No sentiment reading yet" empty state for exactly this case (see
`home_tab.dart`) — reusing that existing null-safe path is simpler and
more honest than inventing a fabricated default score for a cold start.
The former `APP_SENTIMENT_DEFAULT` placeholder is retired along with the
old admin-value file; nothing else in the repo referenced either.
"""

# Minimum number of the ~500 Nifty-500 symbols a breadth snapshot must
# have usable advance/decline data for before its score is trusted. Below
# this, too much of the index is missing for the ratio to be
# representative, so compute_sentiment() returns sentiment: None instead.
COVERAGE_FLOOR = 300

# Below this |avg_change_pct| (percent), the session is treated as too
# flat for the ratio-based score to mean much on its own. This only
# changes the `note` text, never the score.
FLAT_SESSION_AVG_CHANGE_PCT = 0.1


def _score_from_breadth(breadth: dict) -> int | None:
    """
    Pure function: 0-100 sentiment score from one breadth snapshot dict
    (the shape data/breadth.py::load_full_breadth()/save_full_breadth()
    return — advances/declines/unchanged/avg_change_pct/coverage/as_of).
    Returns None if there's no usable advance/decline data at all.
    """
    advances = breadth.get("advances") or 0
    declines = breadth.get("declines") or 0
    total = advances + declines
    if total <= 0:
        return None
    return round(100 * advances / total)


def _note_from_breadth(breadth: dict) -> str:
    """
    A concrete, numeric one-liner the Flutter `_SentimentCard._describe()`
    prefers over its own computed fallback, e.g. "312 of 490 stocks
    advancing (flat session)".
    """
    advances = breadth.get("advances") or 0
    declines = breadth.get("declines") or 0
    unchanged = breadth.get("unchanged") or 0
    total = advances + declines + unchanged
    note = f"{advances} of {total} stocks advancing" if total else f"{advances} stocks advancing"

    avg_change = breadth.get("avg_change_pct")
    if avg_change is not None and abs(avg_change) < FLAT_SESSION_AVG_CHANGE_PCT:
        note += " (flat session)"
    return note


def compute_sentiment(breadth: dict | None) -> dict:
    """
    Builds the {sentiment, updated_at, note} core of GET /api/sentiment
    from a full-breadth snapshot dict — `main.py` passes in the cached
    `_breadth_full_cache` (same in-memory data `/api/breadth/full` serves),
    so this never touches disk or Fyers itself.

    Returns sentiment: None (with updated_at/note: None too) when there is
    no snapshot yet, or when its `coverage` is below COVERAGE_FLOOR — see
    the "No-snapshot / low-coverage behaviour" note in this module's
    docstring for why that's the chosen fallback.
    """
    if not breadth or (breadth.get("coverage") or 0) < COVERAGE_FLOOR:
        return {
            "sentiment" : None,
            "updated_at": None,
            "note"      : None,
        }

    score = _score_from_breadth(breadth)
    if score is None:
        return {
            "sentiment" : None,
            "updated_at": breadth.get("as_of"),
            "note"      : None,
        }

    return {
        "sentiment" : score,
        "updated_at": breadth.get("as_of"),
        "note"      : _note_from_breadth(breadth),
    }

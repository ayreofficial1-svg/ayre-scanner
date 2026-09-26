"""
data/app_weekly_report.py
──────────────────────────
Persistent store for the consumer app's "Weekly Report" section on the
Signals tab — a per-week list of stocks the admin has manually confirmed
were profitable, with the profit percentage and whether the target or the
stop loss was hit. Read by the Flutter app via GET /api/weekly-report;
written by the website admin panel via POST/DELETE.

This is admin-entered data, not computed. There is no code anywhere in
this repo that watches a fired signal afterwards to determine whether
price later hit a target or a stop loss (see
IMPLEMENTATION_SPEC_weekly_report_and_sentiment.md §A.4) — the owner enters
the week, the stocks, their profit %, and the outcome by hand on the
website, and this module just stores exactly that.

Entry schema (JSON list)
─────────────────────────
  [
    {
      "id"           : "c7e2...",           # uuid4 hex
      "week_start"   : "2026-09-06",        # ISO date
      "week_end"     : "2026-09-12",        # ISO date
      "stocks"       : [
        {
          "symbol"    : "RELIANCE",
          "profit_pct": 4.2,
          "outcome"   : "target",
          # ── Optional (Phase 6 — trade-card redesign). Every field below
          # is entirely optional and defaults to null/empty/true so a report
          # saved before Phase 6 keeps parsing and displaying exactly as it
          # always has; the Flutter app falls back to the pre-Phase-6 plain
          # layout whenever "pnl_amount" is absent. ─────────────────────────
          "name"                    : "Reliance Industries",
          "bullish"                 : true,
          "trade_label"             : "BUY SEP 2960 CE",
          "entry_price"             : 64.0,
          "exit_price"              : 60.0,
          "pnl_amount"              : -700.0,
          "date_of_recommendation"  : "2026-09-08",
          "exit_date"               : "2026-09-08",
          "duration_days"           : 1
        },
        { "symbol": "TCS",      "profit_pct": -1.8, "outcome": "stop_loss" }
      ],
      "enabled"      : true,                # false = hidden, soft-deleted
      "display_order": 0,
      "created_at"   : "2026-09-13T10:15:00+00:00",
      "updated_at"   : "2026-09-13T10:15:00+00:00"
    },
    ...
  ]

`week_start`/`week_end` are stored as real ISO dates, not a pre-formatted
display string ("6th September to 12th September") — that ordinal-day
format is a display concern, computed client-side by whichever surface is
showing it (website admin list, and the Flutter Signals tab in Phase 5),
so it can be redisplayed differently later without a data migration.

Each stock row's `outcome` is exactly `"target"` or `"stop_loss"` — no
other spelling — so the value means the same thing in the backend, the
website and the Flutter app. Enforcing that is the API route's job
(main.py's POST /api/weekly-report validates it and rejects anything
else with 400); this module's own per-row normalization is deliberately
forgiving on *read* (lower-cases/strips whatever was already stored),
since a stored value has already passed that validation once and a
defensive read path should not throw on old or hand-edited data.

`id` is the primary key (same convention every other resource in this
codebase uses) — a week is identified by its id, not by
`(week_start, week_end)`, so two reports could technically share a date
range if the admin makes a mistake; that's an admin-UI concern, not a
storage-layer one.

DELETE /api/weekly-report/<id> soft-deletes (sets enabled=False; does not
remove the entry) — same convention app_signals.py's DELETE uses. These
are historical performance records, and preserving them (just hidden from
the public GET) is more useful than losing them outright.
"""

import os
import json
import uuid
import datetime
from config.settings import APP_WEEKLY_REPORT_FILE

# The only two outcome spellings accepted anywhere in this feature.
VALID_OUTCOMES = {"target", "stop_loss"}


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_float_or_none(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int_or_none(value):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_stock(row: dict) -> dict:
    """
    Defensive, read-side normalization of one stock row. Does not enforce
    `outcome` to be a valid value (that's the write-side API route's job) —
    it only lower-cases/strips whatever is already stored so a hand-edited
    or older row doesn't crash the read path.

    Every field from "name" onward is optional (Phase 6 — trade-card
    redesign, see this module's docstring): a row that never had them (any
    report saved before Phase 6) normalizes to null/empty/true here, which
    is exactly the "not provided" state the Flutter app's own parser
    already treats as "fall back to the plain pre-Phase-6 layout".
    """
    return {
        "symbol"                : str(row.get("symbol") or "").strip().upper(),
        "profit_pct"            : _to_float(row.get("profit_pct")),
        "outcome"               : str(row.get("outcome") or "").strip().lower(),
        "name"                  : str(row.get("name") or "").strip(),
        "bullish"               : bool(row.get("bullish", True)),
        "trade_label"           : str(row.get("trade_label") or "").strip(),
        "entry_price"           : _to_float_or_none(row.get("entry_price")),
        "exit_price"            : _to_float_or_none(row.get("exit_price")),
        "pnl_amount"            : _to_float_or_none(row.get("pnl_amount")),
        "date_of_recommendation": str(row.get("date_of_recommendation") or "").strip(),
        "exit_date"             : str(row.get("exit_date") or "").strip(),
        "duration_days"         : _to_int_or_none(row.get("duration_days")),
    }


def _normalize_report(entry: dict) -> dict:
    raw_stocks = entry.get("stocks")
    stocks = (
        [_normalize_stock(s) for s in raw_stocks if isinstance(s, dict)]
        if isinstance(raw_stocks, list)
        else []
    )
    return {
        **entry,
        "week_start"   : str(entry.get("week_start") or "").strip(),
        "week_end"     : str(entry.get("week_end") or "").strip(),
        "stocks"       : stocks,
        "enabled"      : bool(entry.get("enabled", True)),
        "display_order": int(entry.get("display_order") or 0),
    }


def _sort_key(entry: dict) -> tuple[str, int, str]:
    # Newest week first, matching /api/learn and /api/insights' "newest
    # first" convention. `display_order` (from _content_fields, same as
    # every other admin-curated list) breaks ties within an equal
    # week_start; falls back to updated_at as the final tiebreaker.
    return (
        str(entry.get("week_start") or ""),
        int(entry.get("display_order") or 0),
        str(entry.get("updated_at") or entry.get("created_at") or ""),
    )


def load_reports(enabled_only: bool = False) -> list[dict]:
    """
    Load all weekly reports, newest week first. Pass enabled_only=True to
    filter out soft-deleted/hidden ones (this is what GET /api/weekly-report
    uses for the consumer app; the website's own list passes ?all=1 to see
    everything, including hidden reports, so it can still edit/restore them).
    """
    reports: list[dict] = []
    if os.path.exists(APP_WEEKLY_REPORT_FILE):
        try:
            with open(APP_WEEKLY_REPORT_FILE, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    reports = data
        except Exception:
            pass

    normalized = [_normalize_report(r) for r in reports if isinstance(r, dict)]
    if enabled_only:
        normalized = [r for r in normalized if r.get("enabled", True)]
    # Newest week first -> sort ascending by the key above, then reverse.
    return sorted(normalized, key=_sort_key, reverse=True)


def get_report(report_id: str, enabled_only: bool = False) -> dict | None:
    """Look up a single report by id. Returns None if not found (or, when
    enabled_only=True, if it exists but is hidden/soft-deleted)."""
    for r in load_reports():
        if r.get("id") == report_id:
            if enabled_only and not r.get("enabled", True):
                return None
            return r
    return None


def save_reports(reports: list[dict]) -> None:
    with open(APP_WEEKLY_REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(reports, f, indent=2)


def add_report(week_start: str, week_end: str, stocks: list[dict], **fields) -> dict:
    """
    Create a new weekly report. `stocks` is the full row list for the week
    (already validated by the API route) — there is no incremental
    "add one stock to an existing week" operation; POST always replaces the
    whole `stocks` list, same as app_signals.py::save_signals persisting
    the whole list at once.
    """
    now = _now_iso()
    entry = _normalize_report({
        "id"        : uuid.uuid4().hex,
        "week_start": str(week_start).strip(),
        "week_end"  : str(week_end).strip(),
        "stocks"    : stocks,
        "enabled"   : bool(fields.get("enabled", True)),
        "created_at": now,
        "updated_at": now,
        **fields,
    })
    reports = load_reports()
    reports.insert(0, entry)
    save_reports(reports)
    return entry


def update_report(
    report_id: str,
    week_start: str | None,
    week_end: str | None,
    stocks: list[dict] | None,
    **fields,
) -> dict | None:
    """Edit an existing report in place. Returns the updated entry, or None
    if not found. Any of week_start/week_end/stocks left as None is kept
    unchanged; passing `stocks` replaces the whole list (see add_report)."""
    reports = load_reports()
    for idx, r in enumerate(reports):
        if r.get("id") == report_id:
            if week_start is not None:
                r["week_start"] = str(week_start).strip()
            if week_end is not None:
                r["week_end"] = str(week_end).strip()
            if stocks is not None:
                r["stocks"] = stocks
            for key in ("enabled", "display_order"):
                if key in fields:
                    r[key] = fields[key]
            r["updated_at"] = _now_iso()
            r.setdefault("created_at", r["updated_at"])
            r = _normalize_report(r)
            reports[idx] = r
            save_reports(reports)
            return r
    return None


def delete_report(report_id: str) -> bool:
    """
    Soft-delete: sets enabled=False rather than removing the entry, so past
    weekly performance records stay in history (same convention
    app_signals.py::delete_signal uses, applied via the same update path
    app_learn.py::delete_article uses) — GET /api/weekly-report (without
    ?all=1) simply stops returning it. Returns True if a matching report
    was found (whether or not it was already hidden); False if no such
    report exists at all.
    """
    return update_report(report_id, None, None, None, enabled=False) is not None

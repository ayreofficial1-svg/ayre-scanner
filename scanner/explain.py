"""
scanner/explain.py
──────────────────
Plain-English explanations for backtest results.

`build_explanation()` turns the numbers already computed by
scanner.debug_evaluate.evaluate_debug() into a short, specific explanation
of WHY a stock landed in its category. It only reads values — it never
changes any pass/fail decision.

Explanations are deliberately concise: one or two short sentences per check,
each carrying the real numbers that decided it. The website highlights those
technical figures (₹ prices, percentages, MACD values, SMA44, day counts) in
brighter text, so keep them in plain "₹1,234.50" / "2.10%" / "0.1234" form.

Returns:
    {"category": "<short plain label>", "explanation": "<short paragraph>"}
"""

from __future__ import annotations

from config.settings import (
    IMMINENT_GAP_THRESHOLD,
    IMMINENT_HIST_MIN,
    MACD_CROSSOVER_LOOKBACK,
    MIN_BARS,
    SMA44_SUPPORT_BUFFER_PCT,
    SMA_SLOPE_LOOKBACK,
)

_BUFFER_PCT = round(SMA44_SUPPORT_BUFFER_PCT * 100, 2)
_IMMINENT_GAP_PCT = round(IMMINENT_GAP_THRESHOLD * 100, 2)

CATEGORY_LABELS = {
    "trade_ready_confirmed": "Trade Ready — momentum signal just fired",
    "trade_ready_imminent": "Trade Ready — momentum signal about to fire",
    "watchlist": "Watchlist — good setup, momentum not ready yet",
    "preflight": "Skipped — not enough price history",
    "no_data": "Not analysed — no price data received",
    "c1_sma_rising": "Rejected — long-term trend is not rising",
    "c1_slope": "Rejected — trend too weak, too steep or fading",
    "c2_close_vs_sma": "Rejected — price is not at its support line",
    "c3_macd": "Rejected — the momentum entry signal is not fresh",
}


# ── small formatting helpers ─────────────────────────────────────────────────
def _num(v, d: int = 2) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "n/a"
    if f != f:
        return "n/a"
    return f"{f:,.{d}f}"


def _signed_pct(v, d: int = 2) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "n/a"
    if f != f:
        return "n/a"
    return f"{f:+.{d}f}%"


def _abs_pct(v, d: int = 2) -> str:
    try:
        f = abs(float(v))
    except (TypeError, ValueError):
        return "n/a"
    return f"{f:.{d}f}%"


def _rs(v, d: int = 2) -> str:
    s = _num(v, d)
    return s if s == "n/a" else f"₹{s}"


def _bars(n) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "some"
    return f"{n} day" if n == 1 else f"{n} days"


def _age(bars_ago) -> str:
    if bars_ago in (0, None):
        return "today"
    if bars_ago == 1:
        return "yesterday"
    return f"{bars_ago} trading days ago"


# ── reusable building blocks ─────────────────────────────────────────────────
def _trend_ok_sentence(d: dict) -> str:
    """Trend check passed, with the real numbers."""
    sma = f"44-day average (SMA44) {_rs(d.get('sma44_today'))}"
    if d.get("sma44_lookback") is not None and d.get("pct_slope") is not None:
        sma += (
            f" vs {_rs(d.get('sma44_lookback'))} "
            f"{_bars(d.get('sma_slope_lookback', SMA_SLOPE_LOOKBACK))} ago "
            f"({_signed_pct(d.get('pct_slope'))})"
        )
    ma_type = d.get("ma_type")
    if ma_type == "type1":
        return f"{sma} — clearly rising."
    if ma_type == "type2":
        return f"{sma} — flat or just turning up (base-building, accepted)."
    return f"{sma} — passed the trend checks."


def _support_ok_sentence(d: dict) -> str:
    """Price is at its support line, with the real numbers."""
    low_dist = d.get("low_vs_sma44_pct")
    try:
        low_dist_f = float(low_dist)
    except (TypeError, ValueError):
        low_dist_f = None

    if d.get("price_interaction_type") == "crossover" and low_dist_f is not None and low_dist_f < 0:
        s = (
            f"dipped to {_rs(d.get('low_today'))} ({_abs_pct(low_dist_f)} below SMA44), "
            f"then closed {_rs(d.get('close'))}, {_abs_pct(d.get('close_vs_sma_pct'))} above — "
            "buyers defended the line."
        )
    else:
        where = "above" if (low_dist_f is None or low_dist_f >= 0) else "below"
        s = (
            f"low {_rs(d.get('low_today'))} came within {_abs_pct(low_dist_f)} {where} SMA44 "
            f"(limit {_BUFFER_PCT}%); closed {_rs(d.get('close'))}, "
            f"{_abs_pct(d.get('close_vs_sma_pct'))} above it."
        )
    if d.get("is_double_bottom"):
        s += " It also held this line within the last 20 days."
    return s


def _weekly_sentence(d: dict) -> str:
    w = d.get("weekly_rising")
    if w is True:
        return "Weekly SMA44 is also rising."
    if w is False:
        return "Caution: weekly SMA44 is not rising (shown for your judgement)."
    return "Weekly trend unavailable (too little weekly history)."


def _prior_checks_passed(d: dict, up_to: str) -> str:
    """Which earlier checks a rejected stock cleared."""
    cleared = []
    if up_to in ("c1_slope", "c2_close_vs_sma", "c3_macd"):
        cleared.append("trend up (C1a)")
    if up_to in ("c2_close_vs_sma", "c3_macd"):
        cleared.append("trend size and steadiness (C1b/C1c)")
    if up_to == "c3_macd":
        cleared.append("price at support (C2)")
    if not cleared:
        return ""
    return "Passed: " + ", ".join(cleared) + ". "


# ── main entry point ─────────────────────────────────────────────────────────
def build_explanation(status: str, stage: str, d: dict, payload: dict | None = None) -> dict:
    """
    status  : 'signal' | 'watchlist' | 'none'
    stage   : stage string from evaluate_debug
    d       : the merged display-values dict (same one returned as result['values'])
    payload : evaluate() payload (has cross_type / crossover_bars_ago) — optional
    """
    payload = payload or {}
    cross_type = payload.get("cross_type", "")

    # ── Trade Ready: confirmed crossover ─────────────────────────────────────
    if status == "signal" and cross_type == "confirmed":
        age = _age(payload.get("crossover_bars_ago", d.get("crossover_found_bars_ago")))
        text = (
            "Passed all three tests. "
            f"Trend: {_trend_ok_sentence(d)} "
            f"Support: {_support_ok_sentence(d)} "
            f"Momentum: MACD crossed above its signal line {age} "
            f"(MACD {_num(d.get('macd_cur'), 4)} vs signal {_num(d.get('signal_cur'), 4)}) — the buy trigger. "
            f"{_weekly_sentence(d)} "
            "Weakens if it closes below SMA44."
        )
        return {"category": CATEGORY_LABELS["trade_ready_confirmed"], "explanation": text}

    # ── Trade Ready: imminent crossover ──────────────────────────────────────
    if status == "signal":
        text = (
            "Trend and support passed; momentum is about to turn. "
            f"Trend: {_trend_ok_sentence(d)} "
            f"Support: {_support_ok_sentence(d)} "
            f"Momentum: MACD {_num(d.get('macd_cur'), 4)} is just under its signal line "
            f"{_num(d.get('signal_cur'), 4)}. The gap is {_num(d.get('imminent_gap_ratio'))}% of the signal "
            f"(limit {_IMMINENT_GAP_PCT}%) and has narrowed for "
            f"{d.get('hist_consecutive_rising', '?')} days running (needs {IMMINENT_HIST_MIN}), "
            "so a crossover looks likely within a day. "
            f"{_weekly_sentence(d)} "
            "Not crossed yet — fades if the gap widens."
        )
        return {"category": CATEGORY_LABELS["trade_ready_imminent"], "explanation": text}

    # ── Watchlist ────────────────────────────────────────────────────────────
    if status == "watchlist":
        hist_n = d.get("hist_consecutive_rising")
        missing = []
        if not d.get("imminent_hist_ok"):
            missing.append(
                f"the gap has narrowed only {hist_n if hist_n is not None else 0} day(s) "
                f"running (needs {IMMINENT_HIST_MIN}+)"
            )
        if not d.get("imminent_gap_ok"):
            missing.append(
                f"the gap is {_num(d.get('imminent_gap_ratio'))}% of the signal "
                f"(max {_IMMINENT_GAP_PCT}%)"
            )
        why_not_yet = (
            "Not Trade Ready: " + " and ".join(missing) + "."
            if missing else
            "Not Trade Ready: crossover not confirmed."
        )
        text = (
            "Good price setup, momentum not turned yet. "
            f"Trend: {_trend_ok_sentence(d)} "
            f"Support: {_support_ok_sentence(d)} "
            f"Momentum: MACD {_num(d.get('macd_cur'), 4)} is below its signal line "
            f"{_num(d.get('signal_cur'), 4)} with no bullish crossover in the last "
            f"{MACD_CROSSOVER_LOOKBACK} days. {why_not_yet} "
            f"{_weekly_sentence(d)} "
            "Moves to Trade Ready on a MACD crossover; dropped if it closes below SMA44."
        )
        return {"category": CATEGORY_LABELS["watchlist"], "explanation": text}

    # ── Rejected / skipped stages ────────────────────────────────────────────
    if stage == "preflight":
        raw = d.get("raw_bars", 0)
        text = (
            f"Skipped: only {raw} daily bars available ({d.get('valid_bars', 0)} usable after "
            f"indicator warm-up); the scanner needs {MIN_BARS} (44 just for the first SMA44). "
            "Typical of a recent listing or a data gap — not a judgement on the stock."
        )
        return {"category": CATEGORY_LABELS["preflight"], "explanation": text}

    if stage == "c1_sma_rising":
        close_vs = d.get("close_vs_sma_pct")
        try:
            cv = float(close_vs)
            pos = f"Closed {_abs_pct(cv)} {'above' if cv >= 0 else 'below'} it. "
        except (TypeError, ValueError):
            pos = ""
        text = (
            f"Rejected: SMA44 is not rising — {_rs(d.get('sma44_today'))} today vs "
            f"{_rs(d.get('c1a_sma_n_ago'))} {d.get('c1a_lookback')} days ago. "
            f"The {d.get('c1a_linreg_window')}-day trend and 5-day recovery checks showed no upturn either. "
            f"{pos}"
            "The strategy only buys pullbacks in rising trends. "
            "Qualifies once SMA44 stops falling and turns up."
        )
        return {"category": CATEGORY_LABELS["c1_sma_rising"], "explanation": text}

    if stage == "c1_slope":
        prior = _prior_checks_passed(d, "c1_slope")
        pct = d.get("pct_slope")
        pmin, pmax = d.get("pct_slope_min"), d.get("pct_slope_max")
        if not d.get("c1_slope_ready", False):
            detail = (
                f"Too few usable bars to measure the {SMA_SLOPE_LOOKBACK}-day slope "
                f"(have {d.get('valid_bars')}, need {SMA_SLOPE_LOOKBACK + 1})."
            )
            fix = "Can be judged once more history exists."
        elif d.get("c1_slope_error"):
            detail = "The price data produced an invalid slope value."
            fix = "Cannot be judged until the data is valid."
        else:
            try:
                pct_f = float(pct)
                pct_ok = float(pmin) <= pct_f <= float(pmax)
            except (TypeError, ValueError):
                pct_f, pct_ok = None, True
            try:
                atr_f = float(d.get("atr_slope"))
                atr_ok = atr_f > float(d.get("atr_slope_min", 0))
            except (TypeError, ValueError):
                atr_f, atr_ok = None, True

            if not pct_ok and pct_f is not None and pct_f < float(pmin):
                detail = (
                    f"SMA44 changed {_signed_pct(pct_f)} over {_bars(SMA_SLOPE_LOOKBACK)}; "
                    f"at most a {_abs_pct(pmin)} decline is allowed — trend too weak."
                )
                fix = "Needs SMA44 to stop falling."
            elif not pct_ok and pct_f is not None:
                detail = (
                    f"SMA44 rose {_signed_pct(pct_f)} over {_bars(SMA_SLOPE_LOOKBACK)}, above the "
                    f"{_abs_pct(pmax)} cap — stretched after a fast climb, so pullback risk is higher."
                )
                fix = "Qualifies if the rise cools and steadies."
            elif not atr_ok:
                detail = (
                    f"SMA44 moved {_num(atr_f)}x the stock's normal daily range over "
                    f"{_bars(SMA_SLOPE_LOOKBACK)} (needs above {_num(d.get('atr_slope_min'))}) — "
                    "not enough climb for its usual volatility."
                )
                fix = "Needs a clearer climb in SMA44."
            else:
                detail = (
                    f"Uneven trend: the older half was {_signed_pct(d.get('slope_first_half_pct'))}, "
                    f"the recent half only {_signed_pct(d.get('slope_second_half_pct'))} "
                    f"(needs {d.get('slope_recent_half_min_pct')}% or better) — it rose earlier "
                    "but has gone flat or down."
                )
                fix = "Qualifies if the recent trend turns back up."
        text = f"Rejected: trend exists but is not healthy enough. {prior}{detail} {fix}"
        return {"category": CATEGORY_LABELS["c1_slope"], "explanation": text}

    if stage == "c2_close_vs_sma":
        prior = _prior_checks_passed(d, "c2_close_vs_sma")
        lines = []
        low_dist = d.get("low_vs_sma44_pct")
        try:
            ld = float(low_dist)
        except (TypeError, ValueError):
            ld = None
        if not d.get("c2a_low_proximity_pass", False):
            if ld is not None and ld > 0:
                lines.append(
                    f"Low {_rs(d.get('low_today'))} stayed {_abs_pct(ld)} above SMA44 "
                    f"({_rs(d.get('sma44_today'))}); it must come within {_BUFFER_PCT}% — "
                    "no pullback, so no low-risk entry"
                )
            else:
                lines.append(
                    f"Low {_rs(d.get('low_today'))} fell {_abs_pct(ld)} below SMA44 "
                    f"({_rs(d.get('sma44_today'))}), beyond the {_BUFFER_PCT}% allowed — "
                    "deeper than a normal dip"
                )
        if not d.get("c2b_close_above_sma_pass", False):
            lines.append(
                f"Closed {_rs(d.get('close'))}, {_abs_pct(d.get('close_vs_sma_pct'))} below the line — "
                "support failed"
            )
        body = ". ".join(lines) + "." if lines else "Price did not meet the support rules."
        text = (
            f"Rejected: price is not at its support line. {prior}{body} "
            f"Qualifies if it returns within {_BUFFER_PCT}% of SMA44 and closes on or above it."
        )
        return {"category": CATEGORY_LABELS["c2_close_vs_sma"], "explanation": text}

    if stage == "c3_macd":
        prior = _prior_checks_passed(d, "c3_macd")
        if d.get("imminent_not_crossed"):
            mom = (
                f"MACD {_num(d.get('macd_cur'), 4)} is below its signal line "
                f"{_num(d.get('signal_cur'), 4)} with no crossover in the last {MACD_CROSSOVER_LOOKBACK} days."
            )
        else:
            mom = (
                f"MACD {_num(d.get('macd_cur'), 4)} is already above its signal line "
                f"{_num(d.get('signal_cur'), 4)}, but the crossover was over "
                f"{MACD_CROSSOVER_LOOKBACK} days ago — the fresh entry moment has passed."
            )
        text = (
            f"Rejected: price setup is fine but the momentum trigger is missing. {prior}{mom} "
            "Reconsidered if MACD dips below its signal line and crosses back above."
        )
        return {"category": CATEGORY_LABELS["c3_macd"], "explanation": text}

    if stage == "no_data":
        text = (
            "Not analysed: no usable price history was received (not yet listed, suspended or "
            "renamed, or a temporary data failure). Not a judgement on the stock's chart."
        )
        return {"category": CATEGORY_LABELS["no_data"], "explanation": text}

    return {
        "category": "Rejected",
        "explanation": "This stock did not meet the scanner's conditions on this date.",
    }

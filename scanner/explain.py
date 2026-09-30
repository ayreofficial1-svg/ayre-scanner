"""
scanner/explain.py
──────────────────
Plain-English explanations for backtest results.

`build_explanation()` turns the numbers already computed by
scanner.debug_evaluate.evaluate_debug() into a short, specific explanation
of WHY a stock landed in its category. It only reads values — it never
changes any pass/fail decision.

Returns:
    {"category": "<short plain label>", "explanation": "<several sentences>"}
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
    """'The long-term trend check passed' sentence with real numbers."""
    parts = [
        f"The 44-day average price line (SMA44) is at {_rs(d.get('sma44_today'))}"
    ]
    if d.get("sma44_lookback") is not None and d.get("pct_slope") is not None:
        parts.append(
            f", versus {_rs(d.get('sma44_lookback'))} {_bars(d.get('sma_slope_lookback', SMA_SLOPE_LOOKBACK))} ago "
            f"({_signed_pct(d.get('pct_slope'))})"
        )
    ma_type = d.get("ma_type")
    if ma_type == "type1":
        parts.append(", so the trend is clearly rising.")
    elif ma_type == "type2":
        parts.append(
            ", so the line is flat or only just turning up — a base-building / recovering trend, "
            "which the scanner accepts."
        )
    else:
        parts.append(", and it passed the trend checks.")
    return "".join(parts)


def _support_ok_sentence(d: dict) -> str:
    """'Price is at its support line' sentence with real numbers."""
    low_dist = d.get("low_vs_sma44_pct")
    try:
        low_dist_f = float(low_dist)
    except (TypeError, ValueError):
        low_dist_f = None

    if d.get("price_interaction_type") == "crossover" and low_dist_f is not None and low_dist_f < 0:
        s = (
            f"Price tested that line today: it dipped to {_rs(d.get('low_today'))} "
            f"({_abs_pct(low_dist_f)} below the line) and then recovered to close at "
            f"{_rs(d.get('close'))}, {_abs_pct(d.get('close_vs_sma_pct'))} above it — "
            f"buyers stepped in right at the line."
        )
    else:
        where = "above" if (low_dist_f is None or low_dist_f >= 0) else "below"
        s = (
            f"Price came down to the line today: the low of {_rs(d.get('low_today'))} was only "
            f"{_abs_pct(low_dist_f)} {where} it (allowed: within {_BUFFER_PCT}%), and the stock closed at "
            f"{_rs(d.get('close'))}, {_abs_pct(d.get('close_vs_sma_pct'))} above the line."
        )
    if d.get("is_double_bottom"):
        s += (
            " It also touched this line within the last 20 days and held, "
            "so buyers have defended this level more than once."
        )
    return s


def _weekly_sentence(d: dict) -> str:
    w = d.get("weekly_rising")
    if w is True:
        return "The weekly chart agrees: the weekly 44-period average is also rising."
    if w is False:
        return (
            "Caution: the weekly 44-period average is NOT rising. The scanner no longer removes such stocks "
            "before analysis, so this one is shown for your judgement — treat it with extra care."
        )
    return "The weekly trend could not be measured (not enough weekly history), so it is not used here."


def _prior_checks_passed(d: dict, up_to: str) -> str:
    """Which earlier checks this stock cleared, for rejected stocks."""
    cleared = []
    if up_to in ("c1_slope", "c2_close_vs_sma", "c3_macd"):
        cleared.append("the trend is pointing up (C1a)")
    if up_to in ("c2_close_vs_sma", "c3_macd"):
        cleared.append("the trend has a healthy size and steadiness (C1b/C1c)")
    if up_to == "c3_macd":
        cleared.append("price is sitting at its support line (C2)")
    if not cleared:
        return ""
    return "It did clear the earlier checks: " + "; ".join(cleared) + ". "


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
            "Why Trade Ready: the stock passed all three tests. "
            f"1) Trend — {_trend_ok_sentence(d)} "
            f"2) Support — {_support_ok_sentence(d)} "
            f"3) Momentum — the MACD line crossed above its signal line {age} "
            f"(MACD {_num(d.get('macd_cur'), 4)} vs signal {_num(d.get('signal_cur'), 4)}), "
            "which is the buy trigger. "
            f"{_weekly_sentence(d)} "
            "Watch-out: the setup weakens if the stock closes back below its 44-day line."
        )
        return {"category": CATEGORY_LABELS["trade_ready_confirmed"], "explanation": text}

    # ── Trade Ready: imminent crossover ──────────────────────────────────────
    if status == "signal":
        text = (
            "Why Trade Ready (early): trend and support checks passed, and momentum is about to turn. "
            f"1) Trend — {_trend_ok_sentence(d)} "
            f"2) Support — {_support_ok_sentence(d)} "
            f"3) Momentum — MACD ({_num(d.get('macd_cur'), 4)}) is still just below its signal line "
            f"({_num(d.get('signal_cur'), 4)}), but the gap is only {_num(d.get('imminent_gap_ratio'))}% of the signal level "
            f"(limit {_IMMINENT_GAP_PCT}%) and it has been closing for "
            f"{d.get('hist_consecutive_rising', '?')} days in a row (needs at least {IMMINENT_HIST_MIN}). "
            "A crossover looks likely within a day or so, so the scanner promotes it now instead of waiting. "
            f"{_weekly_sentence(d)} "
            "Watch-out: the crossover has not actually happened yet; if the gap widens again the setup fades."
        )
        return {"category": CATEGORY_LABELS["trade_ready_imminent"], "explanation": text}

    # ── Watchlist ────────────────────────────────────────────────────────────
    if status == "watchlist":
        hist_n = d.get("hist_consecutive_rising")
        missing = []
        if not d.get("imminent_hist_ok"):
            missing.append(
                f"the gap to the signal line has closed on only {hist_n if hist_n is not None else 0} "
                f"day(s) in a row (needs {IMMINENT_HIST_MIN}+)"
            )
        if not d.get("imminent_gap_ok"):
            missing.append(
                f"the gap is still {_num(d.get('imminent_gap_ratio'))}% of the signal level "
                f"(must be {_IMMINENT_GAP_PCT}% or less)"
            )
        why_not_yet = (
            "It is not Trade Ready yet because " + " and ".join(missing) + "."
            if missing else
            "It is not Trade Ready yet because the crossover has not been confirmed."
        )
        text = (
            "Why Watchlist: the price setup is good but momentum has not turned up yet. "
            f"1) Trend — {_trend_ok_sentence(d)} "
            f"2) Support — {_support_ok_sentence(d)} "
            f"3) Momentum — MACD ({_num(d.get('macd_cur'), 4)}) is still below its signal line "
            f"({_num(d.get('signal_cur'), 4)}) and there was no bullish crossover in the last "
            f"{MACD_CROSSOVER_LOOKBACK} days. {why_not_yet} "
            f"{_weekly_sentence(d)} "
            "What happens next: it moves to Trade Ready when MACD crosses above its signal line, "
            "and is removed if the stock closes below its 44-day line."
        )
        return {"category": CATEGORY_LABELS["watchlist"], "explanation": text}

    # ── Rejected / skipped stages ────────────────────────────────────────────
    if stage == "preflight":
        raw = d.get("raw_bars", 0)
        text = (
            f"Why skipped: only {raw} daily price bars were available up to this date, and "
            f"{d.get('valid_bars', 0)} were usable after the indicators warmed up. The scanner needs at least "
            f"{MIN_BARS} bars (and 44 just to calculate the first SMA44 value) to judge a trend reliably. "
            "This is typical for a recently listed stock or one with a data gap. "
            "This is not a judgement on the stock — it simply has too little history on this date."
        )
        return {"category": CATEGORY_LABELS["preflight"], "explanation": text}

    if stage == "c1_sma_rising":
        close_vs = d.get("close_vs_sma_pct")
        try:
            cv = float(close_vs)
            pos = f"The stock closed {_abs_pct(cv)} {'above' if cv >= 0 else 'below'} that line. "
        except (TypeError, ValueError):
            pos = ""
        text = (
            f"Why rejected: the 44-day average price line (SMA44) is not rising. It is at "
            f"{_rs(d.get('sma44_today'))} today, versus {_rs(d.get('c1a_sma_n_ago'))} "
            f"{d.get('c1a_lookback')} days ago, so it is not higher. The scanner also checked the "
            f"{d.get('c1a_linreg_window')}-day trend of the line and whether it is starting to recover "
            "over the last 5 days — neither showed an upturn. "
            f"{pos}"
            "This strategy only buys pullbacks in stocks whose average is moving up, because buying into a "
            "falling average means fighting the trend. "
            "It would qualify once the 44-day line stops falling and starts to rise."
        )
        return {"category": CATEGORY_LABELS["c1_sma_rising"], "explanation": text}

    if stage == "c1_slope":
        prior = _prior_checks_passed(d, "c1_slope")
        pct = d.get("pct_slope")
        pmin, pmax = d.get("pct_slope_min"), d.get("pct_slope_max")
        if not d.get("c1_slope_ready", False):
            detail = (
                f"There are not enough usable bars to measure the {SMA_SLOPE_LOOKBACK}-day slope "
                f"(have {d.get('valid_bars')}, need {SMA_SLOPE_LOOKBACK + 1})."
            )
            fix = "It can be judged once more history is available."
        elif d.get("c1_slope_error"):
            detail = "The price data for this stock produced an invalid value for the slope calculation."
            fix = "It cannot be judged until the data is valid."
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
                    f"Over the last {_bars(SMA_SLOPE_LOOKBACK)} the SMA44 line changed by {_signed_pct(pct_f)}. "
                    f"The scanner allows at most a {_abs_pct(pmin)} decline, so the trend is too weak."
                )
                fix = "It would need the 44-day line to stop falling before it qualifies."
            elif not pct_ok and pct_f is not None:
                detail = (
                    f"Over the last {_bars(SMA_SLOPE_LOOKBACK)} the SMA44 line rose {_signed_pct(pct_f)}, which "
                    f"is above the {_abs_pct(pmax)} cap. The stock is stretched after a very fast climb, which "
                    "raises the risk of a sharp pullback."
                )
                fix = "It would qualify if the rise cools off and the average grows more steadily."
            elif not atr_ok:
                detail = (
                    f"The SMA44 line moved {_num(atr_f)} times the stock's normal daily range over "
                    f"{_bars(SMA_SLOPE_LOOKBACK)} (must be above {_num(d.get('atr_slope_min'))}). "
                    "That means the line has not climbed enough compared with how much the stock normally "
                    "moves in a day."
                )
                fix = "It would qualify if the 44-day line climbed more clearly."
            else:
                detail = (
                    f"The trend is uneven. The older half of the period was "
                    f"{_signed_pct(d.get('slope_first_half_pct'))}, but the recent half is only "
                    f"{_signed_pct(d.get('slope_second_half_pct'))} "
                    f"(must be {d.get('slope_recent_half_min_pct')}% or better). "
                    "The stock was rising earlier but has recently gone flat or turned down."
                )
                fix = "It would qualify if the recent part of the trend stopped weakening and turned back up."
        text = (
            f"Why rejected: the trend exists but is not healthy enough. {prior}{detail} {fix}"
        )
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
                    f"Today's low of {_rs(d.get('low_today'))} stayed {_abs_pct(ld)} above the 44-day line "
                    f"({_rs(d.get('sma44_today'))}); the stock needs to come within {_BUFFER_PCT}% of it. "
                    "It has not pulled back to the line, so there is no low-risk entry point"
                )
            else:
                lines.append(
                    f"Today's low of {_rs(d.get('low_today'))} fell {_abs_pct(ld)} below the 44-day line "
                    f"({_rs(d.get('sma44_today'))}) — more than the {_BUFFER_PCT}% allowed. "
                    "That is a deeper break than a normal dip to support"
                )
        if not d.get("c2b_close_above_sma_pass", False):
            lines.append(
                f"The stock closed at {_rs(d.get('close'))}, {_abs_pct(d.get('close_vs_sma_pct'))} below the line, "
                "meaning the line failed to hold as support on this day"
            )
        body = ". ".join(lines) + "." if lines else "The price did not meet the support rules."
        text = (
            f"Why rejected: price is not sitting on its support line. {prior}{body} "
            f"It would qualify if the stock comes back to within {_BUFFER_PCT}% of the 44-day line "
            "and closes on or above it."
        )
        return {"category": CATEGORY_LABELS["c2_close_vs_sma"], "explanation": text}

    if stage == "c3_macd":
        prior = _prior_checks_passed(d, "c3_macd")
        if d.get("imminent_not_crossed"):
            mom = (
                f"MACD ({_num(d.get('macd_cur'), 4)}) is below its signal line ({_num(d.get('signal_cur'), 4)}) "
                f"with no crossover in the last {MACD_CROSSOVER_LOOKBACK} days."
            )
        else:
            mom = (
                f"MACD ({_num(d.get('macd_cur'), 4)}) is already above its signal line "
                f"({_num(d.get('signal_cur'), 4)}), but that crossover happened more than "
                f"{MACD_CROSSOVER_LOOKBACK} days ago. The buy trigger is meant to be a fresh crossover, so "
                "this entry moment has already passed."
            )
        text = (
            f"Why rejected: the price setup is fine but the momentum entry signal is missing. {prior}{mom} "
            "It would be reconsidered if MACD dips below its signal line and then crosses back above it."
        )
        return {"category": CATEGORY_LABELS["c3_macd"], "explanation": text}

    if stage == "no_data":
        text = (
            "Why not analysed: no usable price history was received for this stock up to this date. "
            "Possible reasons: the stock was not listed yet, was suspended or renamed, or the data request "
            "failed temporarily. It is listed here so you can see the whole stock universe was attempted — "
            "this is not a rejection based on the stock's chart."
        )
        return {"category": CATEGORY_LABELS["no_data"], "explanation": text}

    return {
        "category": "Rejected",
        "explanation": "This stock did not meet the scanner's conditions on this date.",
    }


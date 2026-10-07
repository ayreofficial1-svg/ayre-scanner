import type { RangeSuggestion } from './types'
import { inr } from './utils'

// ─────────────────────────────────────────────────────────────────────────────
// Suggested entry / exit ranges (admin website only).
//
// The server works the numbers out from the latest fetched price and the
// stock's own volatility (ATR14). They are only a starting point: every field
// that shows them stays editable, and nothing is saved or sent by asking.
// ─────────────────────────────────────────────────────────────────────────────

export type SuggestResult =
  | { ok: true; suggestion: RangeSuggestion }
  | { ok: false; error: string }

export async function fetchRangeSuggestion(symbol: string): Promise<SuggestResult> {
  const sym = symbol.trim().toUpperCase()
  if (!sym) return { ok: false, error: 'Pick a stock first' }
  try {
    const res = await fetch(`/api/ranges/suggest?symbol=${encodeURIComponent(sym)}`)
    const data = await res.json().catch(() => ({})) as Partial<RangeSuggestion> & { error?: string }
    if (!res.ok) return { ok: false, error: data.error || 'Could not calculate a range' }
    return { ok: true, suggestion: data as RangeSuggestion }
  } catch {
    return { ok: false, error: 'Could not calculate a range' }
  }
}

/** Plain-text input value for a price: no thousands separator, at most 2 decimals. */
export const priceText = (n: number | null | undefined): string =>
  n == null ? '' : String(Math.round(n * 100) / 100)

/** "₹538.00 – ₹543.00", or a single price when both ends are equal. */
export function rangeLabel(low?: number | null, high?: number | null): string {
  if (low == null || high == null) return '—'
  return Math.abs(high - low) < 1e-9 ? inr(low) : `${inr(low)} – ${inr(high)}`
}

/** One line telling the admin where the suggestion came from. */
export function suggestionNote(s: RangeSuggestion): string {
  const price = inr(s.price)
  const from = s.live
    ? `latest live price ${price}`
    : `last close ${price} (market closed or no live price)`
  const how = s.basis === 'atr14'
    ? `sized from the stock's own 14-day volatility (ATR ${inr(s.atr)})`
    : 'sized from a typical volatility, because no history is stored for this stock'
  return `Calculated from the ${from}, ${how}. You can edit every number.`
}

/** Both ends given, numeric, positive and in order? Returns an error text or null. */
export function rangeError(label: string, low: string, high: string): string | null {
  const l = low.trim()
  const h = high.trim()
  if (l === '' && h === '') return null
  if (l === '' || h === '') return `${label} needs both a low and a high price`
  const lo = Number(l)
  const hi = Number(h)
  if (Number.isNaN(lo) || Number.isNaN(hi)) return `${label} prices must be numbers`
  if (lo <= 0 || hi <= 0) return `${label} prices must be greater than zero`
  if (lo > hi) return `${label}: the low price cannot be higher than the high price`
  return null
}

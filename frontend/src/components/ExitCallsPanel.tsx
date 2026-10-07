import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import type { ExitCall, RangeSuggestion } from '../types'
import { inr } from '../utils'
import StockPicker from './StockPicker'
import { postGuarded } from '../pushApi'
import { fetchRangeSuggestion, priceText, rangeError, rangeLabel, suggestionNote } from '../rangeApi'

// ─────────────────────────────────────────────────────────────────────────────
// ExitCallsPanel — shown on the Notifications tab as "Send exit alert"
//
// Pick a stock, enter the profit and the exit range, and the call goes to the
// app's Alerts section (and as a phone notification). An exit call is one-off —
// it is sent once and kept below as history, not edited or kept "live" like a
// signal. Profit is per share in ₹; enter a minus sign for a loss.
//
// Exit range: when a stock is picked the server suggests a range from the
// latest fetched price and the stock's own volatility — always tighter than the
// entry range, because an exit has to be hit. It is only a starting point; both
// ends stay editable and the alert goes out with exactly what is in the form.
// ─────────────────────────────────────────────────────────────────────────────

function emptyForm() {
  return { symbol: '', profit: '', exit_low: '', exit_high: '' }
}

const signed = (n: number) => (n >= 0 ? '+' : '−') + inr(Math.abs(n))

const when = (iso: string) => {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString('en-IN', {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

export default function ExitCallsPanel({ onPushActivity }: { onPushActivity?: () => void }) {
  const [exits, setExits] = useState<ExitCall[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [form, setForm] = useState(emptyForm)
  const [submitting, setSubmitting] = useState(false)
  const [suggestion, setSuggestion] = useState<RangeSuggestion | null>(null)
  const [suggestBusy, setSuggestBusy] = useState(false)
  const [suggestMsg, setSuggestMsg] = useState<string | null>(null)
  const suggestSeq = useRef(0)        // invalidates an answer that arrives after the stock changed
  const rangeEdited = useRef(false)   // admin typed a range: never overwrite it automatically

  const load = async () => {
    try {
      const res  = await fetch('/api/exits')
      const data = await res.json() as { exits?: ExitCall[]; error?: string }
      if (!res.ok) throw new Error(data.error || 'Failed to load exit calls')
      setExits(data.exits ?? [])
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load exit calls')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const requestSuggestion = async (symbol: string, force: boolean) => {
    const sym = symbol.trim().toUpperCase()
    if (!sym) return
    const seq = ++suggestSeq.current
    setSuggestBusy(true)
    setSuggestMsg(null)
    const result = await fetchRangeSuggestion(sym)
    if (seq !== suggestSeq.current) return
    setSuggestBusy(false)
    if (!result.ok) {
      setSuggestMsg(result.error)
      return
    }
    if (!force && rangeEdited.current) return
    rangeEdited.current = false
    setSuggestion(result.suggestion)
    setForm(f => ({
      ...f,
      exit_low: priceText(result.suggestion.exit.low),
      exit_high: priceText(result.suggestion.exit.high),
    }))
  }

  const resetForm = () => {
    suggestSeq.current += 1
    rangeEdited.current = false
    setSuggestion(null)
    setSuggestMsg(null)
    setSuggestBusy(false)
    setForm(emptyForm())
  }

  const sendExit = async (event: FormEvent) => {
    event.preventDefault()
    const symbol = form.symbol.trim().toUpperCase()
    if (!symbol) return
    setError(null)
    setNotice(null)

    const profit = Number(form.profit)
    if (form.profit.trim() === '' || Number.isNaN(profit)) {
      setError('Profit must be a number')
      return
    }
    if (form.exit_low.trim() === '' && form.exit_high.trim() === '') {
      setError('Enter the exit range (or pick the stock to have it calculated)')
      return
    }
    const rangeProblem = rangeError('Exit range', form.exit_low, form.exit_high)
    if (rangeProblem) {
      setError(rangeProblem)
      return
    }
    const exit_low = Number(form.exit_low)
    const exit_high = Number(form.exit_high)
    // The server stores the middle of the range as the exit price.
    const exit_price = Math.round(((exit_low + exit_high) / 2) * 100) / 100

    // A push can't be taken back, so ask once before it goes out.
    if (!window.confirm(
      `Send an exit alert for ${symbol} (exit range ${rangeLabel(exit_low, exit_high)}) to everyone using the app?`,
    )) return

    setSubmitting(true)
    try {
      const { ok, data } = await postGuarded('/api/exits', { symbol, profit, exit_low, exit_high, exit_price })
      if (!ok) throw new Error(data.error || 'Failed to send exit call')
      resetForm()
      setNotice(
        data.notified
          ? `Exit call for ${symbol} sent to ${data.audience ?? 'the'} phone(s).`
          : `Exit call for ${symbol} saved, but push isn't set up on the server, so no phone was notified.`,
      )
      await load()
      onPushActivity?.()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to send exit call')
    } finally {
      setSubmitting(false)
    }
  }

  const removeExit = async (id: string) => {
    try {
      const res  = await fetch(`/api/exits/${id}`, { method: 'DELETE' })
      const data = await res.json().catch(() => ({})) as { error?: string }
      if (!res.ok) throw new Error(data.error || 'Failed to remove exit call')
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to remove exit call')
    }
  }

  return (
    <div className="section" id="panel-send-exit-alert">
      <div className="section-header">
        <div className="section-title">Send exit alert</div>
        <div className="section-sub">Goes to every phone and the app's Alerts</div>
      </div>

      {error && <div className="error-bar">{error}</div>}
      {notice && <div className="notice-bar">{notice}</div>}

      <form className="clean-form" onSubmit={sendExit}>
        <div className="clean-form-grid">
          <StockPicker
            label="Stock"
            value={form.symbol}
            onChange={symbol => {
              suggestSeq.current += 1     // an answer for the previous text is no longer wanted
              setForm(f => ({ ...f, symbol }))
            }}
            // The exit range is filled in as soon as a stock is chosen.
            onSelect={symbol => { rangeEdited.current = false; requestSuggestion(symbol, false) }}
            required
          />
          <label className="field">
            <span>Profit</span>
            <input
              type="number"
              step="0.01"
              value={form.profit}
              onChange={e => setForm(f => ({ ...f, profit: e.target.value }))}
              placeholder="₹ (minus for a loss)"
              required
            />
          </label>
          <label className="field">
            <span>Exit range — from</span>
            <input
              type="number"
              step="0.01"
              min="0"
              value={form.exit_low}
              onChange={e => { rangeEdited.current = true; setForm(f => ({ ...f, exit_low: e.target.value })) }}
              placeholder="₹ low"
              required
            />
          </label>
          <label className="field">
            <span>Exit range — to</span>
            <input
              type="number"
              step="0.01"
              min="0"
              value={form.exit_high}
              onChange={e => { rangeEdited.current = true; setForm(f => ({ ...f, exit_high: e.target.value })) }}
              placeholder="₹ high"
              required
            />
          </label>
        </div>

        <div className="clean-form-footer" style={{ marginBottom: '0.5rem' }}>
          <span className="switch-label">
            {suggestBusy
              ? 'Calculating the exit range from the latest price…'
              : suggestMsg
                ? suggestMsg
                : suggestion
                  ? suggestionNote(suggestion)
                  : 'The exit range is filled in from the latest price when you pick a stock. It is tighter than an entry range. Edit it freely.'}
          </span>
          <div className="clean-form-actions">
            <button
              type="button"
              className="theme-btn"
              disabled={suggestBusy || !form.symbol.trim()}
              title="Replace the range with a fresh one from the latest price"
              onClick={() => requestSuggestion(form.symbol, true)}
            >
              Recalculate from live price
            </button>
          </div>
        </div>

        <div className="clean-form-footer">
          <span className="switch-label">Profit is per share</span>
          <div className="clean-form-actions">
            <button className="rescan-btn" type="submit" disabled={submitting}>
              {submitting ? 'Sending...' : 'Send exit call'}
            </button>
          </div>
        </div>
      </form>

      {loading ? (
        <div className="empty-state">Loading exit calls...</div>
      ) : exits.length === 0 ? (
        <div className="empty-state">No exit calls sent yet.</div>
      ) : (
        <div className="signal-list">
          {exits.map(x => (
            <div className="signal-row" key={x.id}>
              <div className="signal-row-main">
                <span className="card-sym">{x.symbol}</span>
                <span className="tag-disabled">{when(x.created_at)}</span>
              </div>
              <div className="signal-row-prices">
                <span className="price-tag">
                  <small>{x.profit < 0 ? 'Loss' : 'Profit'}</small>
                  <strong style={{ color: x.profit < 0 ? 'var(--red)' : 'var(--green)' }}>
                    {signed(x.profit)}
                  </strong>
                </span>
                <span className="price-tag">
                  <small>{x.exit_low != null && x.exit_high != null ? 'Exit range' : 'Exit'}</small>
                  <strong>
                    {x.exit_low != null && x.exit_high != null
                      ? rangeLabel(x.exit_low, x.exit_high)
                      : inr(x.exit_price)}
                  </strong>
                </span>
              </div>
              <div className="signal-row-actions">
                <button
                  className="theme-btn"
                  title="Removes it from this list only; the notification can't be recalled"
                  onClick={() => removeExit(x.id)}
                >
                  Remove
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

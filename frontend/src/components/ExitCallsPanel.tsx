import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import type { ExitCall } from '../types'
import { inr } from '../utils'
import StockPicker from './StockPicker'
import { postGuarded } from '../pushApi'

// ─────────────────────────────────────────────────────────────────────────────
// ExitCallsPanel
//
// Sits under the signal form on the Signals tab, separate from it. Pick a
// stock, enter the profit and the exit price, and the call goes to the app's
// Alerts section (and as a phone notification). An exit call is one-off — it
// is sent once and kept below as history, not edited or kept "live" like a
// signal. Profit is per share in ₹; enter a minus sign for a loss.
// ─────────────────────────────────────────────────────────────────────────────

function emptyForm() {
  return { symbol: '', profit: '', exit_price: '' }
}

const signed = (n: number) => (n >= 0 ? '+' : '−') + inr(Math.abs(n))

const when = (iso: string) => {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString('en-IN', {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

export default function ExitCallsPanel() {
  const [exits, setExits] = useState<ExitCall[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [form, setForm] = useState(emptyForm)
  const [submitting, setSubmitting] = useState(false)

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

  const sendExit = async (event: FormEvent) => {
    event.preventDefault()
    const symbol = form.symbol.trim().toUpperCase()
    if (!symbol) return
    setError(null)
    setNotice(null)

    const profit = Number(form.profit)
    const exit_price = Number(form.exit_price)
    if (form.profit.trim() === '' || Number.isNaN(profit)
        || form.exit_price.trim() === '' || Number.isNaN(exit_price)) {
      setError('Profit and exit price must be numbers')
      return
    }
    if (exit_price <= 0) {
      setError('Exit price must be greater than zero')
      return
    }

    // A push can't be taken back, so ask once before it goes out.
    if (!window.confirm(`Send an exit alert for ${symbol} to everyone using the app?`)) return

    setSubmitting(true)
    try {
      const { ok, data } = await postGuarded('/api/exits', { symbol, profit, exit_price })
      if (!ok) throw new Error(data.error || 'Failed to send exit call')
      setForm(emptyForm())
      setNotice(
        data.notified
          ? `Exit call for ${symbol} sent to ${data.audience ?? 'the'} phone(s).`
          : `Exit call for ${symbol} saved, but push isn't set up on the server, so no phone was notified.`,
      )
      await load()
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
    <div className="section">
      <div className="section-header">
        <div className="section-title">Exit calls</div>
        <div className="section-sub">Sent to the app's Alerts</div>
      </div>

      {error && <div className="error-bar">{error}</div>}
      {notice && <div className="notice-bar">{notice}</div>}

      <form className="clean-form" onSubmit={sendExit}>
        <div className="clean-form-grid">
          <StockPicker
            label="Stock"
            value={form.symbol}
            onChange={symbol => setForm(f => ({ ...f, symbol }))}
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
            <span>Exit price</span>
            <input
              type="number"
              step="0.01"
              min="0"
              value={form.exit_price}
              onChange={e => setForm(f => ({ ...f, exit_price: e.target.value }))}
              placeholder="₹"
              required
            />
          </label>
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
                  <small>Exit</small>
                  <strong>{inr(x.exit_price)}</strong>
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

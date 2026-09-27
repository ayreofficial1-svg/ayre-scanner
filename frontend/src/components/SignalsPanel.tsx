import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import type { SignalPick } from '../types'
import { inr, pct } from '../utils'
import StockPicker from './StockPicker'

// ─────────────────────────────────────────────────────────────────────────────
// SignalsPanel
//
// Pick a stock, set Entry / Exit / Stop Loss, and it stays live in the app
// until manually disabled or removed. Everything else the old form exposed
// (rationale, category, image, scheduling window, featured/pinned ordering)
// is still accepted by the API for backward compatibility but is no longer
// surfaced here — this tab now only edits the fields it's actually for.
// ─────────────────────────────────────────────────────────────────────────────

function emptyForm() {
  return {
    id: '',
    symbol: '',
    entry_price: '',
    exit_price: '',
    stop_loss: '',
    enabled: true,
  }
}

export default function SignalsPanel() {
  const [signals, setSignals] = useState<SignalPick[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [form, setForm] = useState(emptyForm)
  const [submitting, setSubmitting] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const res  = await fetch('/api/signals?all=1')
      const data = await res.json() as { signals: SignalPick[]; error?: string }
      if (!res.ok) throw new Error(data.error || 'Failed to load signals')
      setSignals(data.signals)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load signals')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const resetForm = () => setForm(emptyForm())

  const editSignal = (signal: SignalPick) => {
    setForm({
      id: signal.id,
      symbol: signal.symbol,
      entry_price: signal.entry_price != null ? String(signal.entry_price) : '',
      exit_price: signal.exit_price != null ? String(signal.exit_price) : '',
      stop_loss: signal.stop_loss != null ? String(signal.stop_loss) : '',
      enabled: signal.enabled ?? signal.active ?? true,
    })
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const addSignal = async (event: FormEvent) => {
    event.preventDefault()
    if (!form.symbol.trim()) return
    setError(null)

    const numOrNull = (v: string) => (v.trim() === '' ? null : Number(v))
    const entry_price = numOrNull(form.entry_price)
    const exit_price = numOrNull(form.exit_price)
    const stop_loss = numOrNull(form.stop_loss)
    if ([entry_price, exit_price, stop_loss].some(v => v !== null && Number.isNaN(v))) {
      setError('Entry, exit and stop loss must be numeric')
      return
    }

    setSubmitting(true)
    try {
      const res  = await fetch('/api/signals', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: form.id || undefined,
          symbol: form.symbol.trim().toUpperCase(),
          enabled: form.enabled,
          entry_price,
          exit_price,
          stop_loss,
        }),
      })
      const data = await res.json().catch(() => ({})) as { error?: string }
      if (!res.ok) throw new Error(data.error || 'Failed to save signal')
      resetForm()
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save signal')
    } finally {
      setSubmitting(false)
    }
  }

  const removeSignal = async (id: string) => {
    try {
      const res  = await fetch(`/api/signals/${id}`, { method: 'DELETE' })
      const data = await res.json().catch(() => ({})) as { error?: string }
      if (!res.ok) throw new Error(data.error || 'Failed to remove signal')
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to remove signal')
    }
  }

  return (
    <div className="section">
      <div className="section-header">
        <div className="section-title">Signals</div>
      </div>

      {error && <div className="error-bar">{error}</div>}

      <form className="clean-form" onSubmit={addSignal}>
        <div className="clean-form-grid">
          <StockPicker
            label="Stock"
            value={form.symbol}
            onChange={symbol => setForm(f => ({ ...f, symbol }))}
            required
          />
          <label className="field">
            <span>Entry price</span>
            <input
              type="number"
              step="0.01"
              value={form.entry_price}
              onChange={e => setForm(f => ({ ...f, entry_price: e.target.value }))}
              placeholder="₹"
            />
          </label>
          <label className="field">
            <span>Exit price</span>
            <input
              type="number"
              step="0.01"
              value={form.exit_price}
              onChange={e => setForm(f => ({ ...f, exit_price: e.target.value }))}
              placeholder="₹"
            />
          </label>
          <label className="field">
            <span>Stop loss</span>
            <input
              type="number"
              step="0.01"
              value={form.stop_loss}
              onChange={e => setForm(f => ({ ...f, stop_loss: e.target.value }))}
              placeholder="₹"
            />
          </label>
        </div>

        <div className="clean-form-footer">
          <label className="switch-field">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={e => setForm(f => ({ ...f, enabled: e.target.checked }))}
            />
            <span className="switch-track" aria-hidden="true"><span className="switch-thumb" /></span>
            <span className="switch-label">Enabled</span>
          </label>

          <div className="clean-form-actions">
            {form.id && <button type="button" className="theme-btn" onClick={resetForm}>Cancel</button>}
            <button className="rescan-btn" type="submit" disabled={submitting}>
              {submitting ? 'Saving...' : form.id ? 'Update signal' : 'Add signal'}
            </button>
          </div>
        </div>
      </form>

      {loading ? (
        <div className="empty-state">Loading signals...</div>
      ) : signals.length === 0 ? (
        <div className="empty-state">No signals yet. Add your first stock above.</div>
      ) : (
        <div className="signal-list">
          {signals.map(s => (
            <div className={`signal-row${s.enabled === false ? ' disabled' : ''}`} key={s.id}>
              <div className="signal-row-main">
                <span className="card-sym">{s.symbol}</span>
                <span className={`card-val ${(s.change_pct ?? 0) >= 0 ? 'g' : 'r'}`}>
                  {inr(s.last_price)} · {pct(s.change_pct)}
                </span>
                {s.enabled === false && <span className="tag-disabled">Disabled</span>}
              </div>
              <div className="signal-row-prices">
                <PriceTag label="Entry" value={s.entry_price} />
                <PriceTag label="Exit" value={s.exit_price} />
                <PriceTag label="Stop loss" value={s.stop_loss} />
              </div>
              <div className="signal-row-actions">
                <button className="theme-btn" onClick={() => editSignal(s)}>Edit</button>
                <button className="theme-btn" onClick={() => removeSignal(s.id)}>Remove</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function PriceTag({ label, value }: { label: string; value?: number | null }) {
  return (
    <span className="price-tag">
      <small>{label}</small>
      <strong>{value != null ? inr(value) : '—'}</strong>
    </span>
  )
}

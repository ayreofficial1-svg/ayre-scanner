import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import type { SignalPick } from '../types'
import { inr, pct } from '../utils'

export default function SignalsPanel() {
  const [signals, setSignals] = useState<SignalPick[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState<string | null>(null)
  const [symbol, setSymbol]       = useState('')
  const [rationale, setRationale] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const res  = await fetch('/api/signals')
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

  const addSignal = async (event: FormEvent) => {
    event.preventDefault()
    if (!symbol.trim()) return
    setSubmitting(true)
    try {
      const res  = await fetch('/api/signals', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: symbol.trim().toUpperCase(), rationale: rationale.trim() }),
      })
      const data = await res.json().catch(() => ({})) as { error?: string }
      if (!res.ok) throw new Error(data.error || 'Failed to add signal')
      setSymbol('')
      setRationale('')
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to add signal')
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
        <div>
          <div className="section-title">Signals</div>
          <div className="section-sub">Admin-curated picks shown in the consumer app</div>
        </div>
      </div>

      <form className="debug-form" onSubmit={addSignal}>
        <label className="field inline-field">
          <span>Symbol</span>
          <input value={symbol} onChange={e => setSymbol(e.target.value)} placeholder="RELIANCE" required />
        </label>
        <label className="field">
          <span>Rationale</span>
          <input value={rationale} onChange={e => setRationale(e.target.value)} placeholder="Short reason" />
        </label>
        <button className="rescan-btn" type="submit" disabled={submitting}>
          {submitting ? 'Adding...' : 'Add Signal'}
        </button>
      </form>

      {error && <div className="error-bar">{error}</div>}

      {loading ? (
        <div className="empty-state">Loading signals...</div>
      ) : signals.length === 0 ? (
        <div className="empty-state">No active signals.</div>
      ) : (
        <div className="backtest-results-list">
          {signals.map(s => (
            <div className="backtest-result" key={s.id}>
              <div className="backtest-result-main">
                <span className="card-sym">{s.symbol}</span>
                <span className={`card-val ${(s.change_pct ?? 0) >= 0 ? 'g' : 'r'}`}>
                  {inr(s.last_price)} · {pct(s.change_pct)}
                </span>
                <button className="theme-btn" onClick={() => removeSignal(s.id)}>Remove</button>
              </div>
              <div className="details-value">{s.rationale}</div>
              <div className="card-date">{s.date_added}{s.added_by ? ` · ${s.added_by}` : ''}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

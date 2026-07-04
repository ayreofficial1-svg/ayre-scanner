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
  const [editingId, setEditingId] = useState('')
  const [enabled, setEnabled] = useState(true)
  const [featured, setFeatured] = useState(false)
  const [pinned, setPinned] = useState(false)
  const [displayOrder, setDisplayOrder] = useState(0)
  const [category, setCategory] = useState('')
  const [imageUrl, setImageUrl] = useState('')
  const [startAt, setStartAt] = useState('')
  const [endAt, setEndAt] = useState('')
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

  const resetForm = () => {
    setEditingId('')
    setSymbol('')
    setRationale('')
    setEnabled(true)
    setFeatured(false)
    setPinned(false)
    setDisplayOrder(0)
    setCategory('')
    setImageUrl('')
    setStartAt('')
    setEndAt('')
  }

  const editSignal = (signal: SignalPick) => {
    setEditingId(signal.id)
    setSymbol(signal.symbol)
    setRationale(signal.rationale ?? '')
    setEnabled(signal.enabled ?? signal.active ?? true)
    setFeatured(signal.featured ?? false)
    setPinned(signal.pinned ?? false)
    setDisplayOrder(signal.display_order ?? 0)
    setCategory(signal.category ?? '')
    setImageUrl(signal.image_url ?? '')
    setStartAt(signal.start_at ?? '')
    setEndAt(signal.end_at ?? '')
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const addSignal = async (event: FormEvent) => {
    event.preventDefault()
    if (!symbol.trim()) return
    setSubmitting(true)
    try {
      const res  = await fetch('/api/signals', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: editingId || undefined,
          symbol: symbol.trim().toUpperCase(),
          rationale: rationale.trim(),
          enabled,
          featured,
          pinned,
          display_order: displayOrder,
          category: category.trim() || null,
          image_url: imageUrl.trim() || null,
          start_at: startAt || null,
          end_at: endAt || null,
        }),
      })
      const data = await res.json().catch(() => ({})) as { error?: string }
      if (!res.ok) throw new Error(data.error || 'Failed to add signal')
      resetForm()
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
        <label className="field inline-field">
          <span>Order</span>
          <input type="number" value={displayOrder} onChange={e => setDisplayOrder(Number(e.target.value) || 0)} />
        </label>
        <label className="field">
          <span>Category</span>
          <input value={category} onChange={e => setCategory(e.target.value)} placeholder="Breakout" />
        </label>
        <label className="field">
          <span>Image URL</span>
          <input value={imageUrl} onChange={e => setImageUrl(e.target.value)} placeholder="/uploads/chart.webp" />
        </label>
        <label className="field inline-field">
          <span>Start</span>
          <input type="datetime-local" value={startAt} onChange={e => setStartAt(e.target.value)} />
        </label>
        <label className="field inline-field">
          <span>End</span>
          <input type="datetime-local" value={endAt} onChange={e => setEndAt(e.target.value)} />
        </label>
        <label className="field" style={{ flexDirection: 'row', alignItems: 'center', gap: '0.6rem' }}>
          <input type="checkbox" style={{ width: 'auto' }} checked={enabled} onChange={e => setEnabled(e.target.checked)} />
          <span style={{ textTransform: 'none', letterSpacing: 'normal' }}>Enabled</span>
        </label>
        <label className="field" style={{ flexDirection: 'row', alignItems: 'center', gap: '0.6rem' }}>
          <input type="checkbox" style={{ width: 'auto' }} checked={featured} onChange={e => setFeatured(e.target.checked)} />
          <span style={{ textTransform: 'none', letterSpacing: 'normal' }}>Featured</span>
        </label>
        <label className="field" style={{ flexDirection: 'row', alignItems: 'center', gap: '0.6rem' }}>
          <input type="checkbox" style={{ width: 'auto' }} checked={pinned} onChange={e => setPinned(e.target.checked)} />
          <span style={{ textTransform: 'none', letterSpacing: 'normal' }}>Pinned</span>
        </label>
        <button className="rescan-btn" type="submit" disabled={submitting}>
          {submitting ? 'Saving...' : editingId ? 'Update Signal' : 'Add Signal'}
        </button>
        {editingId && <button type="button" className="theme-btn" onClick={resetForm}>Cancel edit</button>}
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
                <button className="theme-btn" onClick={() => editSignal(s)}>Edit</button>
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

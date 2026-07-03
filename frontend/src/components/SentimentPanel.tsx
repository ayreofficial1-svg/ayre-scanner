import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import type { SentimentData } from '../types'

export default function SentimentPanel() {
  const [data, setData]     = useState<SentimentData | null>(null)
  const [value, setValue]   = useState('50')
  const [note, setNote]     = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving]   = useState(false)
  const [error, setError]     = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    try {
      const res  = await fetch('/api/sentiment')
      const json = await res.json() as SentimentData
      setData(json)
      if (json.sentiment != null) setValue(String(json.sentiment))
      setNote(json.note ?? '')
      setError(null)
    } catch {
      setError('Failed to load sentiment')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const save = async (event: FormEvent) => {
    event.preventDefault()
    const num = Number(value)
    if (!Number.isInteger(num) || num < 0 || num > 100) {
      setError('Value must be a whole number between 0 and 100')
      return
    }
    setSaving(true)
    try {
      const res  = await fetch('/api/sentiment', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ value: num, note: note.trim() || null }),
      })
      const json = await res.json().catch(() => ({})) as SentimentData & { error?: string }
      if (!res.ok) throw new Error(json.error || 'Failed to save sentiment')
      setData(json)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save sentiment')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="section">
      <div className="section-header">
        <div>
          <div className="section-title">Sentiment</div>
          <div className="section-sub">Manual placeholder gauge (0-100) shown in the consumer app</div>
        </div>
      </div>

      {loading ? (
        <div className="empty-state">Loading sentiment...</div>
      ) : (
        <>
          <div className="stats-row">
            <div className="stat-cell">
              <div className="stat-num gold">{data?.sentiment ?? '—'}</div>
              <div className="stat-lbl">Current Value</div>
            </div>
          </div>
          {data?.updated_at && (
            <div className="card-date" style={{ marginBottom: '1.5rem' }}>
              Last updated {new Date(data.updated_at).toLocaleString('en-IN')}
            </div>
          )}

          <form className="debug-form" onSubmit={save}>
            <label className="field inline-field">
              <span>Value (0-100)</span>
              <input
                type="number" min={0} max={100}
                value={value} onChange={e => setValue(e.target.value)} required
              />
            </label>
            <label className="field">
              <span>Note (optional)</span>
              <input value={note} onChange={e => setNote(e.target.value)} placeholder="Short note" />
            </label>
            <button className="rescan-btn" type="submit" disabled={saving}>
              {saving ? 'Saving...' : 'Save'}
            </button>
          </form>
        </>
      )}

      {error && <div className="error-bar">{error}</div>}
    </div>
  )
}

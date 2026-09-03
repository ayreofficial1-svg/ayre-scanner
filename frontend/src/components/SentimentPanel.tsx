import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import type { InsightContent, SentimentData } from '../types'

const EMPTY_INSIGHT = {
  id: '',
  title: '',
  body: '',
  category: '',
  enabled: true,
  featured: false,
  pinned: false,
  display_order: 0,
  image_url: '',
  start_at: '',
  end_at: '',
}

export default function SentimentPanel() {
  const [data, setData]     = useState<SentimentData | null>(null)
  const [value, setValue]   = useState('50')
  const [note, setNote]     = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving]   = useState(false)
  const [error, setError]     = useState<string | null>(null)
  const [insights, setInsights] = useState<InsightContent[]>([])
  const [insightForm, setInsightForm] = useState(EMPTY_INSIGHT)

  const load = async () => {
    setLoading(true)
    try {
      const res  = await fetch('/api/sentiment')
      const json = await res.json() as SentimentData
      const insightRes = await fetch('/api/insights?all=1')
      const insightJson = await insightRes.json() as { insights: InsightContent[] }
      setData(json)
      setInsights(insightJson.insights ?? [])
      if (json.sentiment != null) setValue(String(json.sentiment))
      setNote(json.note ?? '')
      setError(null)
    } catch {
      setError('Failed to load sentiment')
    } finally {
      setLoading(false)
    }
  }

  const editInsight = (insight: InsightContent) => {
    setInsightForm({
      id: insight.id,
      title: insight.title,
      body: insight.body,
      category: insight.category ?? '',
      enabled: insight.enabled,
      featured: insight.featured ?? false,
      pinned: insight.pinned ?? false,
      display_order: insight.display_order ?? 0,
      image_url: insight.image_url ?? '',
      start_at: insight.start_at ?? '',
      end_at: insight.end_at ?? '',
    })
  }

  const saveInsight = async (event: FormEvent) => {
    event.preventDefault()
    try {
      const res = await fetch('/api/insights', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...insightForm,
          category: insightForm.category.trim() || null,
          image_url: insightForm.image_url.trim() || null,
          start_at: insightForm.start_at || null,
          end_at: insightForm.end_at || null,
        }),
      })
      const json = await res.json().catch(() => ({})) as { error?: string }
      if (!res.ok) throw new Error(json.error || 'Failed to save insight')
      setInsightForm(EMPTY_INSIGHT)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save insight')
    }
  }

  const hideInsight = async (id: string) => {
    try {
      const res = await fetch(`/api/insights/${id}`, { method: 'DELETE' })
      const json = await res.json().catch(() => ({})) as { error?: string }
      if (!res.ok) throw new Error(json.error || 'Failed to hide insight')
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to hide insight')
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
              Last updated {new Date(data.updated_at).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' })}
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

      <div className="section-header" style={{ marginTop: '2rem' }}>
        <div>
          <div className="section-title">Insights</div>
          <div className="section-sub">Content cards shown below the mobile sentiment gauge</div>
        </div>
      </div>

      <form className="debug-form" onSubmit={saveInsight}>
        <label className="field">
          <span>Title</span>
          <input value={insightForm.title} onChange={e => setInsightForm(f => ({ ...f, title: e.target.value }))} required />
        </label>
        <label className="field">
          <span>Body</span>
          <input value={insightForm.body} onChange={e => setInsightForm(f => ({ ...f, body: e.target.value }))} required />
        </label>
        <label className="field">
          <span>Category</span>
          <input value={insightForm.category} onChange={e => setInsightForm(f => ({ ...f, category: e.target.value }))} />
        </label>
        <label className="field inline-field">
          <span>Order</span>
          <input type="number" value={insightForm.display_order} onChange={e => setInsightForm(f => ({ ...f, display_order: Number(e.target.value) || 0 }))} />
        </label>
        <label className="field">
          <span>Image URL</span>
          <input value={insightForm.image_url} onChange={e => setInsightForm(f => ({ ...f, image_url: e.target.value }))} />
        </label>
        <label className="field inline-field">
          <span>Start</span>
          <input type="datetime-local" value={insightForm.start_at} onChange={e => setInsightForm(f => ({ ...f, start_at: e.target.value }))} />
        </label>
        <label className="field inline-field">
          <span>End</span>
          <input type="datetime-local" value={insightForm.end_at} onChange={e => setInsightForm(f => ({ ...f, end_at: e.target.value }))} />
        </label>
        {(['enabled', 'featured', 'pinned'] as const).map(key => (
          <label key={key} className="field" style={{ flexDirection: 'row', alignItems: 'center', gap: '0.6rem' }}>
            <input type="checkbox" style={{ width: 'auto' }} checked={insightForm[key]} onChange={e => setInsightForm(f => ({ ...f, [key]: e.target.checked }))} />
            <span style={{ textTransform: 'none', letterSpacing: 'normal' }}>{key}</span>
          </label>
        ))}
        <button className="rescan-btn" type="submit">{insightForm.id ? 'Update Insight' : 'Create Insight'}</button>
        {insightForm.id && <button type="button" className="theme-btn" onClick={() => setInsightForm(EMPTY_INSIGHT)}>Cancel edit</button>}
      </form>

      <div className="backtest-results-list">
        {insights.map(insight => (
          <div className="backtest-result" key={insight.id}>
            <div className="backtest-result-main">
              <span className="card-sym">{insight.title}</span>
              <span className="card-val dim">{insight.category || '—'}</span>
              <button className="theme-btn" onClick={() => editInsight(insight)}>Edit</button>
              <button className="theme-btn" onClick={() => hideInsight(insight.id)}>Hide</button>
            </div>
            <div className="details-value">{insight.body}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

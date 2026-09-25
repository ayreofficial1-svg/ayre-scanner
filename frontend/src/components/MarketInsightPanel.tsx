import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import type { InsightContent } from '../types'

// ─────────────────────────────────────────────────────────────────────────────
// MarketInsightPanel
//
// Formerly the second half of SentimentPanel.tsx (the "Insights" CRUD form).
// Split out and renamed in Phase 1 of IMPLEMENTATION_SPEC_weekly_report_and_
// sentiment.md: this manages the content cards that power the Home tab's
// "Market Insight" hero carousel (AyreInsightCarousel in the Flutter app) —
// NOT the tab-local "Desk notes" section that used to also render the same
// data on the Insights tab (that duplicate render was removed from the app;
// the underlying data/endpoint stays, because the Home tab carousel still
// depends on it).
//
// The manual 0-100 sentiment number form that used to live alongside this in
// SentimentPanel.tsx has been removed outright — GET/POST /api/sentiment's
// manual write path no longer exists (Phase 1); the score is computed
// automatically from live market breadth from Phase 2 onward.
// ─────────────────────────────────────────────────────────────────────────────

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

export default function MarketInsightPanel() {
  const [insights, setInsights] = useState<InsightContent[]>([])
  const [insightForm, setInsightForm] = useState(EMPTY_INSIGHT)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    try {
      const insightRes = await fetch('/api/insights?all=1')
      const insightJson = await insightRes.json() as { insights: InsightContent[] }
      setInsights(insightJson.insights ?? [])
      setError(null)
    } catch {
      setError('Failed to load insights')
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

  return (
    <div className="section">
      <div className="section-header">
        <div>
          <div className="section-title">Market Insight</div>
          <div className="section-sub">Content cards shown in the mobile app's Home tab carousel</div>
        </div>
      </div>

      {error && <div className="error-bar">{error}</div>}

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

      {loading ? (
        <div className="empty-state">Loading insights...</div>
      ) : (
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
      )}
    </div>
  )
}

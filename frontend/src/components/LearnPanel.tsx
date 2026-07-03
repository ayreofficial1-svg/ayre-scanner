import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import type { LearnArticle } from '../types'

const EMPTY_FORM = { id: '', title: '', body: '', category: '', published: true }

export default function LearnPanel() {
  const [articles, setArticles] = useState<LearnArticle[]>([])
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState<string | null>(null)
  const [form, setForm]         = useState(EMPTY_FORM)
  const [saving, setSaving]     = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const res  = await fetch('/api/learn')
      const data = await res.json() as { articles: LearnArticle[] }
      setArticles(data.articles)
      setError(null)
    } catch {
      setError('Failed to load articles')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const edit = (a: LearnArticle) => {
    setForm({ id: a.id, title: a.title, body: a.body, category: a.category ?? '', published: a.published })
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const save = async (event: FormEvent) => {
    event.preventDefault()
    if (!form.title.trim() || !form.body.trim()) return
    setSaving(true)
    try {
      const res  = await fetch('/api/learn', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: form.id || undefined,
          title: form.title.trim(),
          body: form.body.trim(),
          category: form.category.trim() || null,
          published: form.published,
        }),
      })
      const data = await res.json().catch(() => ({})) as { error?: string }
      if (!res.ok) throw new Error(data.error || 'Failed to save article')
      setForm(EMPTY_FORM)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save article')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="section">
      <div className="section-header">
        <div>
          <div className="section-title">Learn</div>
          <div className="section-sub">Articles shown in the consumer app's Learn tab</div>
        </div>
      </div>

      <form className="debug-form" onSubmit={save} style={{ flexDirection: 'column', alignItems: 'stretch' }}>
        <label className="field">
          <span>Title</span>
          <input value={form.title} onChange={e => setForm(f => ({ ...f, title: e.target.value }))} required />
        </label>
        <label className="field">
          <span>Category (optional)</span>
          <input value={form.category} onChange={e => setForm(f => ({ ...f, category: e.target.value }))} placeholder="Basics" />
        </label>
        <label className="field">
          <span>Body</span>
          <textarea
            value={form.body}
            onChange={e => setForm(f => ({ ...f, body: e.target.value }))}
            rows={6}
            required
          />
        </label>
        <label className="field" style={{ flexDirection: 'row', alignItems: 'center', gap: '0.6rem' }}>
          <input
            type="checkbox"
            style={{ width: 'auto' }}
            checked={form.published}
            onChange={e => setForm(f => ({ ...f, published: e.target.checked }))}
          />
          <span style={{ textTransform: 'none', letterSpacing: 'normal' }}>Published</span>
        </label>
        <div style={{ display: 'flex', gap: '0.75rem' }}>
          <button className="rescan-btn" type="submit" disabled={saving}>
            {saving ? 'Saving...' : form.id ? 'Update Article' : 'Create Article'}
          </button>
          {form.id && (
            <button type="button" className="theme-btn" onClick={() => setForm(EMPTY_FORM)}>Cancel edit</button>
          )}
        </div>
      </form>

      {error && <div className="error-bar">{error}</div>}

      {loading ? (
        <div className="empty-state">Loading articles...</div>
      ) : articles.length === 0 ? (
        <div className="empty-state">No published articles yet.</div>
      ) : (
        <div className="backtest-results-list">
          {articles.map(a => (
            <div className="backtest-result" key={a.id}>
              <div className="backtest-result-main">
                <span className="card-sym">{a.title}</span>
                <span className="card-val dim">{a.category || '—'}</span>
                <button className="theme-btn" onClick={() => edit(a)}>Edit</button>
              </div>
              <div className="card-date">Updated {new Date(a.updated_at).toLocaleString('en-IN')}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

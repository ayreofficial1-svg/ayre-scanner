import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import type { LearnArticle } from '../types'

const EMPTY_FORM = {
  id: '',
  title: '',
  body: '',
  category: '',
  published: true,
  featured: false,
  pinned: false,
  display_order: 0,
  image_url: '',
  icon: 'auto_graph',
  tone: 'orange',
  start_at: '',
  end_at: '',
  tags: '',
}

export default function LearnPanel() {
  const [articles, setArticles] = useState<LearnArticle[]>([])
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState<string | null>(null)
  const [form, setForm]         = useState(EMPTY_FORM)
  const [saving, setSaving]     = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const res  = await fetch('/api/learn?all=1')
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
    setForm({
      id: a.id,
      title: a.title,
      body: a.body,
      category: a.category ?? '',
      published: a.enabled ?? a.published,
      featured: a.featured ?? false,
      pinned: a.pinned ?? false,
      display_order: a.display_order ?? 0,
      image_url: a.image_url ?? '',
      icon: a.icon ?? 'auto_graph',
      tone: a.tone ?? 'orange',
      start_at: a.start_at ?? '',
      end_at: a.end_at ?? '',
      tags: (a.tags ?? []).join(', '),
    })
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
          enabled: form.published,
          featured: form.featured,
          pinned: form.pinned,
          display_order: form.display_order,
          image_url: form.image_url.trim() || null,
          icon: form.icon,
          tone: form.tone,
          start_at: form.start_at || null,
          end_at: form.end_at || null,
          tags: form.tags.split(',').map(t => t.trim()).filter(Boolean),
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

  const uploadAsset = async (file: File | null) => {
    if (!file) return
    const body = new FormData()
    body.append('file', file)
    try {
      const res = await fetch('/api/uploads', { method: 'POST', body })
      const data = await res.json().catch(() => ({})) as { url?: string; error?: string }
      if (!res.ok || !data.url) throw new Error(data.error || 'Upload failed')
      setForm(f => ({ ...f, image_url: data.url! }))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed')
    }
  }

  const remove = async (id: string) => {
    try {
      const res = await fetch(`/api/learn/${id}`, { method: 'DELETE' })
      const data = await res.json().catch(() => ({})) as { error?: string }
      if (!res.ok) throw new Error(data.error || 'Failed to hide article')
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to hide article')
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
        <div className="debug-form">
          <label className="field inline-field">
            <span>Order</span>
            <input type="number" value={form.display_order} onChange={e => setForm(f => ({ ...f, display_order: Number(e.target.value) || 0 }))} />
          </label>
          <label className="field inline-field">
            <span>Icon</span>
            <select value={form.icon} onChange={e => setForm(f => ({ ...f, icon: e.target.value }))}>
              <option value="auto_graph">Chart</option>
              <option value="shield">Shield</option>
              <option value="speed">Speed</option>
              <option value="school">School</option>
              <option value="psychology">Psychology</option>
            </select>
          </label>
          <label className="field inline-field">
            <span>Tone</span>
            <select value={form.tone} onChange={e => setForm(f => ({ ...f, tone: e.target.value }))}>
              <option value="orange">Orange</option>
              <option value="mint">Mint</option>
              <option value="dark">Dark</option>
            </select>
          </label>
        </div>
        <label className="field">
          <span>Image URL</span>
          <input value={form.image_url} onChange={e => setForm(f => ({ ...f, image_url: e.target.value }))} placeholder="/uploads/example.webp" />
        </label>
        <label className="field">
          <span>Upload Image</span>
          <input type="file" accept="image/*" onChange={e => uploadAsset(e.target.files?.[0] ?? null)} />
        </label>
        <label className="field">
          <span>Tags</span>
          <input value={form.tags} onChange={e => setForm(f => ({ ...f, tags: e.target.value }))} placeholder="risk, basics" />
        </label>
        <div className="debug-form">
          <label className="field inline-field">
            <span>Start</span>
            <input type="datetime-local" value={form.start_at} onChange={e => setForm(f => ({ ...f, start_at: e.target.value }))} />
          </label>
          <label className="field inline-field">
            <span>End</span>
            <input type="datetime-local" value={form.end_at} onChange={e => setForm(f => ({ ...f, end_at: e.target.value }))} />
          </label>
        </div>
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
        <label className="field" style={{ flexDirection: 'row', alignItems: 'center', gap: '0.6rem' }}>
          <input type="checkbox" style={{ width: 'auto' }} checked={form.featured} onChange={e => setForm(f => ({ ...f, featured: e.target.checked }))} />
          <span style={{ textTransform: 'none', letterSpacing: 'normal' }}>Featured</span>
        </label>
        <label className="field" style={{ flexDirection: 'row', alignItems: 'center', gap: '0.6rem' }}>
          <input type="checkbox" style={{ width: 'auto' }} checked={form.pinned} onChange={e => setForm(f => ({ ...f, pinned: e.target.checked }))} />
          <span style={{ textTransform: 'none', letterSpacing: 'normal' }}>Pinned</span>
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
                <button className="theme-btn" onClick={() => remove(a.id)}>Hide</button>
              </div>
              <div className="card-date">Updated {new Date(a.updated_at).toLocaleString('en-IN')}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

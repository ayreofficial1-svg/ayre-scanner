import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import type { WeeklyReport, WeeklyReportOutcome, WeeklyReportStock } from '../types'

// ─────────────────────────────────────────────────────────────────────────────
// WeeklyReportPanel
//
// Phase 4 of IMPLEMENTATION_SPEC_weekly_report_and_sentiment.md. Modeled on
// SignalsPanel.tsx (list + add/edit form), following the same
// /api/weekly-report GET (list, ?all=1 for hidden) / POST (create or, with
// an "id", edit-in-place) / DELETE (soft-delete) shape every other
// admin-curated resource in this app uses.
//
// This is a NEW, additional tab (§A.2's correction note) — it does not
// replace or touch MarketInsightPanel ("Market Insight", formerly
// SentimentPanel), which manages a completely different feed (the Home
// tab's Insight carousel).
//
// The data here is admin-entered by design, not computed — there is no
// automatic target/stop-loss-hit detection anywhere in this codebase (see
// spec §A.4) — so this form's whole job is to make hand-entry fast: a
// date-range picker plus a repeatable row per stock (symbol, profit %,
// target/stop-loss toggle), saved as one POST that replaces the report's
// whole stocks list.
// ─────────────────────────────────────────────────────────────────────────────

type StockRow = {
  symbol: string
  profit_pct: string // kept as a string while editing so a bare "-" or "" mid-type isn't clobbered
  outcome: WeeklyReportOutcome
}

const EMPTY_ROW = (): StockRow => ({ symbol: '', profit_pct: '', outcome: 'target' })

function emptyForm() {
  return {
    id: '',
    week_start: '',
    week_end: '',
    enabled: true,
    display_order: 0,
    stocks: [EMPTY_ROW()],
  }
}

function formatRange(weekStart: string, weekEnd: string): string {
  const fmt = (iso: string) => {
    try {
      return new Date(`${iso}T00:00:00`).toLocaleDateString('en-IN', {
        day: 'numeric', month: 'short', year: 'numeric',
      })
    } catch {
      return iso
    }
  }
  return `${fmt(weekStart)} – ${fmt(weekEnd)}`
}

export default function WeeklyReportPanel() {
  const [reports, setReports] = useState<WeeklyReport[]>([])
  const [form, setForm] = useState(emptyForm)
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    try {
      const res  = await fetch('/api/weekly-report?all=1')
      const data = await res.json() as { reports: WeeklyReport[]; error?: string }
      if (!res.ok) throw new Error(data.error || 'Failed to load weekly reports')
      setReports(data.reports ?? [])
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load weekly reports')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const resetForm = () => setForm(emptyForm())

  const editReport = (report: WeeklyReport) => {
    setForm({
      id: report.id,
      week_start: report.week_start,
      week_end: report.week_end,
      enabled: report.enabled ?? true,
      display_order: report.display_order ?? 0,
      stocks: report.stocks.length
        ? report.stocks.map(s => ({
            symbol: s.symbol,
            profit_pct: String(s.profit_pct),
            outcome: s.outcome,
          }))
        : [EMPTY_ROW()],
    })
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const updateRow = (index: number, patch: Partial<StockRow>) => {
    setForm(f => ({
      ...f,
      stocks: f.stocks.map((row, i) => (i === index ? { ...row, ...patch } : row)),
    }))
  }

  const addRow = () => setForm(f => ({ ...f, stocks: [...f.stocks, EMPTY_ROW()] }))

  const removeRow = (index: number) =>
    setForm(f => ({
      ...f,
      stocks: f.stocks.length > 1 ? f.stocks.filter((_, i) => i !== index) : f.stocks,
    }))

  const saveReport = async (event: FormEvent) => {
    event.preventDefault()
    setError(null)

    const rows = form.stocks
      .map(row => ({
        symbol: row.symbol.trim().toUpperCase(),
        profit_pct: Number(row.profit_pct),
        outcome: row.outcome,
      }))
      .filter(row => row.symbol.length > 0)

    if (!form.week_start || !form.week_end) {
      setError('Week start and week end are required')
      return
    }
    if (rows.length === 0) {
      setError('At least one stock row is required')
      return
    }
    if (rows.some(row => Number.isNaN(row.profit_pct))) {
      setError('Every stock row needs a numeric profit %')
      return
    }

    setSubmitting(true)
    try {
      const res = await fetch('/api/weekly-report', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: form.id || undefined,
          week_start: form.week_start,
          week_end: form.week_end,
          enabled: form.enabled,
          display_order: form.display_order,
          stocks: rows satisfies WeeklyReportStock[],
        }),
      })
      const data = await res.json().catch(() => ({})) as { error?: string }
      if (!res.ok) throw new Error(data.error || 'Failed to save weekly report')
      resetForm()
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save weekly report')
    } finally {
      setSubmitting(false)
    }
  }

  const hideReport = async (id: string) => {
    try {
      const res  = await fetch(`/api/weekly-report/${id}`, { method: 'DELETE' })
      const data = await res.json().catch(() => ({})) as { error?: string }
      if (!res.ok) throw new Error(data.error || 'Failed to hide weekly report')
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to hide weekly report')
    }
  }

  return (
    <div className="section">
      <div className="section-header">
        <div>
          <div className="section-title">Weekly Report</div>
          <div className="section-sub">
            Hand-entered weekly performance shown on the app's Signals tab. No auto-calculation — enter each week's profitable stocks yourself.
          </div>
        </div>
      </div>

      {error && <div className="error-bar">{error}</div>}

      <form className="debug-form" onSubmit={saveReport}>
        <label className="field inline-field">
          <span>Week start</span>
          <input
            type="date"
            value={form.week_start}
            onChange={e => setForm(f => ({ ...f, week_start: e.target.value }))}
            required
          />
        </label>
        <label className="field inline-field">
          <span>Week end</span>
          <input
            type="date"
            value={form.week_end}
            onChange={e => setForm(f => ({ ...f, week_end: e.target.value }))}
            required
          />
        </label>
        <label className="field inline-field">
          <span>Order</span>
          <input
            type="number"
            value={form.display_order}
            onChange={e => setForm(f => ({ ...f, display_order: Number(e.target.value) || 0 }))}
          />
        </label>
        <label className="field" style={{ flexDirection: 'row', alignItems: 'center', gap: '0.6rem' }}>
          <input
            type="checkbox"
            style={{ width: 'auto' }}
            checked={form.enabled}
            onChange={e => setForm(f => ({ ...f, enabled: e.target.checked }))}
          />
          <span style={{ textTransform: 'none', letterSpacing: 'normal' }}>Enabled</span>
        </label>

        <div className="backtest-results-list" style={{ width: '100%' }}>
          {form.stocks.map((row, index) => (
            <div className="backtest-result" key={index}>
              <div className="backtest-result-main">
                <input
                  value={row.symbol}
                  onChange={e => updateRow(index, { symbol: e.target.value })}
                  placeholder="RELIANCE"
                  style={{ maxWidth: '10rem' }}
                />
                <input
                  type="number"
                  step="0.01"
                  value={row.profit_pct}
                  onChange={e => updateRow(index, { profit_pct: e.target.value })}
                  placeholder="Profit %"
                  style={{ maxWidth: '8rem' }}
                />
                <select
                  value={row.outcome}
                  onChange={e => updateRow(index, { outcome: e.target.value as WeeklyReportOutcome })}
                >
                  <option value="target">Target hit</option>
                  <option value="stop_loss">Stop-loss hit</option>
                </select>
                <button type="button" className="theme-btn" onClick={() => removeRow(index)}>
                  Remove row
                </button>
              </div>
            </div>
          ))}
        </div>

        <button type="button" className="theme-btn" onClick={addRow}>+ Add stock row</button>
        <button className="rescan-btn" type="submit" disabled={submitting}>
          {submitting ? 'Saving...' : form.id ? 'Update Report' : 'Create Report'}
        </button>
        {form.id && <button type="button" className="theme-btn" onClick={resetForm}>Cancel edit</button>}
      </form>

      {loading ? (
        <div className="empty-state">Loading weekly reports...</div>
      ) : reports.length === 0 ? (
        <div className="empty-state">No weekly reports yet.</div>
      ) : (
        <div className="backtest-results-list">
          {reports.map(report => (
            <div className="backtest-result" key={report.id}>
              <div className="backtest-result-main">
                <span className="card-sym">{formatRange(report.week_start, report.week_end)}</span>
                <span className="card-val dim">
                  {report.stocks.length} stock{report.stocks.length !== 1 ? 's' : ''}
                  {report.enabled === false ? ' · hidden' : ''}
                </span>
                <button className="theme-btn" onClick={() => editReport(report)}>Edit</button>
                <button className="theme-btn" onClick={() => hideReport(report.id)}>Hide</button>
              </div>
              <div className="details-value">
                {report.stocks.map(s => (
                  <span key={s.symbol} className={`result-badge ${s.outcome === 'target' ? 'signal' : 'watchlist'}`} style={{ marginRight: '0.5rem' }}>
                    {s.symbol} {s.profit_pct >= 0 ? '+' : ''}{s.profit_pct}% · {s.outcome === 'target' ? 'Target' : 'Stop-loss'}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

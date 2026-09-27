import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import type { WeeklyReport, WeeklyReportOutcome } from '../types'
import { inr } from '../utils'
import StockPicker from './StockPicker'
import DatePicker from './DatePicker'

// ─────────────────────────────────────────────────────────────────────────────
// WeeklyReportPanel
//
// One past weekly report = a date range plus a row per recommended stock:
// stock, entry price, exit price, profit %, date of recommendation. Nothing
// else is shown here — the older optional fields (company name, trade
// label, duration, ₹ P&L, per-row enabled) still round-trip through the API
// untouched but are no longer edited on this screen.
//
// `outcome` ("target"/"stop_loss") is still required by the API/Flutter
// parser, so it's derived automatically from the sign of profit % (>=0 is a
// target hit, negative is a stop-loss hit) rather than asked for by hand.
// ─────────────────────────────────────────────────────────────────────────────

type StockRow = {
  symbol: string
  entry_price: string
  exit_price: string
  profit_pct: string
  date_of_recommendation: string
}

const EMPTY_ROW = (): StockRow => ({
  symbol: '',
  entry_price: '',
  exit_price: '',
  profit_pct: '',
  date_of_recommendation: '',
})

function emptyForm() {
  return {
    id: '',
    week_start: '',
    week_end: '',
    stocks: [EMPTY_ROW()],
  }
}

function outcomeFor(profitPct: number): WeeklyReportOutcome {
  return profitPct >= 0 ? 'target' : 'stop_loss'
}

function formatDate(iso: string): string {
  try {
    return new Date(`${iso}T00:00:00`).toLocaleDateString('en-IN', {
      day: 'numeric', month: 'short', year: 'numeric',
    })
  } catch {
    return iso
  }
}

function formatRange(weekStart: string, weekEnd: string): string {
  return `${formatDate(weekStart)} – ${formatDate(weekEnd)}`
}

export default function WeeklyReportPanel() {
  const [reports, setReports] = useState<WeeklyReport[]>([])
  const [form, setForm] = useState(emptyForm)
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Tracks whether the admin has picked Week End themselves. Until they do,
  // picking Week Start also fills Week End with the same date (a sensible
  // starting point for a 5-7 day range) — they can still change it after.
  const [weekEndTouched, setWeekEndTouched] = useState(false)

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

  const resetForm = () => { setForm(emptyForm()); setWeekEndTouched(false) }

  const editReport = (report: WeeklyReport) => {
    setWeekEndTouched(true)
    setForm({
      id: report.id,
      week_start: report.week_start,
      week_end: report.week_end,
      stocks: report.stocks.length
        ? report.stocks.map(s => ({
            symbol: s.symbol,
            entry_price: s.entry_price != null ? String(s.entry_price) : '',
            exit_price: s.exit_price != null ? String(s.exit_price) : '',
            profit_pct: String(s.profit_pct),
            date_of_recommendation: s.date_of_recommendation ?? '',
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

    const numOrUndefined = (v: string) => (v.trim() === '' ? undefined : Number(v))

    const rows = form.stocks
      .map(row => {
        const profit_pct = Number(row.profit_pct)
        return {
          symbol: row.symbol.trim().toUpperCase(),
          profit_pct,
          outcome: outcomeFor(profit_pct),
          entry_price: numOrUndefined(row.entry_price),
          exit_price: numOrUndefined(row.exit_price),
          date_of_recommendation: row.date_of_recommendation || undefined,
        }
      })
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
      setError('Every stock row needs a numeric percentage return')
      return
    }
    if (rows.some(row => row.entry_price !== undefined && Number.isNaN(row.entry_price))) {
      setError('Entry price must be numeric')
      return
    }
    if (rows.some(row => row.exit_price !== undefined && Number.isNaN(row.exit_price))) {
      setError('Exit price must be numeric')
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
          stocks: rows,
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
        <div className="section-title">Weekly Report</div>
      </div>

      {error && <div className="error-bar">{error}</div>}

      <form className="clean-form" onSubmit={saveReport}>
        <div className="clean-form-grid">
          <DatePicker
            label="Week start"
            value={form.week_start}
            onChange={v => setForm(f => ({
              ...f,
              week_start: v,
              // Give Week End a sensible starting date until the admin picks
              // their own — makes the second field faster to fill in.
              week_end: weekEndTouched ? f.week_end : v,
            }))}
            required
          />
          <DatePicker
            label="Week end"
            value={form.week_end}
            onChange={v => { setWeekEndTouched(true); setForm(f => ({ ...f, week_end: v })) }}
            required
          />
        </div>

        <div className="report-rows">
          {form.stocks.map((row, index) => (
            <div className="report-row" key={index}>
              <div className="report-row-grid">
                <StockPicker
                  label="Stock"
                  value={row.symbol}
                  onChange={symbol => updateRow(index, { symbol })}
                />
                <label className="field">
                  <span>Entry price</span>
                  <input
                    type="number"
                    step="0.01"
                    value={row.entry_price}
                    onChange={e => updateRow(index, { entry_price: e.target.value })}
                    placeholder="₹"
                  />
                </label>
                <label className="field">
                  <span>Exit price</span>
                  <input
                    type="number"
                    step="0.01"
                    value={row.exit_price}
                    onChange={e => updateRow(index, { exit_price: e.target.value })}
                    placeholder="₹"
                  />
                </label>
                <label className="field">
                  <span>Return %</span>
                  <input
                    type="number"
                    step="0.01"
                    value={row.profit_pct}
                    onChange={e => updateRow(index, { profit_pct: e.target.value })}
                    placeholder="e.g. 4.2 or -1.8"
                  />
                </label>
                <DatePicker
                  label="Date of recommendation"
                  value={row.date_of_recommendation}
                  onChange={v => updateRow(index, { date_of_recommendation: v })}
                />
              </div>
              {form.stocks.length > 1 && (
                <button type="button" className="report-row-remove" onClick={() => removeRow(index)} aria-label="Remove stock row">
                  ✕
                </button>
              )}
            </div>
          ))}
        </div>

        <div className="clean-form-footer">
          <button type="button" className="theme-btn" onClick={addRow}>+ Add stock</button>
          <div className="clean-form-actions">
            {form.id && <button type="button" className="theme-btn" onClick={resetForm}>Cancel</button>}
            <button className="rescan-btn" type="submit" disabled={submitting}>
              {submitting ? 'Saving...' : form.id ? 'Update report' : 'Create report'}
            </button>
          </div>
        </div>
      </form>

      {loading ? (
        <div className="empty-state">Loading weekly reports...</div>
      ) : reports.length === 0 ? (
        <div className="empty-state">No weekly reports yet.</div>
      ) : (
        <div className="report-list">
          {reports.map(report => (
            <div className="report-card" key={report.id}>
              <div className="report-card-header">
                <span className="card-sym">{formatRange(report.week_start, report.week_end)}</span>
                <span className="card-val dim">
                  {report.stocks.length} stock{report.stocks.length !== 1 ? 's' : ''}
                  {report.enabled === false ? ' · hidden' : ''}
                </span>
                <div className="report-card-actions">
                  <button className="theme-btn" onClick={() => editReport(report)}>Edit</button>
                  <button className="theme-btn" onClick={() => hideReport(report.id)}>Hide</button>
                </div>
              </div>
              <div className="report-card-stocks">
                {report.stocks.map((s, i) => (
                  <div className="report-stock-chip" key={`${s.symbol}-${i}`}>
                    <span className="card-sym">{s.symbol}</span>
                    <span className={`card-val ${s.profit_pct >= 0 ? 'g' : 'r'}`}>
                      {s.profit_pct >= 0 ? '+' : ''}{s.profit_pct}%
                    </span>
                    {(s.entry_price != null || s.exit_price != null) && (
                      <span className="card-val dim">
                        {s.entry_price != null ? inr(s.entry_price) : '—'} → {s.exit_price != null ? inr(s.exit_price) : '—'}
                      </span>
                    )}
                    {s.date_of_recommendation && (
                      <span className="card-val dim">{formatDate(s.date_of_recommendation)}</span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import type { WeeklyReport, WeeklyReportOutcome } from '../types'
import { inr, pct } from '../utils'
import StockPicker from './StockPicker'
import DatePicker from './DatePicker'

// ─────────────────────────────────────────────────────────────────────────────
// WeeklyReportPanel
//
// One past weekly report = a date range plus a row per recommended stock:
// stock, entry price, exit price, profit per share, date of recommendation.
// Nothing else is shown here — the older optional fields (company name,
// trade label, duration, per-row enabled) still round-trip through the API
// untouched but are no longer edited on this screen.
//
// Return % is no longer a manual field — it is always derived from Entry
// price and Exit price (server-side, authoritatively, in POST
// /api/weekly-report) so it can never drift from the two prices the admin
// actually entered. Profit per share (₹) IS still manual — it is the ₹
// figure per share, which the two prices alone can't tell us (lot size,
// brokerage, etc. are the admin's call) — and is optional.
//
// `outcome` ("target"/"stop_loss") is still required by the API/Flutter
// parser, so it's derived automatically from the sign of the computed
// return % (>=0 is a target hit, negative is a stop-loss hit) rather than
// asked for by hand.
// ─────────────────────────────────────────────────────────────────────────────

type StockRow = {
  symbol: string
  entry_price: string
  exit_price: string
  pnl_amount: string
  date_of_recommendation: string
}

const EMPTY_ROW = (): StockRow => ({
  symbol: '',
  entry_price: '',
  exit_price: '',
  pnl_amount: '',
  date_of_recommendation: '',
})

function outcomeFor(profitPct: number): WeeklyReportOutcome {
  return profitPct >= 0 ? 'target' : 'stop_loss'
}

// Mirrors the backend's own calculation (main.py::api_weekly_report_add) so
// the admin sees the same return % here, before saving, that the Flutter
// app will end up showing.
function computeProfitPct(entryPrice: number, exitPrice: number): number {
  return ((exitPrice - entryPrice) / entryPrice) * 100
}

function emptyForm() {
  return {
    id: '',
    week_start: '',
    week_end: '',
    stocks: [EMPTY_ROW()],
  }
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
            pnl_amount: s.pnl_amount != null ? String(s.pnl_amount) : '',
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
        const entry_price = Number(row.entry_price)
        const exit_price = Number(row.exit_price)
        const profit_pct = computeProfitPct(entry_price, exit_price)
        return {
          symbol: row.symbol.trim().toUpperCase(),
          entry_price,
          exit_price,
          profit_pct,
          outcome: outcomeFor(profit_pct),
          pnl_amount: numOrUndefined(row.pnl_amount),
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
    if (rows.some(row => Number.isNaN(row.entry_price) || row.entry_price === 0)) {
      setError('Entry price is required and must be a non-zero number, for every stock row')
      return
    }
    if (rows.some(row => Number.isNaN(row.exit_price))) {
      setError('Exit price is required and must be numeric, for every stock row')
      return
    }
    if (rows.some(row => row.pnl_amount !== undefined && Number.isNaN(row.pnl_amount))) {
      setError('Profit per share must be numeric')
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
          {form.stocks.map((row, index) => {
            const entry = Number(row.entry_price)
            const exit = Number(row.exit_price)
            const hasBothPrices =
              row.entry_price.trim() !== '' && row.exit_price.trim() !== '' &&
              !Number.isNaN(entry) && !Number.isNaN(exit) && entry !== 0
            const livePct = hasBothPrices ? computeProfitPct(entry, exit) : null

            return (
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
                    <span>Return % (auto)</span>
                    <input
                      type="text"
                      value={livePct == null ? '—' : pct(livePct)}
                      readOnly
                      disabled
                      title="Calculated automatically from Entry price and Exit price"
                    />
                  </label>
                  <label className="field">
                    <span>Profit per share (₹)</span>
                    <input
                      type="number"
                      step="0.01"
                      value={row.pnl_amount}
                      onChange={e => updateRow(index, { pnl_amount: e.target.value })}
                      placeholder="₹ (optional)"
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
            )
          })}
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
                      {pct(s.profit_pct)}
                    </span>
                    {s.pnl_amount != null && (
                      <span className={`card-val ${s.pnl_amount >= 0 ? 'g' : 'r'}`}>
                        {inr(s.pnl_amount)} / share
                      </span>
                    )}
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

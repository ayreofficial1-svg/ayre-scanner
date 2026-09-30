import type { ScanProgressInfo, ScanProgressStage } from '../types'

// Real-time scan progress. Replaces the old spinner (ScanRing) on the Scanner
// and Backtest pages. Every figure comes from /api/scan/progress, which is fed
// by the same counters that produce the Railway log line
// "400/501 processed — 396 valid, 4 to retry". Nothing here is a timer or an
// estimate; when no data has arrived yet the bar is shown as indeterminate.

interface Props {
  progress: ScanProgressInfo | null
  kind: 'live' | 'backtest'
}

const STEPS: { key: ScanProgressStage; label: string }[] = [
  { key: 'fetch',   label: 'Fetch' },
  { key: 'retry',   label: 'Retry' },
  { key: 'analyse', label: 'Evaluate' },
]
const STAGE_ORDER: Record<string, number> = {
  idle: -1, fetch: 0, retry: 1, analyse: 2, done: 3, error: 3,
}

function fmtElapsed(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  const m = Math.floor(s / 60)
  return `${String(m).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

function fmtDate(iso: string | null): string {
  if (!iso) return ''
  try {
    return new Date(iso + 'T00:00:00').toLocaleDateString('en-IN', {
      day: 'numeric', month: 'short', year: 'numeric',
    })
  } catch {
    return iso
  }
}

type StepState = 'done' | 'active' | 'pending' | 'skipped'

function stepState(step: ScanProgressStage, p: ScanProgressInfo): StepState {
  const current = STAGE_ORDER[p.stage] ?? -1
  const mine = STAGE_ORDER[step]
  if (mine === current && p.stage !== 'done') return 'active'
  if (mine < current) {
    // No retry pass ever started → nothing needed retrying.
    return step === 'retry' && p.retry_pass === 0 ? 'skipped' : 'done'
  }
  return 'pending'
}

const STEP_MARK: Record<StepState, string> = {
  done: '✓', active: '●', pending: '○', skipped: '–',
}

export default function ScanProgress({ progress, kind }: Props) {
  // Until the scanner has reported a symbol universe there is nothing real to
  // show, so fall through to the indeterminate "starting" state.
  const p = progress && progress.active && !(progress.stage === 'fetch' && progress.total === 0)
    ? progress
    : null

  const title = kind === 'backtest'
    ? `Backtest${p?.target_date ? ` · ${fmtDate(p.target_date)}` : ''}`
    : 'Scanning Nifty 500'

  // ── Waiting for the first real numbers ─────────────────────────────────────
  if (!p) {
    return (
      <div className="sp-panel" role="status">
        <div className="sp-head">
          <span className="sp-title"><span className="sp-dot" />{title}</span>
        </div>
        <div className="sp-headline">
          <span className="sp-waiting">Starting scan…</span>
        </div>
        <div className="sp-track"><div className="sp-fill indeterminate" /></div>
        <div className="sp-status">Live progress appears as soon as the scanner reports in</div>
      </div>
    )
  }

  const inFetch   = p.stage === 'fetch'
  const inRetry   = p.stage === 'retry'
  const inAnalyse = p.stage === 'analyse'

  // Headline counter follows the stage that is actually running.
  let done  = p.processed
  let total = p.total
  if (inRetry)   { done = p.stage_done; total = p.stage_total }
  if (inAnalyse) { done = p.analysed;   total = p.analyse_total }

  const pct = Math.max(0, Math.min(100, p.percent))

  let statusLine: string
  if (inFetch) {
    statusLine = `${p.processed}/${p.total} processed · ${p.valid} valid · ${p.to_retry} to retry`
  } else if (inRetry) {
    statusLine = `Retry pass ${p.retry_pass}: ${p.stage_done}/${p.stage_total} re-checked · ${p.valid} valid · ${p.to_retry} to retry`
  } else if (inAnalyse) {
    statusLine = `${p.analysed}/${p.analyse_total} evaluated · ${p.valid} valid · ${p.not_scanned} not scanned`
  } else {
    statusLine = `${p.processed}/${p.total} processed · ${p.valid} valid`
  }

  // While running, failing symbols are counted under "To retry"; only stocks
  // Fyers has no history for are already final.
  const notScannedNote = p.no_data > 0 ? `${p.no_data} no history` : ''

  return (
    <div className="sp-panel" role="status">
      <div className="sp-head">
        <span className="sp-title"><span className="sp-dot" />{title}</span>
        <span className="sp-elapsed">{p.stage_label} · {fmtElapsed(p.elapsed_seconds)}</span>
      </div>

      <div className="sp-headline">
        <span className="sp-count">
          {done}<small>/{total}</small>
        </span>
        <span className="sp-pct">{pct.toFixed(0)}%</span>
      </div>

      <div
        className="sp-track"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(pct)}
        aria-label={p.stage_label}
      >
        <div className="sp-fill" style={{ width: `${pct}%` }} />
      </div>

      <div className="sp-status">{statusLine}</div>

      <div className="sp-stats">
        <div className="stat-cell">
          <div className="stat-num g">{p.valid}</div>
          <div className="stat-lbl">Valid</div>
        </div>
        <div className="stat-cell">
          <div className="stat-num gold">{p.to_retry}</div>
          <div className="stat-lbl">To retry</div>
        </div>
        <div className="stat-cell">
          <div className="stat-num">{p.not_scanned}</div>
          <div className="stat-lbl">Not scanned</div>
          {notScannedNote && <div className="sp-note">{notScannedNote}</div>}
        </div>
      </div>

      <div className="sp-steps">
        {STEPS.map(step => {
          const state = stepState(step.key, p)
          return (
            <span key={step.key} className={`sp-step ${state}`}>
              <i>{STEP_MARK[state]}</i>{step.label}
            </span>
          )
        })}
      </div>
    </div>
  )
}

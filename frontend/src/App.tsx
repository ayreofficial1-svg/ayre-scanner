import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import type {
  BacktestDebugResult, DebugStatus, ScanProgressInfo, ScanProgressResponse, ScanState,
} from './types'
import Clock from './components/Clock'
import ScanRing from './components/ScanRing'
import ScanProgress from './components/ScanProgress'
import SignalCard from './components/SignalCard'
import WatchlistTable from './components/WatchlistTable'
import SignalsPanel from './components/SignalsPanel'
import MarketInsightPanel from './components/MarketInsightPanel'
import LearnPanel from './components/LearnPanel'
import WeeklyReportPanel from './components/WeeklyReportPanel'
import DatePicker from './components/DatePicker'

type View = 'scanner' | 'backtest' | 'signals' | 'insights' | 'learn' | 'weeklyReport'
type AuthState = 'checking' | 'authenticated' | 'login'
type BacktestFilter = 'all' | 'signal' | 'watchlist' | 'none' | 'no_data'
type TradeReadyTimes = Record<string, string>

const TRADE_READY_TIMES_KEY = 'ayre.tradeReadyTimes.v1'
const MONTHS: Record<string, number> = {
  jan: 0,
  feb: 1,
  mar: 2,
  apr: 3,
  may: 4,
  jun: 5,
  jul: 6,
  aug: 7,
  sep: 8,
  oct: 9,
  nov: 10,
  dec: 11,
}

const DEFAULT_STATE: ScanState = {
  scanning:        false,
  scan_time:       null,
  total_scanned:   0,
  signals:         [],
  watchlist_items: [],
  error:           null,
}

// ─────────────────────────────────────────────────────────────────────────────
// Separate type for backtest-specific UI state.
// Keeps backtest lifecycle fields from bleeding into ScanState.
// ─────────────────────────────────────────────────────────────────────────────
interface BacktestUIState {
  loading: boolean          // true while a backtest is running on the server (shared by all users)
  jobId:   string | null    // running job id (if any)
  state:   ScanState        // what the page shows (last result, or a "running" overlay)
  result:  ScanState | null // last completed result, kept so "running" never wipes it
}

// scanning:true until the first load from the server finishes, so the page
// shows the loading ring instead of flashing an empty "no results" prompt.
const DEFAULT_BACKTEST_UI: BacktestUIState = {
  loading: false,
  jobId:   null,
  state:   { ...DEFAULT_STATE, scanning: true },
  result:  null,
}

interface BacktestServerState {
  unchanged?:        boolean
  revision:          number
  selected_date?:    string | null
  filter?:           BacktestFilter
  running?:          boolean
  running_job?:      { job_id: string; date: string; created_at: string } | null
  result?:           ScanState | null
  result_unchanged?: boolean
  error?:            string | null
}

const todayIso = () => new Date().toISOString().slice(0, 10)

async function readJsonResponse(res: Response) {
  const text = await res.text()
  if (!text) return null
  try {
    return JSON.parse(text)
  } catch {
    throw new Error(text.slice(0, 240) || `HTTP ${res.status}`)
  }
}

function loadTradeReadyTimes(): TradeReadyTimes {
  try {
    const raw = localStorage.getItem(TRADE_READY_TIMES_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? parsed as TradeReadyTimes : {}
  } catch {
    return {}
  }
}

function saveTradeReadyTimes(times: TradeReadyTimes) {
  localStorage.setItem(TRADE_READY_TIMES_KEY, JSON.stringify(times))
}

function explicitReadyTimestamp(signal: {
  trade_ready_at?: string
  ready_at?: string
  became_trade_ready_at?: string
  logged_at?: string
  alert_time?: string
}): string | null {
  return signal.trade_ready_at
      ?? signal.ready_at
      ?? signal.became_trade_ready_at
      ?? signal.logged_at
      ?? signal.alert_time
      ?? null
}

function parseTimestamp(value: string | null | undefined): Date | null {
  if (!value) return null
  const trimmed = value.trim()
  if (!trimmed || trimmed.includes('Running') || trimmed.includes('queued')) return null

  const scanMatch = trimmed.match(/(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?/)
  if (scanMatch) {
    const [, day, month, year, hour, minute, second] = scanMatch
    const monthIndex = MONTHS[month.toLowerCase()]
    if (monthIndex !== undefined) {
      return new Date(
        Number(year),
        monthIndex,
        Number(day),
        Number(hour),
        Number(minute),
        Number(second ?? 0),
      )
    }
  }

  const parsed = new Date(trimmed)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

function normalizeTimestamp(value: string | null | undefined): string | null {
  const parsed = parseTimestamp(value)
  return parsed ? parsed.toISOString() : null
}

function formatTradeReadyTimestamp(value: string | null | undefined): string | null {
  const parsed = parseTimestamp(value)
  if (!parsed) return null

  // Always render in IST (Asia/Kolkata), regardless of the viewer's own
  // device/browser timezone.
  const date = parsed.toLocaleDateString('en-IN', {
    timeZone: 'Asia/Kolkata',
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  })
  const time = parsed.toLocaleTimeString('en-IN', {
    timeZone: 'Asia/Kolkata',
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  }).toUpperCase()

  return `${date} — ${time}`
}

export default function App() {
  const [theme, setTheme]           = useState<string>(() => localStorage.getItem('theme') ?? 'dark')
  const [auth, setAuth]             = useState<AuthState>('checking')
  const [view, setView]             = useState<View>('scanner')
  const [state, setState]           = useState<ScanState>(DEFAULT_STATE)
  const [tradeReadyTimes, setTradeReadyTimes] = useState<TradeReadyTimes>(loadTradeReadyTimes)
  const [backtestDate, setBacktestDate] = useState(todayIso)
  const [loginError, setLoginError] = useState<string | null>(null)
  const [authConfigured, setAuthConfigured] = useState(true)

  // ── Backtest state is now managed in one object so partial updates never
  //    clobber the completed result. ─────────────────────────────────────────
  const [backtest, setBacktest] = useState<BacktestUIState>(DEFAULT_BACKTEST_UI)

  // ── Backtest filter lives outside the backtest state so changing it doesn't
  //    reset results. ────────────────────────────────────────────────────────
  const [backtestFilter, setBacktestFilter] = useState<BacktestFilter>('all')

  // Live scan progress (scheduled/manual scan + backtest). Fed by
  // /api/scan/progress — an in-memory read on the server, no Fyers calls.
  const [progress, setProgress] = useState<ScanProgressResponse | null>(null)
  const progressWasActive = useRef({ live: false, backtest: false })

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('theme', theme)
  }, [theme])

  const toggleTheme = () => setTheme(t => t === 'dark' ? 'light' : 'dark')

  const checkAuth = useCallback(async () => {
    try {
      const res  = await fetch('/api/auth/session')
      const data = await res.json() as { authenticated: boolean; configured: boolean }
      setAuthConfigured(data.configured)
      setAuth(data.authenticated ? 'authenticated' : 'login')
    } catch {
      setAuth('login')
    }
  }, [])

  useEffect(() => { checkAuth() }, [checkAuth])

  const poll = useCallback(async () => {
    if (auth !== 'authenticated') return
    try {
      const res = await fetch('/api/results')
      if (res.status === 401) { setAuth('login'); return }
      const data = await res.json() as ScanState
      setState(data)
    } catch { /* keep last state */ }
  }, [auth])

  useEffect(() => {
    poll()
    const id = setInterval(poll, 10_000)
    return () => clearInterval(id)
  }, [poll])

  useEffect(() => {
    setTradeReadyTimes(prev => {
      const next: TradeReadyTimes = {}

      // Only ever use an explicit, backend-supplied Trade Ready timestamp
      // (or a previously-seen explicit value cached across polls). Never
      // fall back to the current scan time — that's the time the scanner
      // happened to run, not the time the stock actually became Trade
      // Ready, and showing it would be misleading.
      for (const signal of state.signals) {
        const explicitTimestamp = normalizeTimestamp(explicitReadyTimestamp(signal))
        next[signal.symbol] = explicitTimestamp ?? prev[signal.symbol] ?? ''
      }

      saveTradeReadyTimes(next)
      return next
    })
  }, [state.scan_time, state.signals])

  const triggerRescan = async () => {
    const res = await fetch('/api/rescan', { method: 'POST' })
    if (!res.ok) {
      const data = await res.json().catch(() => ({})) as { message?: string; status?: string }
      // Surface the rejection reason (e.g. market closed) in the error bar.
      setState(prev => ({
        ...prev,
        error: data.message ?? `Rescan rejected (${res.status})`,
      }))
      return
    }
    poll()
    setTimeout(() => { pollProgress() }, 400)
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Shared Backtest state.
  //
  // The server holds ONE backtest state for every user (selected date, filter,
  // running job, last completed result) and persists it to disk. The page
  // simply mirrors it: load on login / refresh, then poll cheaply
  // (?since=<revision>) so other people's runs and changes show up here.
  // Nothing in this flow calls Fyers.
  // ─────────────────────────────────────────────────────────────────────────
  const btRevision  = useRef(-1)
  const btResultJob = useRef<string | null>(null)

  const applyBacktestServerState = useCallback((data: BacktestServerState) => {
    btRevision.current = data.revision
    if (data.selected_date) setBacktestDate(data.selected_date)
    if (data.filter)        setBacktestFilter(data.filter)
    if (data.result?.job_id) btResultJob.current = String(data.result.job_id)

    setBacktest(prev => {
      const result: ScanState | null = data.result_unchanged
        ? prev.result
        : (data.result ?? null)
      const base = result ?? DEFAULT_STATE

      if (data.running && data.running_job) {
        return {
          loading: true,
          jobId:   data.running_job.job_id,
          result,
          state: {
            ...base,
            scanning:  true,
            scan_time: `Running backtest for ${data.running_job.date}…`,
            error:     null,
          },
        }
      }
      return {
        loading: false,
        jobId:   null,
        result,
        state: {
          ...base,
          scanning: false,
          error:    data.error ?? base.error ?? null,
        },
      }
    })
  }, [])

  const syncBacktest = useCallback(async () => {
    if (auth !== 'authenticated') return
    try {
      const qs = new URLSearchParams()
      if (btRevision.current >= 0) qs.set('since', String(btRevision.current))
      if (btResultJob.current)     qs.set('result_job', btResultJob.current)
      const res = await fetch(`/api/backtest/state?${qs.toString()}`)
      if (res.status === 401) { setAuth('login'); return }
      const data = await readJsonResponse(res) as BacktestServerState | null
      if (!res.ok || !data) return
      if (data.unchanged) return
      applyBacktestServerState(data)
    } catch {
      // Keep whatever is on screen; if the very first load failed, stop the spinner.
      if (btRevision.current < 0) {
        setBacktest(prev => (
          prev.state.scanning && !prev.loading
            ? { ...prev, state: { ...prev.state, scanning: false } }
            : prev
        ))
      }
    }
  }, [auth, applyBacktestServerState])

  // Load on login/refresh, then poll: fast while a backtest is running, slow otherwise.
  const backtestRunning = backtest.loading
  useEffect(() => {
    if (auth !== 'authenticated') return
    syncBacktest()
    const id = setInterval(syncBacktest, backtestRunning ? 3_000 : 10_000)
    return () => clearInterval(id)
  }, [auth, syncBacktest, backtestRunning])

  // ─────────────────────────────────────────────────────────────────────────
  // Live scan progress.
  //
  // Polled every second while a scan/backtest is running and every few seconds
  // otherwise (so scheduled scans that start on their own are noticed within a
  // moment). When a run finishes, the real results are fetched immediately
  // rather than waiting for the slower regular polls.
  // ─────────────────────────────────────────────────────────────────────────
  const pollProgress = useCallback(async () => {
    if (auth !== 'authenticated') return
    try {
      const res = await fetch('/api/scan/progress', { cache: 'no-store' })
      if (res.status === 401) { setAuth('login'); return }
      if (!res.ok) return
      const data = await res.json() as ScanProgressResponse
      setProgress(data)

      const was = progressWasActive.current
      progressWasActive.current = { live: data.live.active, backtest: data.backtest.active }
      if (was.live && !data.live.active)         poll()
      if (was.backtest && !data.backtest.active) syncBacktest()
    } catch { /* keep last progress */ }
  }, [auth, poll, syncBacktest])

  const anyScanActive = !!(progress?.live.active || progress?.backtest.active)
  useEffect(() => {
    if (auth !== 'authenticated') return
    pollProgress()
    const id = setInterval(pollProgress, anyScanActive ? 1_000 : 3_000)
    return () => clearInterval(id)
  }, [auth, pollProgress, anyScanActive])

  // Explicit, shared changes to the page state (persisted on the server).
  const saveBacktestSetting = async (patch: { date?: string; filter?: BacktestFilter }) => {
    try {
      const res = await fetch('/api/backtest/state', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(patch),
      })
      if (res.status === 401) { setAuth('login'); return }
    } catch { /* next sync reconciles with the server */ }
  }

  const changeBacktestDate = (value: string) => {
    setBacktestDate(value)
    if (value) saveBacktestSetting({ date: value })   // '' = mid-edit, don't persist
  }

  const changeBacktestFilter = (value: BacktestFilter) => {
    setBacktestFilter(value)
    saveBacktestSetting({ filter: value })
  }

  const submitBacktest = async (event: FormEvent) => {
    event.preventDefault()
    if (backtest.loading) return

    try {
      const res = await fetch('/api/backtest/scan', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ date: backtestDate }),
      })
      if (res.status === 401) { setAuth('login'); return }

      const data = await readJsonResponse(res)
      if (!res.ok) {
        // 409 = someone else's backtest is already running: join it instead.
        if (res.status === 409 && data?.state) {
          applyBacktestServerState(data.state as BacktestServerState)
          return
        }
        throw new Error(data?.error ?? 'Backtest failed')
      }

      // Show "running" right away; the shared sync loop delivers the result
      // (to this user and everyone else) when the job finishes.
      setBacktest(prev => ({
        ...prev,
        loading: true,
        jobId:   data?.job_id ?? null,
        state: {
          ...(prev.result ?? DEFAULT_STATE),
          scanning:  true,
          scan_time: `Running backtest for ${backtestDate}…`,
          error:     null,
        },
      }))
      setBacktestFilter('all')
      syncBacktest()
    } catch (error) {
      setBacktest(prev => ({
        ...prev,
        state: {
          ...prev.state,
          scanning: false,
          error: error instanceof Error ? error.message : 'Backtest failed',
        },
      }))
    }
  }

  const login = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setLoginError(null)
    const form     = new FormData(event.currentTarget)
    const username = String(form.get('username') ?? '')
    const password = String(form.get('password') ?? '')
    try {
      const res  = await fetch('/api/auth/login', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ username, password }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || 'Login failed')
      setAuth('authenticated')
      poll()
    } catch (error) {
      setLoginError(error instanceof Error ? error.message : 'Login failed')
    }
  }

  const logout = async () => {
    await fetch('/api/auth/logout', { method: 'POST' })
    setAuth('login')
  }

  if (auth === 'checking') {
    return <Frame><div className="login-shell"><ScanRing /></div></Frame>
  }

  if (auth === 'login') {
    return (
      <Frame>
        <main className="login-shell">
          <form className="login-panel" onSubmit={login}>
            <div>
              <h1>Ayre.</h1>
              <p className="login-copy">Sign in to continue.</p>
            </div>
            {!authConfigured && (
              <div className="error-bar">Set SCANNER_USERS in Railway before logging in.</div>
            )}
            {loginError && <div className="error-bar">{loginError}</div>}
            <label className="field">
              <span>Username</span>
              <input name="username" autoComplete="username" required />
            </label>
            <label className="field">
              <span>Password</span>
              <input name="password" type="password" autoComplete="current-password" required />
            </label>
            <button className="rescan-btn login-btn" type="submit">Login</button>
          </form>
        </main>
      </Frame>
    )
  }

  // ── Derive the active state for the current view. ─────────────────────────
  const activeState = view === 'backtest' ? backtest.state : state
  const { scan_time, total_scanned, signals, watchlist_items, error } = activeState
  const backtestLoading = backtest.loading || !!progress?.backtest.active

  // A scan counts as running if either the page state or the live progress
  // tracker says so — so scheduled scans that start on their own show up
  // within a second instead of waiting for the next results poll.
  const activeProgress = view === 'backtest' ? progress?.backtest ?? null : progress?.live ?? null
  const scanning = activeState.scanning || !!activeProgress?.active

  return (
    <Frame>
      <div className="wrap">
        <header>
          <div className="header-top">
            <h1>Ayre.</h1>
            <div className="header-right">
              <Clock />
              <button className="theme-btn" onClick={toggleTheme}>{theme === 'dark' ? 'Light' : 'Dark'}</button>
              <button className="theme-btn" onClick={logout}>Logout</button>
              {view === 'scanner' && (
                <button className="rescan-btn" onClick={triggerRescan} disabled={scanning}>
                  {scanning ? 'Scanning...' : 'Rescan'}
                </button>
              )}
            </div>
          </div>

          <nav className="nav-tabs" aria-label="Primary">
            <button className={view === 'scanner'   ? 'active' : ''} onClick={() => setView('scanner')}>Scanner</button>
            <button className={view === 'backtest'  ? 'active' : ''} onClick={() => setView('backtest')}>Backtest</button>
            <button className={view === 'signals'   ? 'active' : ''} onClick={() => setView('signals')}>Signals</button>
            <button className={view === 'insights' ? 'active' : ''} onClick={() => setView('insights')}>Market Insight</button>
            <button className={view === 'learn'     ? 'active' : ''} onClick={() => setView('learn')}>Learn</button>
            <button className={view === 'weeklyReport' ? 'active' : ''} onClick={() => setView('weeklyReport')}>Weekly Report</button>
          </nav>

          {(view === 'scanner' || view === 'backtest') && (
            <div className="header-meta">
              <span className="scan-label">{scan_time ? `Last scan - ${scan_time}` : 'Awaiting scan...'}</span>
              <span className="scan-label">Nifty 500 · Daily candles · 3-10 day holds</span>
            </div>
          )}
        </header>

        {view === 'signals' && <SignalsPanel />}
        {view === 'insights' && <MarketInsightPanel />}
        {view === 'learn' && <LearnPanel />}
        {view === 'weeklyReport' && <WeeklyReportPanel />}

        {view === 'backtest' && (
          <form className="debug-form" onSubmit={submitBacktest}>
            <DatePicker
              label="Date"
              value={backtestDate}
              onChange={changeBacktestDate}
              max={todayIso()}
              required
            />
            <button className="rescan-btn" type="submit" disabled={backtestLoading}>
              {backtestLoading ? 'Running...' : 'Run Backtest'}
            </button>
          </form>
        )}

        {(view === 'scanner' || view === 'backtest') && (
          <>
            <div className="stats-row">
              <div className="stat-cell">
                <div className="stat-num">{scanning ? '-' : total_scanned || '-'}</div>
                <div className="stat-lbl">Scanned</div>
              </div>
              <div className="stat-cell">
                <div className="stat-num g">{scanning ? '-' : signals.length}</div>
                <div className="stat-lbl">Trade Ready</div>
              </div>
              <div className="stat-cell">
                <div className="stat-num gold">{scanning ? '-' : watchlist_items.length}</div>
                <div className="stat-lbl">Watchlist</div>
              </div>
            </div>

            {error && <div className="error-bar">{error}</div>}

            {/* ── Backtest summary bar: show once results are available. ─────── */}
            {view === 'backtest' && (activeState.backtest_results?.length ?? 0) > 0 && activeState.debug && (
              <div className="backtest-summary">
                <span>{activeState.total_scanned || 0} evaluated</span>
                <span>{signals.length} trade ready</span>
                <span>{watchlist_items.length} watchlist</span>
                <span>{activeState.debug.status_counts?.none ?? 0} rejected</span>
                {(activeState.debug.no_data_symbols ?? 0) > 0 && (
                  <span>{activeState.debug.no_data_symbols} no data</span>
                )}
                {activeState.debug.resolved_date && activeState.debug.requested_date !== activeState.debug.resolved_date && (
                  <span className="resolved-note">
                    Resolved to {formatDisplayDate(activeState.debug.resolved_date)}
                  </span>
                )}
              </div>
            )}

            <Results
              state={activeState}
              scanning={scanning}
              progress={activeProgress}
              backtestRunning={backtestLoading}
              view={view}
              tradeReadyTimes={tradeReadyTimes}
              backtestFilter={backtestFilter}
              onBacktestFilter={changeBacktestFilter}
            />
          </>
        )}
      </div>
    </Frame>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Results
//
// In scanner mode: always shows Trade Ready cards + Watchlist table.
// In backtest mode: if a job has run (backtest_results exists and non-empty),
//   shows the filterable debug table — which mirrors debug_run.py's output.
//   Otherwise falls back to an empty/loading state.
//
// KEY FIX: the Scanner-style fallthrough is removed from backtest mode.
//   Backtest always renders BacktestResults (which handles its own loading
//   state via the `scanning` prop). There is no ambiguous branching.
// ─────────────────────────────────────────────────────────────────────────────
function Results({
  state,
  scanning,
  progress,
  backtestRunning,
  view,
  tradeReadyTimes,
  backtestFilter,
  onBacktestFilter,
}: {
  state: ScanState
  scanning: boolean
  progress: ScanProgressInfo | null
  backtestRunning: boolean
  view: View
  tradeReadyTimes: TradeReadyTimes
  backtestFilter: BacktestFilter
  onBacktestFilter: (filter: BacktestFilter) => void
}) {
  const { signals, watchlist_items } = state

  if (view === 'backtest') {
    // Always render the BacktestResults component in backtest mode.
    // It handles the scanning / empty / populated states internally.
    return (
      <BacktestResults
        scanning={scanning}
        progress={progress}
        running={backtestRunning}
        results={state.backtest_results ?? []}
        filter={backtestFilter}
        onFilter={onBacktestFilter}
      />
    )
  }

  // ── Scanner mode ───────────────────────────────────────────────────────────
  return (
    <>
      <div className="section">
        <div className="section-header">
          <span className="section-title">Trade Ready</span>
          <span className="section-sub">
            {scanning ? '...' : `${signals.length} setup${signals.length !== 1 ? 's' : ''}`}
          </span>
        </div>
        {scanning ? (
          <ScanProgress progress={progress} kind="live" />
        ) : signals.length > 0 ? (
          <div className="trade-ready-list">
            {signals.map(s => (
              <SignalCard
                key={s.symbol}
                signal={s}
                readyAt={formatTradeReadyTimestamp(tradeReadyTimes[s.symbol])}
              />
            ))}
          </div>
        ) : (
          <div className="empty-state">
            No trade-ready setups. Watchlist stocks move here when MACD confirms.
          </div>
        )}
      </div>

      <div className="section">
        <div className="section-header">
          <span className="section-title">Watchlist</span>
          <span className="section-sub">
            {scanning ? '...' : `${watchlist_items.length} stock${watchlist_items.length !== 1 ? 's' : ''} awaiting MACD`}
          </span>
        </div>
        {!scanning && <WatchlistTable items={watchlist_items} />}
      </div>
    </>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// BacktestResults
//
// Mirrors debug_run.py terminal output: every evaluated stock with its status,
// stage, reason, and key metric values. Filterable by status.
//
// States:
//   scanning=true, results=[]  → shows spinner (job in progress)
//   scanning=false, results=[] → shows "Run a backtest" prompt
//   scanning=false, results>0  → shows filterable table
// ─────────────────────────────────────────────────────────────────────────────
function BacktestResults({
  scanning,
  progress,
  running,
  results,
  filter,
  onFilter,
}: {
  scanning: boolean
  progress: ScanProgressInfo | null
  running: boolean          // a backtest is genuinely in flight (vs. the initial page load)
  results: BacktestDebugResult[]
  filter: BacktestFilter
  onFilter: (filter: BacktestFilter) => void
}) {
  const counts = useMemo(() => ({
    all:      results.length,
    signal:   results.filter(r => r.status === 'signal').length,
    watchlist:results.filter(r => r.status === 'watchlist').length,
    none:     results.filter(r => r.status === 'none').length,
    no_data:  results.filter(r => r.status === 'no_data').length,
  }), [results])

  const visible = useMemo(() => {
    const filtered = filter === 'all' ? results : results.filter(r => r.status === filter)
    return [...filtered].sort((a, b) => {
      const order = { signal: 0, watchlist: 1, none: 2, no_data: 3 }
      const ao = order[a.status as keyof typeof order] ?? 4
      const bo = order[b.status as keyof typeof order] ?? 4
      if (ao !== bo) return ao - bo
      // Within the same status, sort by change_pct desc (mirrors debug_run.py)
      const aChg = (a.values?.change_pct as number) ?? 0
      const bChg = (b.values?.change_pct as number) ?? 0
      if (bChg !== aChg) return bChg - aChg
      return a.symbol.localeCompare(b.symbol)
    })
  }, [filter, results])

  return (
    <div className="section">
      <div className="section-header">
        <span className="section-title">Backtest Results</span>
        <span className="section-sub">
          {scanning
            ? 'Running…'
            : results.length > 0
              ? `${visible.length} of ${results.length} stocks`
              : 'No results yet'}
        </span>
      </div>

      {scanning ? (
        // Before the first server load `scanning` is true with nothing running:
        // keep the plain spinner for that, the progress panel for real runs.
        progress?.active || running
          ? <ScanProgress progress={progress} kind="backtest" />
          : <ScanRing />
      ) : results.length === 0 ? (
        <div className="empty-state">
          Select a date and click Run Backtest to see results.
        </div>
      ) : (
        <>
          <div className="result-filters" role="tablist" aria-label="Backtest result filters">
            <FilterButton label="All"         value="all"       active={filter} count={counts.all}       onFilter={onFilter} />
            <FilterButton label="Trade Ready" value="signal"    active={filter} count={counts.signal}    onFilter={onFilter} />
            <FilterButton label="Watchlist"   value="watchlist" active={filter} count={counts.watchlist} onFilter={onFilter} />
            <FilterButton label="Rejected"    value="none"      active={filter} count={counts.none}      onFilter={onFilter} />
            {counts.no_data > 0 && (
              <FilterButton label="No Data" value="no_data" active={filter} count={counts.no_data} onFilter={onFilter} />
            )}
          </div>
          <div className="backtest-results-list">
            {visible.map(result => (
              <BacktestResultRow key={result.symbol} result={result} />
            ))}
          </div>
        </>
      )}
    </div>
  )
}

function FilterButton({
  label, value, active, count, onFilter,
}: {
  label: string
  value: BacktestFilter
  active: BacktestFilter
  count: number
  onFilter: (filter: BacktestFilter) => void
}) {
  return (
    <button
      type="button"
      className={active === value ? 'active' : ''}
      onClick={() => onFilter(value)}
    >
      <span>{label}</span>
      <strong>{count}</strong>
    </button>
  )
}

function BacktestResultRow({ result }: { result: BacktestDebugResult }) {
  const values = result.values ?? {}
  const nse    = `https://www.nseindia.com/get-quotes/equity?symbol=${result.symbol}`

  // Field names match what evaluate_debug returns in the values dict.
  const close  = formatValue(values.close,                                2)
  const sma44  = formatValue(values.sma44 ?? values.sma44_today,         2)
  const macd   = formatValue(values.macd  ?? values.macd_cur,            4)
  const signal = formatValue(values.macd_signal ?? values.signal_cur,    4)
  const hist   = formatValue(values.macd_histogram ?? values.histogram,  4)
  const slope  = formatPercent(values.pct_slope)
  const chgPct = values.change_pct != null ? formatPercent(values.change_pct) : null

  return (
    <article className={`backtest-result ${statusClass(result.status)}`}>
      <div className="backtest-result-main">
        <a href={nse} target="_blank" rel="noreferrer">{result.symbol}</a>
        <span className={`result-badge ${statusClass(result.status)}`}>
          {statusLabel(result.status)}
        </span>
        <span className="result-stage">{formatStage(result.stage)}</span>
        {chgPct && (
          <span className={`result-chg ${(values.change_pct as number) >= 0 ? 'g' : 'r'}`}>
            {chgPct}
          </span>
        )}
      </div>
      {result.category && <p className="result-category">{result.category}</p>}
      <p className="result-explanation">
        {highlightTechnical(result.explanation ?? result.reason)}
      </p>
      {result.explanation && (
        <details className="result-tech">
          <summary>Technical detail</summary>
          <p className="result-reason">{result.reason}</p>
        </details>
      )}
      <div className="result-metrics">
        <Metric label="Close"     value={close}  />
        <Metric label="SMA44"     value={sma44}  />
        <Metric label="Pct slope" value={slope}  />
        <Metric label="MACD"      value={macd}   />
        <Metric label="Signal"    value={signal} />
        <Metric label="Histogram" value={hist}   />
        <Metric label="Weekly ↑"  value={formatBool(values.weekly_rising)} />
      </div>
    </article>
  )
}

// Technical figures inside a plain-English explanation: ₹ prices, percentages,
// MACD-style decimals, SMA44/MACD terms, check codes (C1a, C1b/C1c) and day
// counts. They are wrapped in <span class="tech"> so they read brighter than
// the surrounding sentence. Display only — the text itself is unchanged.
const TECH_TOKEN =
  /(₹\s?\d+(?:,\d{2,3})*(?:\.\d+)?|[+-]?\d+(?:,\d{3})*(?:\.\d+)?%|[+-]?\d+\.\d+x?|\b\d+x\b|\b\d+\s(?:trading\s)?days?\b|\bSMA\d+\b|\bMACD\b|\bC[123][abc]?(?:\/C[123][abc]?)*\b)/g

function highlightTechnical(text: string): ReactNode {
  return text.split(TECH_TOKEN).map((part, i) =>
    i % 2 === 1 ? <span key={i} className="tech">{part}</span> : part
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <span>
      <small>{label}</small>
      <strong>{value}</strong>
    </span>
  )
}

// ─── Formatting helpers ───────────────────────────────────────────────────────

function statusClass(status: DebugStatus) {
  if (status === 'signal')    return 'signal'
  if (status === 'watchlist') return 'watchlist'
  if (status === 'none')      return 'none'
  if (status === 'no_data')   return 'none'
  return 'error'
}

function statusLabel(status: DebugStatus) {
  if (status === 'signal')    return 'Trade Ready'
  if (status === 'watchlist') return 'Watchlist'
  if (status === 'none')      return 'Rejected'
  if (status === 'no_data')   return 'No Data'
  return 'Error'
}

function formatStage(stage: string) {
  return stage.replace(/_/g, ' ')
}

function formatValue(value: unknown, decimals: number): string {
  if (value === null || value === undefined || value === '') return '-'
  const n = Number(value)
  if (!Number.isFinite(n)) return String(value)
  return n.toFixed(decimals)
}

function formatPercent(value: unknown): string {
  if (value === null || value === undefined || value === '') return '-'
  const n = Number(value)
  if (!Number.isFinite(n)) return String(value)
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`
}

function formatBool(value: unknown): string {
  if (value === true)  return 'Yes'
  if (value === false) return 'No'
  return '-'
}

function formatDisplayDate(iso: string): string {
  try {
    return new Date(iso + 'T00:00:00').toLocaleDateString('en-IN', {
      day: 'numeric', month: 'short', year: 'numeric',
    })
  } catch {
    return iso
  }
}

function Frame({ children }: { children: ReactNode }) {
  return (
    <>
      <div className="noise" />
      <div className="ambient" />
      {children}
    </>
  )
}

import { useCallback, useEffect, useMemo, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import type { BacktestDebugResult, DebugStatus, ScanState } from './types'
import Clock from './components/Clock'
import ScanRing from './components/ScanRing'
import SignalCard from './components/SignalCard'
import WatchlistTable from './components/WatchlistTable'

type View = 'scanner' | 'backtest'
type AuthState = 'checking' | 'authenticated' | 'login'
type BacktestFilter = 'all' | 'signal' | 'watchlist' | 'none'

const DEFAULT_STATE: ScanState = {
  scanning:        false,
  scan_time:       null,
  total_scanned:   0,
  signals:         [],
  watchlist_items: [],
  error:           null,
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

const wait = (ms: number) => new Promise(resolve => window.setTimeout(resolve, ms))

export default function App() {
  const [theme, setTheme] = useState<string>(() => localStorage.getItem('theme') ?? 'dark')
  const [auth, setAuth] = useState<AuthState>('checking')
  const [view, setView] = useState<View>('scanner')
  const [state, setState] = useState<ScanState>(DEFAULT_STATE)
  const [backtestState, setBacktestState] = useState<ScanState>(DEFAULT_STATE)
  const [backtestDate, setBacktestDate] = useState(todayIso)
  const [backtestFilter, setBacktestFilter] = useState<BacktestFilter>('all')
  const [backtestLoading, setBacktestLoading] = useState(false)
  const [loginError, setLoginError] = useState<string | null>(null)
  const [authConfigured, setAuthConfigured] = useState(true)

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('theme', theme)
  }, [theme])

  const toggleTheme = () => setTheme(t => t === 'dark' ? 'light' : 'dark')

  const checkAuth = useCallback(async () => {
    try {
      const res = await fetch('/api/auth/session')
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
      if (res.status === 401) {
        setAuth('login')
        return
      }
      const data = await res.json() as ScanState
      setState(data)
    } catch { /* keep last state */ }
  }, [auth])

  useEffect(() => {
    poll()
    const id = setInterval(poll, 10_000)
    return () => clearInterval(id)
  }, [poll])

  const triggerRescan = async () => {
    await fetch('/api/rescan', { method: 'POST' })
    poll()
  }

  const submitBacktest = async (event: FormEvent) => {
    event.preventDefault()
    setBacktestLoading(true)
    setBacktestState(s => ({ ...s, scanning: true, error: null }))
    try {
      const res = await fetch('/api/backtest/scan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ date: backtestDate }),
      })
      if (res.status === 401) {
        setAuth('login')
        return
      }
      const data = await readJsonResponse(res)
      if (!res.ok) throw new Error(data?.error || 'Backtest failed')
      if (data?.job_id && data?.status === 'running') {
        setBacktestState(data as ScanState)
        await pollBacktestJob(data.job_id)
        return
      }
      console.info('Backtest response', {
        total_scanned: data.total_scanned,
        signals: Array.isArray(data.signals) ? data.signals.length : 'missing',
        watchlist_items: Array.isArray(data.watchlist_items) ? data.watchlist_items.length : 'missing',
        debug: data.debug,
      })
      setBacktestState(data as ScanState)
    } catch (error) {
      setBacktestState(s => ({
        ...s,
        scanning: false,
        error: error instanceof Error ? error.message : 'Backtest failed',
      }))
    } finally {
      setBacktestLoading(false)
    }
  }

  const pollBacktestJob = async (jobId: string) => {
    while (true) {
      await wait(2_000)
      const res = await fetch(`/api/backtest/status/${jobId}`)
      if (res.status === 401) {
        setAuth('login')
        return
      }
      const data = await readJsonResponse(res)
      if (!res.ok) throw new Error(data?.error || 'Backtest failed')
      setBacktestState(data as ScanState)
      if (!data?.scanning && data?.status !== 'running') {
        console.info('Backtest response', {
          total_scanned: data.total_scanned,
          signals: Array.isArray(data.signals) ? data.signals.length : 'missing',
          watchlist_items: Array.isArray(data.watchlist_items) ? data.watchlist_items.length : 'missing',
          debug: data.debug,
        })
        return
      }
    }
  }

  const login = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setLoginError(null)
    const form = new FormData(event.currentTarget)
    const username = String(form.get('username') ?? '')
    const password = String(form.get('password') ?? '')
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
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
              <h1>Swing Scanner.</h1>
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

  const activeState = view === 'backtest' ? backtestState : state
  const { scanning, scan_time, total_scanned, signals, watchlist_items, error } = activeState

  return (
    <Frame>
      <div className="wrap">
        <header>
          <div className="header-top">
            <h1>Swing Scanner.</h1>
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
            <button className={view === 'scanner' ? 'active' : ''} onClick={() => setView('scanner')}>Scanner</button>
            <button className={view === 'backtest' ? 'active' : ''} onClick={() => setView('backtest')}>Backtest</button>
          </nav>

          <div className="header-meta">
            <span className="scan-label">{scan_time ? `Last scan - ${scan_time}` : 'Awaiting scan...'}</span>
            <span className="scan-label">Nifty 500 · Daily candles · 3-10 day holds</span>
          </div>
        </header>

        {view === 'backtest' && (
          <form className="debug-form" onSubmit={submitBacktest}>
            <label className="field inline-field">
              <span>Date</span>
              <input type="date" value={backtestDate} max={todayIso()} onChange={e => setBacktestDate(e.target.value)} required />
            </label>
            <button className="rescan-btn" type="submit" disabled={backtestLoading}>
              {backtestLoading ? 'Running...' : 'Run Backtest'}
            </button>
          </form>
        )}

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

        {view === 'backtest' && activeState.debug && (
          <div className="backtest-summary">
            <span>{activeState.total_scanned || 0} evaluated</span>
            <span>{signals.length} trade ready</span>
            <span>{watchlist_items.length} watchlist</span>
            <span>{activeState.debug.status_counts?.none ?? 0} rejected</span>
          </div>
        )}

        <Results state={activeState} view={view} backtestFilter={backtestFilter} onBacktestFilter={setBacktestFilter} />
      </div>
    </Frame>
  )
}

function Results({
  state,
  view,
  backtestFilter,
  onBacktestFilter,
}: {
  state: ScanState
  view: View
  backtestFilter: BacktestFilter
  onBacktestFilter: (filter: BacktestFilter) => void
}) {
  const { scanning, signals, watchlist_items } = state
  const backtestResults = state.backtest_results ?? []

  if (view === 'backtest' && (scanning || backtestResults.length > 0)) {
    return (
      <BacktestResults
        scanning={scanning}
        results={backtestResults}
        filter={backtestFilter}
        onFilter={onBacktestFilter}
      />
    )
  }

  return (
    <>
      <div className="section">
        <div className="section-header">
          <span className="section-title">Trade Ready</span>
          <span className="section-sub">{scanning ? '...' : `${signals.length} setup${signals.length !== 1 ? 's' : ''}`}</span>
        </div>
        {scanning ? (
          <ScanRing />
        ) : signals.length > 0 ? (
          <div className="cards-grid">
            {signals.map(s => <SignalCard key={s.symbol} signal={s} />)}
          </div>
        ) : (
          <div className="empty-state">No trade-ready setups. Watchlist stocks move here when MACD confirms.</div>
        )}
      </div>

      <div className="section">
        <div className="section-header">
          <span className="section-title">Watchlist</span>
          <span className="section-sub">{scanning ? '...' : `${watchlist_items.length} stock${watchlist_items.length !== 1 ? 's' : ''} awaiting MACD`}</span>
        </div>
        {!scanning && <WatchlistTable items={watchlist_items} />}
      </div>
    </>
  )
}

function BacktestResults({
  scanning,
  results,
  filter,
  onFilter,
}: {
  scanning: boolean
  results: BacktestDebugResult[]
  filter: BacktestFilter
  onFilter: (filter: BacktestFilter) => void
}) {
  const counts = useMemo(() => ({
    all: results.length,
    signal: results.filter(r => r.status === 'signal').length,
    watchlist: results.filter(r => r.status === 'watchlist').length,
    none: results.filter(r => r.status === 'none').length,
  }), [results])

  const visible = useMemo(() => {
    const filtered = filter === 'all' ? results : results.filter(r => r.status === filter)
    return [...filtered].sort((a, b) => {
      const order = { signal: 0, watchlist: 1, none: 2 }
      const aOrder = order[a.status as keyof typeof order] ?? 3
      const bOrder = order[b.status as keyof typeof order] ?? 3
      if (aOrder !== bOrder) return aOrder - bOrder
      return a.symbol.localeCompare(b.symbol)
    })
  }, [filter, results])

  return (
    <div className="section">
      <div className="section-header">
        <span className="section-title">Backtest Results</span>
        <span className="section-sub">{scanning ? '...' : `${visible.length} of ${results.length} stocks`}</span>
      </div>

      {scanning ? (
        <ScanRing />
      ) : results.length > 0 ? (
        <>
          <div className="result-filters" role="tablist" aria-label="Backtest result filters">
            <FilterButton label="All" value="all" active={filter} count={counts.all} onFilter={onFilter} />
            <FilterButton label="Trade Ready" value="signal" active={filter} count={counts.signal} onFilter={onFilter} />
            <FilterButton label="Watchlist" value="watchlist" active={filter} count={counts.watchlist} onFilter={onFilter} />
            <FilterButton label="Rejected" value="none" active={filter} count={counts.none} onFilter={onFilter} />
          </div>
          <div className="backtest-results-list">
            {visible.map(result => <BacktestResultRow key={result.symbol} result={result} />)}
          </div>
        </>
      ) : (
        <div className="empty-state">No backtest results.</div>
      )}
    </div>
  )
}

function FilterButton({
  label,
  value,
  active,
  count,
  onFilter,
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
  const nse = `https://www.nseindia.com/get-quotes/equity?symbol=${result.symbol}`
  const close = formatValue(values.close, 2)
  const sma44 = formatValue(values.sma44 ?? values.sma44_today, 2)
  const macd = formatValue(values.macd ?? values.macd_cur, 4)
  const signal = formatValue(values.macd_signal ?? values.signal_cur, 4)
  const slope = formatPercent(values.pct_slope)

  return (
    <article className={`backtest-result ${statusClass(result.status)}`}>
      <div className="backtest-result-main">
        <a href={nse} target="_blank" rel="noreferrer">{result.symbol}</a>
        <span className={`result-badge ${statusClass(result.status)}`}>{statusLabel(result.status)}</span>
        <span className="result-stage">{formatStage(result.stage)}</span>
      </div>
      <p>{result.reason}</p>
      <div className="result-metrics">
        <Metric label="Close" value={close} />
        <Metric label="SMA44" value={sma44} />
        <Metric label="Pct slope" value={slope} />
        <Metric label="MACD" value={macd} />
        <Metric label="Signal" value={signal} />
        <Metric label="Weekly" value={formatBool(values.weekly_rising)} />
      </div>
    </article>
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

function statusClass(status: DebugStatus) {
  if (status === 'signal') return 'signal'
  if (status === 'watchlist') return 'watchlist'
  if (status === 'none') return 'none'
  return 'error'
}

function statusLabel(status: DebugStatus) {
  if (status === 'signal') return 'Trade Ready'
  if (status === 'watchlist') return 'Watchlist'
  if (status === 'none') return 'Rejected'
  return 'Error'
}

function formatStage(stage: string) {
  return stage.replace(/_/g, ' ')
}

function formatValue(value: unknown, decimals: number) {
  if (value === null || value === undefined || value === '') return '-'
  const numberValue = Number(value)
  if (!Number.isFinite(numberValue)) return String(value)
  return numberValue.toFixed(decimals)
}

function formatPercent(value: unknown) {
  if (value === null || value === undefined || value === '') return '-'
  const numberValue = Number(value)
  if (!Number.isFinite(numberValue)) return String(value)
  return `${numberValue.toFixed(2)}%`
}

function formatBool(value: unknown) {
  if (value === true) return 'Yes'
  if (value === false) return 'No'
  return '-'
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

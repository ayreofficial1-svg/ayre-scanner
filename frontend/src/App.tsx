import { useCallback, useEffect, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import type { ScanState } from './types'
import Clock from './components/Clock'
import ScanRing from './components/ScanRing'
import SignalCard from './components/SignalCard'
import WatchlistTable from './components/WatchlistTable'

type View = 'scanner' | 'backtest'
type AuthState = 'checking' | 'authenticated' | 'login'

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

        <Results state={activeState} />
      </div>
    </Frame>
  )
}

function Results({ state }: { state: ScanState }) {
  const { scanning, signals, watchlist_items } = state

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

function Frame({ children }: { children: ReactNode }) {
  return (
    <>
      <div className="noise" />
      <div className="ambient" />
      {children}
    </>
  )
}

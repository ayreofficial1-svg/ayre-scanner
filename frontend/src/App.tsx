import { useState, useEffect, useCallback } from 'react'
import type { ScanState } from './types'
import Clock from './components/Clock'
import ScanRing from './components/ScanRing'
import SignalCard from './components/SignalCard'
import WatchlistTable from './components/WatchlistTable'

const DEFAULT_STATE: ScanState = {
  scanning:        false,
  scan_time:       null,
  total_scanned:   0,
  signals:         [],
  watchlist_items: [],
  error:           null,
}

export default function App() {
  const [theme,  setTheme]  = useState<string>(() => localStorage.getItem('theme') ?? 'dark')
  const [state,  setState]  = useState<ScanState>(DEFAULT_STATE)

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('theme', theme)
  }, [theme])

  const toggleTheme = () => setTheme(t => t === 'dark' ? 'light' : 'dark')

  const poll = useCallback(async () => {
    try {
      const res  = await fetch('/api/results')
      const data = await res.json() as ScanState
      setState(data)
    } catch { /* network error — keep last state */ }
  }, [])

  useEffect(() => {
    poll()
    const id = setInterval(poll, 10_000)
    return () => clearInterval(id)
  }, [poll])

  const triggerRescan = async () => {
    await fetch('/api/rescan', { method: 'POST' })
    poll()
  }

  const { scanning, scan_time, total_scanned, signals, watchlist_items, error } = state

  return (
    <>
      <div className="noise" />
      <div className="ambient" />

      <div className="wrap">
        {/* ── Header ── */}
        <header>
          <div className="header-top">
            <h1>Swing Scanner.</h1>
            <div className="header-right">
              <Clock />
              <button className="theme-btn" onClick={toggleTheme}>
                {theme === 'dark' ? 'Light' : 'Dark'}
              </button>
              <button className="rescan-btn" onClick={triggerRescan} disabled={scanning}>
                {scanning ? 'Scanning…' : 'Rescan'}
              </button>
            </div>
          </div>
          <div className="header-meta">
            <span className="scan-label">
              {scan_time ? `Last scan — ${scan_time}` : 'Awaiting scan…'}
            </span>
            <span className="scan-label">Nifty 500 · Daily candles · 3–10 day holds</span>
          </div>
        </header>

        {/* ── Stats ── */}
        <div className="stats-row">
          <div className="stat-cell">
            <div className="stat-num">{scanning ? '—' : total_scanned || '—'}</div>
            <div className="stat-lbl">Scanned</div>
          </div>
          <div className="stat-cell">
            <div className="stat-num g">{scanning ? '—' : signals.length}</div>
            <div className="stat-lbl">Trade Ready</div>
          </div>
          <div className="stat-cell">
            <div className="stat-num gold">{scanning ? '—' : watchlist_items.length}</div>
            <div className="stat-lbl">Watchlist</div>
          </div>
        </div>

        {/* ── Error ── */}
        {error && <div className="error-bar">{error}</div>}

        {/* ── Trade Ready ── */}
        <div className="section">
          <div className="section-header">
            <span className="section-title">Trade Ready</span>
            <span className="section-sub">
              {scanning ? '…' : `${signals.length} setup${signals.length !== 1 ? 's' : ''}`}
            </span>
          </div>
          {scanning ? (
            <ScanRing />
          ) : signals.length > 0 ? (
            <div className="cards-grid">
              {signals.map(s => <SignalCard key={s.symbol} signal={s} />)}
            </div>
          ) : (
            <div className="empty-state">
              No trade-ready setups today. Watchlist stocks move here when MACD confirms.
            </div>
          )}
        </div>

        {/* ── Watchlist ── */}
        <div className="section">
          <div className="section-header">
            <span className="section-title">Watchlist</span>
            <span className="section-sub">
              {scanning
                ? '…'
                : `${watchlist_items.length} stock${watchlist_items.length !== 1 ? 's' : ''} awaiting MACD`}
            </span>
          </div>
          {!scanning && <WatchlistTable items={watchlist_items} />}
        </div>
      </div>
    </>
  )
}

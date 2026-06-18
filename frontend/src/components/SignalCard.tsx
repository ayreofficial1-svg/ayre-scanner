import type { Signal } from '../types'
import { inr, num, pct } from '../utils'
import InsightMetrics from './InsightMetrics'
import { useState } from 'react'

interface Props { signal: Signal }

const labelize = (value?: string | null): string =>
  value ? value.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()) : 'Standard'

const crossAge = (s: Signal): string => {
  if (s.cross_type === 'imminent') return 'Imminent'
  if (s.cross_type === 'pending') return 'Pending'
  if (s.crossover_bars_ago == null) return 'Confirmed'
  if (s.crossover_bars_ago === 0) return 'Today'
  if (s.crossover_bars_ago === 1) return 'Yesterday'
  return `${s.crossover_bars_ago} bars ago`
}

export default function SignalCard({ signal: s }: Props) {
  const nse = `https://www.nseindia.com/get-quotes/equity?symbol=${s.symbol}`
  const [showDetails, setShowDetails] = useState(false)

  return (
    <div className={`card${s.promoted ? ' promoted' : ''}`}>
      <div className="card-top">
        <div>
          {s.promoted && (
            <div className="promoted-tag">
              Promoted from watchlist{s.watchlist_since ? ` · ${s.watchlist_since}` : ''}
            </div>
          )}
          <div className="card-sym">
            <a href={nse} target="_blank" rel="noreferrer">{s.symbol}</a>
          </div>
          <div className="card-decision">
            {labelize(s.ma_type)} · {labelize(s.price_interaction_type)}
            {s.is_double_bottom ? ' · Double bottom' : ''}
          </div>
        </div>
        <div className="card-date">{s.date}<br />{crossAge(s)}</div>
      </div>

      <button
        className="toggle-insights"
        onClick={() => setShowDetails(!showDetails)}
        title={showDetails ? 'Hide insights' : 'Show insights'}
      >
        {showDetails ? 'Hide Insights' : 'Show Insights'}
      </button>

      {showDetails && <InsightMetrics signal={s} />}

      <div className="card-metrics">
        <Row label="Close" val={inr(s.close)} />
        <Row label="SMA 44" val={inr(s.sma44)} cls="gold" />
        <Row label="vs SMA" val={pct(s.sma_dist_pct)} cls={s.sma_dist_pct >= 0 ? 'g' : 'r'} />
        <Row label="Day" val={pct(s.change_pct)} cls={s.change_pct >= 0 ? 'g' : 'r'} />
        <Row label="MACD" val={num(s.macd)} />
        <Row label="Signal" val={num(s.macd_signal)} />
        <Row
          label="Histogram"
          val={(s.macd_histogram >= 0 ? '+' : '') + num(s.macd_histogram)}
          cls={s.macd_histogram >= 0 ? 'g' : 'r'}
        />
        <Row label="MACD Type" val={labelize(s.cross_type)} cls="b" />
        <Row label="Weekly" val={s.weekly_rising == null ? 'Unavailable' : s.weekly_rising ? 'Rising' : 'Not rising'} />
      </div>
    </div>
  )
}

function Row({ label, val, cls }: { label: string; val: string; cls?: string }) {
  return (
    <div className="card-row">
      <span className="card-lbl">{label}</span>
      <span className={`card-val${cls ? ` ${cls}` : ''}`}>{val}</span>
    </div>
  )
}

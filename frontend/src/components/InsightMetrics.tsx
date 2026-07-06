import type { Signal } from '../types'
import { num, pct } from '../utils'

interface Props { signal: Signal }

const title = (value?: string | null): string =>
  value ? value.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()) : 'Standard'

export default function InsightMetrics({ signal: s }: Props) {
  return (
    <div className="insight-metrics">
      <div className="metric-card">
        <div className="metric-title">Momentum & Trend</div>
        <div className="metric-grid">
          <Metric
            label="Distance from SMA"
            value={pct(s.sma_dist_pct)}
            tone={s.sma_dist_pct >= 0 ? 'positive' : 'negative'}
            hint={s.sma_dist_pct >= 2 ? 'Extended above SMA44' : s.sma_dist_pct >= -2 ? 'Close to SMA44' : 'Below SMA44'}
          />
          <Metric
            label="44-bar Slope"
            value={pct(s.pct_slope)}
            tone={(s.pct_slope ?? 0) >= 0 ? 'positive' : 'negative'}
            hint={(s.pct_slope ?? 0) >= 2 ? 'Trend has room' : (s.pct_slope ?? 0) >= 0 ? 'Mildly positive' : 'Consolidating'}
          />
          <Metric
            label="ATR Slope"
            value={num(s.atr_slope)}
            tone={(s.atr_slope ?? 0) > 0 ? 'positive' : 'neutral'}
            hint="SMA44 change normalised by ATR14"
          />
        </div>
      </div>

      <div className="metric-card">
        <div className="metric-title">Setup Tags</div>
        <div className="metric-grid">
          <Metric
            label="MA Type"
            value={title(s.ma_type)}
            tone={s.ma_type === 'type1' ? 'positive' : s.ma_type === 'type2' ? 'neutral' : 'negative'}
            hint={s.ma_type === 'type1' ? 'Clean rising SMA44' : s.ma_type === 'type2' ? 'Sideways or recovering SMA44' : 'Counter-trend classification'}
          />
          <Metric
            label="Price Interaction"
            value={title(s.price_interaction_type)}
            tone={s.price_interaction_type === 'crossover' ? 'positive' : 'neutral'}
            hint={s.price_interaction_type === 'crossover' ? 'Recovered after dipping below SMA44' : 'Held SMA44 as support'}
          />
          <Metric
            label="Double Bottom"
            value={s.is_double_bottom ? 'Yes' : 'No'}
            tone={s.is_double_bottom ? 'positive' : 'neutral'}
            hint="Prior SMA44 support touch inside lookback"
          />
          <Metric
            label="Weekly SMA44"
            value={s.weekly_rising == null ? 'Unavailable' : s.weekly_rising ? 'Rising' : 'Not Rising'}
            tone={s.weekly_rising == null ? 'neutral' : s.weekly_rising ? 'positive' : 'negative'}
            hint="Optional higher-timeframe filter"
          />
        </div>
      </div>

      <div className="metric-card">
        <div className="metric-title">MACD State</div>
        <div className="metric-grid">
          <Metric
            label="Cross Type"
            value={title(s.cross_type)}
            tone={s.cross_type === 'confirmed' ? 'positive' : s.cross_type === 'imminent' ? 'positive' : 'neutral'}
            hint={s.cross_type === 'pending' ? 'Waiting for crossover' : 'Crossover condition is actionable'}
          />
          <Metric
            label="Histogram"
            value={`${s.macd_histogram >= 0 ? '+' : ''}${num(s.macd_histogram)}`}
            tone={s.macd_histogram >= 0 ? 'positive' : 'negative'}
            hint={`${s.hist_consecutive_rising ?? 0} consecutive rising histogram bars`}
          />
          <Metric
            label="Imminent Gap"
            value={pct(s.imminent_gap_ratio)}
            tone={s.is_imminent_crossover ? 'positive' : 'neutral'}
            hint="Gap between MACD and Signal when pending"
          />
        </div>
      </div>
    </div>
  )
}

function Metric({
  label,
  value,
  tone,
  hint,
}: {
  label: string
  value: string
  tone: 'positive' | 'negative' | 'neutral'
  hint: string
}) {
  return (
    <div className="metric-item">
      <span className="metric-label">{label}</span>
      <span className={`metric-value ${tone}`}>{value}</span>
      <span className="metric-hint">{hint}</span>
    </div>
  )
}

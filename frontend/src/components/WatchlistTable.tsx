import type { Signal } from '../types'
import { inr, num, pct } from '../utils'
import { Fragment, useState } from 'react'

interface Props { items: Signal[] }

const labelize = (value?: string | null): string =>
  value ? value.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()) : '-'

export default function WatchlistTable({ items }: Props) {
  const [expandedRow, setExpandedRow] = useState<string | null>(null)

  if (!items.length) {
    return <div className="empty-state">No stocks on the watchlist.</div>
  }

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>Symbol</th>
            <th>Setup</th>
            <th>Close</th>
            <th>SMA 44</th>
            <th>vs SMA</th>
            <th>MACD</th>
            <th>Histogram</th>
            <th>Type</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {items.map((s, i) => {
            const nse = `https://www.nseindia.com/get-quotes/equity?symbol=${s.symbol}`
            const isExpanded = expandedRow === s.symbol
            return (
              <Fragment key={`${s.symbol}-group-${i}`}>
                <tr className="watchlist-row" onClick={() => setExpandedRow(isExpanded ? null : s.symbol)}>
                  <td style={{ color: 'var(--text-dim)', cursor: 'pointer' }}>{i + 1}</td>
                  <td>
                    <a className="sym-link" href={nse} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>
                      {s.symbol}
                    </a>
                  </td>
                  <td>
                    {s.date}
                    <span className="awaiting">{labelize(s.price_interaction_type)}</span>
                  </td>
                  <td>{inr(s.close)}</td>
                  <td className="td-gold">{inr(s.sma44)}</td>
                  <td className={s.sma_dist_pct >= 0 ? 'td-g' : 'td-r'}>{pct(s.sma_dist_pct)}</td>
                  <td>{num(s.macd)}</td>
                  <td className={s.macd_histogram >= 0 ? 'td-g' : 'td-r'}>
                    {s.macd_histogram >= 0 ? '+' : ''}{num(s.macd_histogram)}
                  </td>
                  <td>{labelize(s.ma_type)}</td>
                  <td className="status-col">
                    <span className="expand-indicator" title={isExpanded ? 'Collapse' : 'Expand'}>
                      {isExpanded ? 'Collapse' : 'Expand'}
                    </span>
                  </td>
                </tr>
                {isExpanded && (
                  <tr className="watchlist-details-row">
                    <td colSpan={10}>
                      <div className="watchlist-details">
                        <div className="details-section">
                          <span className="details-label">Weekly SMA44:</span>
                          <span className="details-value">
                            {s.weekly_rising == null ? 'Unavailable' : s.weekly_rising ? 'Rising' : 'Not rising'}
                          </span>
                        </div>
                        <div className="details-section">
                          <span className="details-label">Pattern:</span>
                          <span className="details-value">
                            {s.is_double_bottom ? 'Double bottom near SMA44' : 'Single SMA44 support interaction'}
                          </span>
                        </div>
                        <div className="details-section">
                          <span className="details-label">Next Watch:</span>
                          <span className="details-value">
                            MACD confirmation. Histogram has risen {s.hist_consecutive_rising ?? 0} bar(s).
                          </span>
                        </div>
                        <div className="details-metrics">
                          <div className="detail-metric">
                            <span className="detail-label">44-bar Slope</span>
                            <span className={`detail-val ${(s.pct_slope ?? 0) >= 0 ? 'positive' : 'negative'}`}>
                              {pct(s.pct_slope)}
                            </span>
                          </div>
                          <div className="detail-metric">
                            <span className="detail-label">ATR Slope</span>
                            <span className={`detail-val ${(s.atr_slope ?? 0) > 0 ? 'positive' : 'negative'}`}>
                              {num(s.atr_slope)}
                            </span>
                          </div>
                          <div className="detail-metric">
                            <span className="detail-label">MACD Gap</span>
                            <span className="detail-val">{pct(s.imminent_gap_ratio)}</span>
                          </div>
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

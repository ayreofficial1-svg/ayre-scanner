import type { Signal } from '../types'

interface Props { items: Signal[] }

export default function WatchlistTable({ items }: Props) {
  if (!items.length) {
    return <div className="empty-state">No stocks on the watchlist.</div>
  }

  return (
    <div className="watchlist-list">
      {items.map((s) => {
        const nse = `https://www.nseindia.com/get-quotes/equity?symbol=${s.symbol}`
        return (
          <a className="stock-name-row" href={nse} target="_blank" rel="noreferrer" key={s.symbol}>
            {s.symbol}
          </a>
        )
      })}
    </div>
  )
}

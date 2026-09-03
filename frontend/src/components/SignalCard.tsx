import type { Signal } from '../types'

interface Props {
  signal: Signal
  readyAt: string | null
}

export default function SignalCard({ signal: s, readyAt }: Props) {
  const nse = `https://www.nseindia.com/get-quotes/equity?symbol=${s.symbol}`

  return (
    <div className={`card stock-card${s.promoted ? ' promoted' : ''}`}>
      <a className="stock-card-link" href={nse} target="_blank" rel="noreferrer">
        {s.symbol}
      </a>
      <span className="stock-card-time">{readyAt ?? 'Time unavailable'}</span>
    </div>
  )
}

import type { Signal } from '../types'

interface Props { signal: Signal }

export default function SignalCard({ signal: s }: Props) {
  const nse = `https://www.nseindia.com/get-quotes/equity?symbol=${s.symbol}`

  return (
    <div className={`card stock-card${s.promoted ? ' promoted' : ''}`}>
      <a className="stock-card-link" href={nse} target="_blank" rel="noreferrer">
        {s.symbol}
      </a>
    </div>
  )
}

export const inr = (n: number | null | undefined): string => {
  if (n == null) return '—'
  return '₹' + Number(n).toLocaleString('en-IN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

export const num = (n: number | null | undefined, d = 4): string =>
  n == null ? '—' : Number(n).toFixed(d)

export const pct = (n: number | null | undefined): string =>
  n == null ? '—' : (n >= 0 ? '+' : '') + Number(n).toFixed(2) + '%'

export const rratio = (e: number, t: number, sl: number): string => {
  const r = e - sl
  if (r <= 0) return '—'
  return '1:' + ((t - e) / r).toFixed(1)
}

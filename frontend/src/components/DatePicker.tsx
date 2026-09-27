import { useEffect, useRef, useState } from 'react'

// ─────────────────────────────────────────────────────────────────────────────
// DatePicker
//
// A small calendar popover that replaces manual date-text entry throughout
// the Signals and Weekly Report tabs. Stores/emits plain ISO date strings
// ("2026-09-27") — the same shape every date field in this app already
// uses — so it's a drop-in replacement for a native <input type="date">
// with no backend or type changes required.
// ─────────────────────────────────────────────────────────────────────────────

const WEEKDAYS = ['S', 'M', 'T', 'W', 'T', 'F', 'S']
const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

function pad(n: number) { return String(n).padStart(2, '0') }
function toIso(y: number, m: number, d: number) { return `${y}-${pad(m + 1)}-${pad(d)}` }

function parseIso(iso: string): { y: number; m: number; d: number } | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso.trim())
  if (!match) return null
  return { y: Number(match[1]), m: Number(match[2]) - 1, d: Number(match[3]) }
}

function displayLabel(iso: string): string {
  const parsed = parseIso(iso)
  if (!parsed) return ''
  try {
    return new Date(parsed.y, parsed.m, parsed.d).toLocaleDateString('en-IN', {
      day: 'numeric', month: 'short', year: 'numeric',
    })
  } catch {
    return iso
  }
}

export default function DatePicker({
  label,
  value,
  onChange,
  required,
  clearable = true,
}: {
  label?: string
  value: string
  onChange: (iso: string) => void
  required?: boolean
  clearable?: boolean
}) {
  const [open, setOpen] = useState(false)
  const today = new Date()
  const parsedValue = parseIso(value)
  const [viewYear, setViewYear] = useState(parsedValue?.y ?? today.getFullYear())
  const [viewMonth, setViewMonth] = useState(parsedValue?.m ?? today.getMonth())
  const wrapRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const p = parseIso(value)
    if (p) { setViewYear(p.y); setViewMonth(p.m) }
  }, [value])

  useEffect(() => {
    const onClickOutside = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClickOutside)
    return () => document.removeEventListener('mousedown', onClickOutside)
  }, [])

  const daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate()
  const firstWeekday = new Date(viewYear, viewMonth, 1).getDay()
  const cells: (number | null)[] = [
    ...Array(firstWeekday).fill(null),
    ...Array.from({ length: daysInMonth }, (_, i) => i + 1),
  ]

  const goMonth = (delta: number) => {
    let y = viewYear
    let m = viewMonth + delta
    if (m < 0) { m = 11; y -= 1 }
    if (m > 11) { m = 0; y += 1 }
    setViewYear(y)
    setViewMonth(m)
  }

  const pick = (day: number) => {
    onChange(toIso(viewYear, viewMonth, day))
    setOpen(false)
  }

  const isSelected = (day: number) =>
    !!parsedValue && parsedValue.y === viewYear && parsedValue.m === viewMonth && parsedValue.d === day

  const isToday = (day: number) =>
    today.getFullYear() === viewYear && today.getMonth() === viewMonth && today.getDate() === day

  return (
    <div className="field date-picker" ref={wrapRef}>
      {label && <span>{label}</span>}
      <button
        type="button"
        className={`date-picker-trigger${required && !value ? ' input-error' : ''}`}
        onClick={() => setOpen(o => !o)}
      >
        <span className={value ? '' : 'placeholder'}>
          {value ? displayLabel(value) : 'Select date'}
        </span>
        <CalendarGlyph />
      </button>

      {open && (
        <div className="date-picker-popover" role="dialog" aria-label="Choose a date">
          <div className="date-picker-nav">
            <button type="button" onClick={() => goMonth(-1)} aria-label="Previous month">‹</button>
            <span>{MONTH_NAMES[viewMonth]} {viewYear}</span>
            <button type="button" onClick={() => goMonth(1)} aria-label="Next month">›</button>
          </div>
          <div className="date-picker-weekdays">
            {WEEKDAYS.map((w, i) => <span key={i}>{w}</span>)}
          </div>
          <div className="date-picker-grid">
            {cells.map((day, i) => (
              day === null
                ? <span key={i} className="date-picker-empty" />
                : (
                  <button
                    key={i}
                    type="button"
                    className={`date-picker-day${isSelected(day) ? ' selected' : ''}${isToday(day) ? ' today' : ''}`}
                    onClick={() => pick(day)}
                  >
                    {day}
                  </button>
                )
            ))}
          </div>
          <div className="date-picker-footer">
            <button
              type="button"
              className="date-picker-today-btn"
              onClick={() => {
                setViewYear(today.getFullYear())
                setViewMonth(today.getMonth())
                onChange(toIso(today.getFullYear(), today.getMonth(), today.getDate()))
                setOpen(false)
              }}
            >
              Today
            </button>
            {clearable && value && (
              <button type="button" className="date-picker-clear-btn" onClick={() => { onChange(''); setOpen(false) }}>
                Clear
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function CalendarGlyph() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect x="3" y="5" width="18" height="16" rx="2" stroke="currentColor" strokeWidth="1.6" />
      <path d="M3 9.5H21" stroke="currentColor" strokeWidth="1.6" />
      <path d="M8 3V6.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      <path d="M16 3V6.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  )
}

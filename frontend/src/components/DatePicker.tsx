import { useEffect, useRef, useState } from 'react'

// ─────────────────────────────────────────────────────────────────────────────
// DatePicker
//
// A date field that supports BOTH manual typing and calendar selection.
// Stores/emits plain ISO date strings ("2026-09-27") — the same shape every
// date field in this app already uses — so it's a drop-in replacement
// wherever it was already used, and for a native <input type="date">.
//
// Typing accepts: ISO ("2026-09-27"), "27 Sep 2026", or "27/09/2026".
// Invalid or out-of-range (min/max) text reverts to the last valid value
// on blur/Enter; valid text commits immediately via onChange.
// ─────────────────────────────────────────────────────────────────────────────

const WEEKDAYS = ['S', 'M', 'T', 'W', 'T', 'F', 'S']
const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

type YMD = { y: number; m: number; d: number }

function pad(n: number) { return String(n).padStart(2, '0') }
function toIso(y: number, m: number, d: number) { return `${y}-${pad(m + 1)}-${pad(d)}` }

function isValidYMD({ y, m, d }: YMD): boolean {
  if (m < 0 || m > 11) return false
  if (y < 1000 || y > 9999) return false
  const daysInMonth = new Date(y, m + 1, 0).getDate()
  return d >= 1 && d <= daysInMonth
}

function parseIso(iso: string): YMD | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso.trim())
  if (!match) return null
  const ymd = { y: Number(match[1]), m: Number(match[2]) - 1, d: Number(match[3]) }
  return isValidYMD(ymd) ? ymd : null
}

// Accepts "27 Sep 2026" / "27 September 2026", "27/09/2026" or "27-09-2026",
// in addition to plain ISO — a superset so typing what the field already
// displays always round-trips.
function parseFlexible(input: string): YMD | null {
  const s = input.trim()
  if (!s) return null

  const iso = parseIso(s)
  if (iso) return iso

  const named = /^(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})$/.exec(s)
  if (named) {
    const day = Number(named[1])
    const year = Number(named[3])
    const monthKey = named[2].toLowerCase().slice(0, 3)
    const m = MONTH_NAMES.findIndex(name => name.toLowerCase().startsWith(monthKey))
    if (m >= 0) {
      const ymd = { y: year, m, d: day }
      return isValidYMD(ymd) ? ymd : null
    }
    return null
  }

  const slashed = /^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$/.exec(s)
  if (slashed) {
    const ymd = { y: Number(slashed[3]), m: Number(slashed[2]) - 1, d: Number(slashed[1]) }
    return isValidYMD(ymd) ? ymd : null
  }

  return null
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
  min,
  max,
  placeholder = 'DD MMM YYYY',
}: {
  label?: string
  value: string
  onChange: (iso: string) => void
  required?: boolean
  clearable?: boolean
  /** Inclusive ISO bounds ("2026-01-01"). Out-of-range days/typed dates are rejected. */
  min?: string
  max?: string
  placeholder?: string
}) {
  const [open, setOpen] = useState(false)
  const [text, setText] = useState(() => displayLabel(value))
  const [invalid, setInvalid] = useState(false)
  const today = new Date()
  const parsedValue = parseIso(value)
  const [viewYear, setViewYear] = useState(parsedValue?.y ?? today.getFullYear())
  const [viewMonth, setViewMonth] = useState(parsedValue?.m ?? today.getMonth())
  const wrapRef = useRef<HTMLDivElement>(null)

  // Keep the displayed text and the visible month in sync whenever the
  // *committed* value changes — whether from typing, the calendar, or a
  // parent auto-filling this field (e.g. Week End picking up Week Start).
  useEffect(() => {
    setText(displayLabel(value))
    setInvalid(false)
    const p = parseIso(value)
    if (p) { setViewYear(p.y); setViewMonth(p.m) }
  }, [value])

  useEffect(() => {
    const onClickOutside = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false)
        commitText(text)
      }
    }
    document.addEventListener('mousedown', onClickOutside)
    return () => document.removeEventListener('mousedown', onClickOutside)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text])

  const inRange = (iso: string) => (!min || iso >= min) && (!max || iso <= max)

  const commitText = (raw: string) => {
    const trimmed = raw.trim()
    if (!trimmed) {
      setInvalid(false)
      setText('')
      if (value) onChange('')
      return
    }
    const parsed = parseFlexible(trimmed)
    if (!parsed) { setInvalid(true); return }
    const iso = toIso(parsed.y, parsed.m, parsed.d)
    if (!inRange(iso)) { setInvalid(true); return }
    setInvalid(false)
    setText(displayLabel(iso))
    if (iso !== value) onChange(iso)
  }

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

  const dayIso = (day: number) => toIso(viewYear, viewMonth, day)
  const isDayDisabled = (day: number) => !inRange(dayIso(day))

  const pick = (day: number) => {
    if (isDayDisabled(day)) return
    const iso = dayIso(day)
    setInvalid(false)
    setText(displayLabel(iso))
    onChange(iso)
    setOpen(false)
  }

  const isSelected = (day: number) =>
    !!parsedValue && parsedValue.y === viewYear && parsedValue.m === viewMonth && parsedValue.d === day

  const isToday = (day: number) =>
    today.getFullYear() === viewYear && today.getMonth() === viewMonth && today.getDate() === day

  const todayIso = toIso(today.getFullYear(), today.getMonth(), today.getDate())
  const todayDisabled = !inRange(todayIso)

  return (
    <div className="field date-picker" ref={wrapRef}>
      {label && <span>{label}</span>}
      <div className={`date-picker-control${(invalid || (required && !value)) ? ' input-error' : ''}`}>
        <input
          type="text"
          className="date-picker-input"
          value={text}
          placeholder={placeholder}
          onChange={e => { setText(e.target.value); setInvalid(false) }}
          onFocus={() => setOpen(true)}
          onBlur={() => commitText(text)}
          onKeyDown={e => {
            if (e.key === 'Enter') { commitText(text); setOpen(false) }
            if (e.key === 'Escape') { setText(displayLabel(value)); setInvalid(false); setOpen(false) }
          }}
          required={required}
          autoComplete="off"
        />
        <button
          type="button"
          className="date-picker-icon-btn"
          aria-label="Open calendar"
          onClick={() => setOpen(o => !o)}
        >
          <CalendarGlyph />
        </button>
      </div>

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
                    disabled={isDayDisabled(day)}
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
              disabled={todayDisabled}
              onClick={() => {
                setViewYear(today.getFullYear())
                setViewMonth(today.getMonth())
                setInvalid(false)
                setText(displayLabel(todayIso))
                onChange(todayIso)
                setOpen(false)
              }}
            >
              Today
            </button>
            {clearable && value && (
              <button
                type="button"
                className="date-picker-clear-btn"
                onClick={() => { setInvalid(false); setText(''); onChange(''); setOpen(false) }}
              >
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

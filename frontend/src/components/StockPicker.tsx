import { useEffect, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import type { StockDirectoryEntry } from '../types'

// ─────────────────────────────────────────────────────────────────────────────
// StockPicker
//
// Searchable stock autocomplete shared by the Signals tab and the Weekly
// Report tab. Loads /api/stocks once (module-level cache, since the list is
// the same everywhere it's used and rarely changes) and filters client-side
// as the admin types — empty input shows nothing, one letter shows a short
// "starts with" list, narrowing as they type more.
// ─────────────────────────────────────────────────────────────────────────────

let directoryCache: StockDirectoryEntry[] | null = null
let directoryPromise: Promise<StockDirectoryEntry[]> | null = null

function loadDirectory(): Promise<StockDirectoryEntry[]> {
  if (directoryCache) return Promise.resolve(directoryCache)
  if (directoryPromise) return directoryPromise
  directoryPromise = fetch('/api/stocks')
    .then(res => res.json())
    .then((data: { stocks?: StockDirectoryEntry[] }) => {
      directoryCache = data.stocks ?? []
      return directoryCache
    })
    .catch(() => {
      directoryCache = []
      return directoryCache
    })
  return directoryPromise
}

const MAX_SUGGESTIONS = 8

export default function StockPicker({
  label,
  value,
  onChange,
  placeholder = 'Start typing a symbol or name…',
  required,
  onSelect,
}: {
  label?: string
  value: string
  onChange: (symbol: string) => void
  placeholder?: string
  required?: boolean
  /** Called only when a stock is actually chosen from the list (click or Enter), not on every keystroke. */
  onSelect?: (symbol: string) => void
}) {
  const [directory, setDirectory] = useState<StockDirectoryEntry[]>([])
  const [query, setQuery] = useState(value)
  const [open, setOpen] = useState(false)
  const [highlight, setHighlight] = useState(0)
  const wrapRef = useRef<HTMLDivElement>(null)

  useEffect(() => { loadDirectory().then(setDirectory) }, [])

  // Keep the visible text in sync when the parent resets/edits the value
  // (e.g. cancel-edit, or loading a row to edit) without fighting the
  // user's own typing.
  useEffect(() => { setQuery(value) }, [value])

  useEffect(() => {
    const onClickOutside = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClickOutside)
    return () => document.removeEventListener('mousedown', onClickOutside)
  }, [])

  const suggestions = useMemo(() => {
    const q = query.trim().toUpperCase()
    if (!q) return []
    const starts: StockDirectoryEntry[] = []
    const contains: StockDirectoryEntry[] = []
    for (const s of directory) {
      const sym = s.symbol.toUpperCase()
      const name = (s.name || '').toUpperCase()
      if (sym.startsWith(q) || name.startsWith(q)) starts.push(s)
      else if (sym.includes(q) || name.includes(q)) contains.push(s)
      if (starts.length >= MAX_SUGGESTIONS) break
    }
    return [...starts, ...contains].slice(0, MAX_SUGGESTIONS)
  }, [query, directory])

  const select = (s: StockDirectoryEntry) => {
    onChange(s.symbol)
    setQuery(s.symbol)
    setOpen(false)
    onSelect?.(s.symbol)
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (!open || suggestions.length === 0) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHighlight(h => (h + 1) % suggestions.length)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHighlight(h => (h - 1 + suggestions.length) % suggestions.length)
    } else if (e.key === 'Enter') {
      e.preventDefault()
      select(suggestions[highlight])
    } else if (e.key === 'Escape') {
      setOpen(false)
    }
  }

  return (
    <div className="field stock-picker" ref={wrapRef}>
      {label && <span>{label}</span>}
      <div className="stock-picker-input-wrap">
        <input
          value={query}
          onChange={e => {
            const v = e.target.value
            setQuery(v)
            onChange(v.trim().toUpperCase())
            setOpen(true)
            setHighlight(0)
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          required={required}
          autoComplete="off"
        />
        {open && suggestions.length > 0 && (
          <ul className="stock-picker-menu" role="listbox">
            {suggestions.map((s, i) => (
              <li
                key={s.symbol}
                role="option"
                aria-selected={i === highlight}
                className={i === highlight ? 'active' : ''}
                onMouseDown={e => { e.preventDefault(); select(s) }}
                onMouseEnter={() => setHighlight(i)}
              >
                <span className="stock-picker-sym">{s.symbol}</span>
                {s.name && <span className="stock-picker-name">{s.name}</span>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

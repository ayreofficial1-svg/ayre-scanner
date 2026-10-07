import { useCallback, useEffect, useRef, useState } from 'react'
import type { EntryHitsResponse } from '../types'
import { beep, readSound, unlockAudio, writeSound } from '../entrySound'

// ─────────────────────────────────────────────────────────────────────────────
// EntryHitsWatcher (admin only)
//
// Lives in the page header, so it keeps running on EVERY tab (Scanner, Backtest,
// Signals ...). It asks OUR backend (never Fyers) every 12 s for today's entry
// hits, beeps when a new one appears (if the sound switch is on), shows how many
// are still "New", and puts an unseen counter in the browser tab title while the
// tab is hidden. The sound choice is saved in this browser.
// The hit list and the action buttons stay in the Entry hits panel (Signals tab).
// ─────────────────────────────────────────────────────────────────────────────

const POLL_MS = 12000
const BASE_TITLE = typeof document !== 'undefined' ? document.title : ''

export default function EntryHitsWatcher({ onOpen }: { onOpen: () => void }) {
  const [sound, setSound] = useState<boolean>(readSound)
  const [newCount, setNewCount] = useState(0)
  const [unseen, setUnseen] = useState(0)
  const seen = useRef<Set<string> | null>(null)
  const soundRef = useRef(false)
  soundRef.current = sound

  // Sound restored after a refresh: unlock audio on the first click / key press.
  useEffect(() => {
    if (!sound) return
    const unlock = () => unlockAudio()
    window.addEventListener('pointerdown', unlock, { once: true })
    window.addEventListener('keydown', unlock, { once: true })
    return () => {
      window.removeEventListener('pointerdown', unlock)
      window.removeEventListener('keydown', unlock)
    }
  }, [sound])

  const load = useCallback(async () => {
    try {
      const res = await fetch('/api/entries/hits?days=1')
      if (!res.ok) return
      const body = await res.json().catch(() => null) as EntryHitsResponse | null
      if (!body || !Array.isArray(body.hits)) return
      setNewCount(body.hits.filter(h => h.status === 'new').length)

      const ids = new Set(body.hits.map(h => h.id))
      if (seen.current === null) {
        seen.current = ids                       // first load: nothing counts as new
      } else {
        const fresh = body.hits.filter(h => !seen.current!.has(h.id) && h.status === 'new')
        if (fresh.length) {
          if (document.hidden) setUnseen(n => n + fresh.length)
          if (soundRef.current) beep()
        }
        seen.current = ids
      }
    } catch { /* the next poll tries again */ }
  }, [])

  useEffect(() => {
    load()
    // Keeps polling when the tab is hidden too (browsers slow hidden tabs to about once a minute).
    const timer = window.setInterval(load, POLL_MS)
    const onVisible = () => { if (!document.hidden) { setUnseen(0); load() } }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [load])

  useEffect(() => {
    document.title = unseen > 0 ? `(${unseen}) ${BASE_TITLE}` : BASE_TITLE
    return () => { document.title = BASE_TITLE }
  }, [unseen])

  return (
    <>
      {newCount > 0 && (
        <button
          className="theme-btn"
          onClick={onOpen}
          title="New entry hits waiting for your review. Click to open the Signals tab."
        >
          {newCount} new hit{newCount === 1 ? '' : 's'}
        </button>
      )}
      <label className="switch-field" title="Beep when a new entry hit is detected (works on every tab)">
        <input
          type="checkbox"
          checked={sound}
          onChange={e => {
            setSound(e.target.checked)
            writeSound(e.target.checked)
            if (e.target.checked) { unlockAudio(); beep() }
          }}
        />
        <span className="switch-track" aria-hidden="true"><span className="switch-thumb" /></span>
        <span className="switch-label">Hit sound</span>
      </label>
    </>
  )
}

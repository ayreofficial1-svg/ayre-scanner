import { useCallback, useEffect, useRef, useState } from 'react'
import type { EntryHitsResponse } from '../types'
import {
  beep, claimHits, readSound, startKeepAlive, startTicker, stopKeepAlive,
  unlockAudio, unlockOnInteraction, writeSound,
} from '../entrySound'

// ─────────────────────────────────────────────────────────────────────────────
// EntryHitsWatcher (admin only)
//
// Lives in the page header, so it keeps running on EVERY tab (Scanner, Backtest,
// Signals ...) and does not depend on any panel being open. It asks OUR backend
// (never Fyers) every 12 s for today's entry hits and sounds the alert once for each
// hit it has not seen before (if the sound switch is on), shows how many are still
// "New", and puts an unseen counter in the browser tab title while the tab is hidden.
//
// Background reliability (tab in the background or window minimized): the 12 s tick
// runs in a Web Worker (not throttled like page timers), the audio engine is unlocked
// on the first interaction and kept awake, and a Web Lock + inaudible audio stream stop
// the browser from freezing the tab. Each hit id is sounded once, also across tabs.
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
  const loading = useRef(false)
  soundRef.current = sound

  // Sound on (also restored after a refresh): unlock audio on the first interaction and
  // keep the tab awake while it stays on.
  useEffect(() => {
    if (!sound) { stopKeepAlive(); return }
    const removeUnlock = unlockOnInteraction()
    startKeepAlive()
    const onVisible = () => { if (!document.hidden) { unlockAudio(); startKeepAlive() } }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      removeUnlock()
      document.removeEventListener('visibilitychange', onVisible)
      stopKeepAlive()
    }
  }, [sound])

  const load = useCallback(async () => {
    if (loading.current) return
    loading.current = true
    try {
      const res = await fetch('/api/entries/hits?days=1')
      if (!res.ok) return
      const body = await res.json().catch(() => null) as EntryHitsResponse | null
      if (!body || !Array.isArray(body.hits)) return
      setNewCount(body.hits.filter(h => h.status === 'new').length)

      if (seen.current === null) {
        seen.current = new Set(body.hits.map(h => h.id))   // first load: nothing counts as new
      } else {
        const known = seen.current
        // Genuinely new = an id never seen before. A hit that is dismissed / reviewed
        // later keeps its id, so it can never sound twice; a fresh entry event has a new id.
        const fresh = body.hits.filter(h => !known.has(h.id) && h.status === 'new')
        body.hits.forEach(h => known.add(h.id))
        if (fresh.length) {
          if (document.hidden) setUnseen(n => n + fresh.length)
          if (soundRef.current && claimHits(fresh.map(h => h.id))) beep()
        }
      }
    } catch { /* the next poll tries again */ }
    finally { loading.current = false }
  }, [])

  useEffect(() => {
    load()
    // Worker-driven tick: keeps its pace while the tab is hidden or the window minimized.
    const stopTicker = startTicker(load, POLL_MS)
    const onVisible = () => { if (!document.hidden) { setUnseen(0); load() } }
    const onOnline = () => load()
    document.addEventListener('visibilitychange', onVisible)
    window.addEventListener('online', onOnline)
    return () => {
      stopTicker()
      document.removeEventListener('visibilitychange', onVisible)
      window.removeEventListener('online', onOnline)
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
      <label className="switch-field" title="Sound when a new entry hit is detected (works on every tab, also in the background)">
        <input
          type="checkbox"
          checked={sound}
          onChange={e => {
            setSound(e.target.checked)
            writeSound(e.target.checked)
            if (e.target.checked) { unlockAudio(); startKeepAlive(); beep() }
          }}
        />
        <span className="switch-track" aria-hidden="true"><span className="switch-thumb" /></span>
        <span className="switch-label">Hit sound</span>
      </label>
    </>
  )
}

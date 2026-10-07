// Shared sound + background helpers for entry-hit alerts (website, admin only).
// The on/off choice is saved in this browser so a refresh or tab switch keeps it.
//
// Keeping the alert reliable while the tab is in the background or the window is
// minimized needs three things, all done here:
//   1. The audio engine must already be unlocked. Browsers only allow sound after a
//      click / key press on the page, so it is unlocked on the first interaction and
//      re-resumed whenever the browser suspends it.
//   2. The page must keep checking. Ordinary timers in a hidden tab are slowed to about
//      once a minute (and a long-hidden tab can be frozen), so the 12 s tick runs in a
//      Web Worker, whose timers are not throttled, and a Web Lock + an inaudible audio
//      stream are held while the sound is on so the browser does not freeze the tab.
//   3. Each hit must sound once. Hit ids are remembered, also across tabs of this browser.

const SOUND_KEY = 'ayre.entryHits.sound'
const BEEPED_KEY = 'ayre.entryHits.beeped'

export function readSound(): boolean {
  try { return window.localStorage.getItem(SOUND_KEY) === '1' } catch { return false }
}

export function writeSound(on: boolean): void {
  try { window.localStorage.setItem(SOUND_KEY, on ? '1' : '0') } catch { /* storage may be blocked */ }
}

// One shared audio context.
let audioCtx: AudioContext | null = null

function getCtx(): AudioContext | null {
  try {
    if (audioCtx && audioCtx.state !== 'closed') return audioCtx
    const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
    audioCtx = new Ctx()
    // If the browser suspends / interrupts it later (e.g. a phone call, power saving), wake it again.
    audioCtx.addEventListener('statechange', () => {
      if (audioCtx && audioCtx.state !== 'running' && audioCtx.state !== 'closed') {
        audioCtx.resume().catch(() => {})
      }
    })
    return audioCtx
  } catch {
    return null
  }
}

/** True once the browser lets this page make sound. */
export function audioReady(): boolean {
  return !!audioCtx && audioCtx.state === 'running'
}

export function unlockAudio(): void {
  const ctx = getCtx()
  if (ctx && ctx.state !== 'running') ctx.resume().catch(() => {})
}

/**
 * Unlock the audio on the first click / key press / touch (kept until it works).
 * Returns a function that removes the listeners.
 */
export function unlockOnInteraction(): () => void {
  const events: Array<keyof WindowEventMap> = ['pointerdown', 'keydown', 'touchstart', 'click']
  const handler = () => {
    unlockAudio()
    if (audioReady()) remove()
  }
  const remove = () => events.forEach(e => window.removeEventListener(e, handler))
  events.forEach(e => window.addEventListener(e, handler, { passive: true }))
  // Also try right away: works when the page already had a click before this was called.
  unlockAudio()
  return remove
}

// ── Keep-alive: stops the browser freezing / throttling the tab while sound is on ──
let keepOsc: OscillatorNode | null = null
let releaseLock: (() => void) | null = null

export function startKeepAlive(): void {
  try {
    const ctx = getCtx()
    if (ctx && !keepOsc) {
      // An inaudible tone keeps the page counted as "playing audio", which browsers
      // exempt from background throttling.
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      gain.gain.value = 0.00001
      osc.frequency.value = 40
      osc.connect(gain)
      gain.connect(ctx.destination)
      osc.start()
      keepOsc = osc
    }
  } catch { /* optional */ }
  try {
    const locks = (navigator as unknown as { locks?: { request: (name: string, cb: () => Promise<void>) => Promise<void> } }).locks
    if (locks && !releaseLock) {
      // A held Web Lock stops Chrome from freezing a hidden tab.
      locks.request('ayre-entry-hits-keepalive', () => new Promise<void>(resolve => { releaseLock = resolve }))
        .catch(() => {})
    }
  } catch { /* optional */ }
}

export function stopKeepAlive(): void {
  try { keepOsc?.stop() } catch { /* already stopped */ }
  keepOsc = null
  if (releaseLock) { releaseLock(); releaseLock = null }
}

// ── Background ticker: a Web Worker timer is not throttled in hidden tabs ──
/** Calls `onTick` every `ms`, even while the tab is hidden. Returns a stop function. */
export function startTicker(onTick: () => void, ms: number): () => void {
  try {
    const src = `let t=setInterval(()=>postMessage(1),${Math.max(1000, Math.floor(ms))});onmessage=e=>{if(e.data==='stop'){clearInterval(t);close()}}`
    const url = URL.createObjectURL(new Blob([src], { type: 'text/javascript' }))
    const worker = new Worker(url)
    URL.revokeObjectURL(url)
    worker.onmessage = () => onTick()
    return () => { try { worker.postMessage('stop'); worker.terminate() } catch { /* gone */ } }
  } catch {
    // Workers blocked: fall back to a normal timer (slower while hidden, still works).
    const id = window.setInterval(onTick, ms)
    return () => window.clearInterval(id)
  }
}

// ── One sound per hit, even with several tabs of the site open ──
/** Marks these hit ids as sounded. Returns true only if at least one was not already marked. */
export function claimHits(ids: string[]): boolean {
  if (!ids.length) return false
  try {
    const raw = window.localStorage.getItem(BEEPED_KEY)
    const done: string[] = raw ? JSON.parse(raw) : []
    const fresh = ids.filter(id => !done.includes(id))
    if (!fresh.length) return false
    window.localStorage.setItem(BEEPED_KEY, JSON.stringify([...done, ...fresh].slice(-200)))
    return true
  } catch {
    return true      // storage blocked: better to sound than to stay silent
  }
}

/** Plays the alert: two short tones, clearly audible but not harsh. */
export function beep(): void {
  try {
    const ctx = getCtx()
    if (!ctx) return
    if (ctx.state !== 'running') ctx.resume().catch(() => {})
    const t0 = ctx.currentTime + 0.02
    ;[[880, 0], [1175, 0.3]].forEach(([freq, offset]) => {
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.type = 'sine'
      osc.frequency.value = freq
      gain.gain.setValueAtTime(0.0001, t0 + offset)
      gain.gain.exponentialRampToValueAtTime(0.2, t0 + offset + 0.02)
      gain.gain.exponentialRampToValueAtTime(0.0001, t0 + offset + 0.24)
      osc.connect(gain)
      gain.connect(ctx.destination)
      osc.start(t0 + offset)
      osc.stop(t0 + offset + 0.26)
    })
  } catch { /* sound is optional */ }
}

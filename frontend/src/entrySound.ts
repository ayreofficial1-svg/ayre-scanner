// Shared sound helpers for entry-hit alerts (website, admin only).
// The on/off choice is saved in this browser so a refresh or tab switch keeps it.

const SOUND_KEY = 'ayre.entryHits.sound'

export function readSound(): boolean {
  try { return window.localStorage.getItem(SOUND_KEY) === '1' } catch { return false }
}

export function writeSound(on: boolean): void {
  try { window.localStorage.setItem(SOUND_KEY, on ? '1' : '0') } catch { /* storage may be blocked */ }
}

// One shared audio context. A browser keeps it silent after a page refresh until
// the page gets a click or key press, so it is resumed on the first interaction.
let audioCtx: AudioContext | null = null

function getCtx(): AudioContext | null {
  try {
    if (audioCtx && audioCtx.state !== 'closed') return audioCtx
    const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
    audioCtx = new Ctx()
    return audioCtx
  } catch {
    return null
  }
}

export function unlockAudio(): void {
  const ctx = getCtx()
  if (ctx && ctx.state === 'suspended') ctx.resume().catch(() => {})
}

export function beep(): void {
  try {
    const ctx = getCtx()
    if (!ctx) return
    if (ctx.state === 'suspended') ctx.resume().catch(() => {})
    const osc = ctx.createOscillator()
    const gain = ctx.createGain()
    osc.frequency.value = 880
    gain.gain.value = 0.08
    osc.connect(gain)
    gain.connect(ctx.destination)
    osc.start()
    osc.stop(ctx.currentTime + 0.25)
  } catch { /* sound is optional */ }
}

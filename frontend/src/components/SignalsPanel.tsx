import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import type { SignalPick, SignalEntryState } from '../types'
import { inr, pct } from '../utils'
import StockPicker from './StockPicker'
import { postGuarded, usePushStatus, whenIST } from '../pushApi'

// ─────────────────────────────────────────────────────────────────────────────
// SignalsPanel
//
// Pick a stock, set Entry / Exit / Stop Loss, and it stays live in the app
// until manually disabled or removed. Everything else the old form exposed
// (rationale, category, image, scheduling window, featured/pinned ordering)
// is still accepted by the API for backward compatibility but is no longer
// surfaced here — this tab now only edits the fields it's actually for.
//
// Publication gate: a saved signal is a DRAFT (admin only). Only the
// "Publish to app" button makes it visible to app users. Saving, editing and
// publishing never send a notification. Phone notifications are separate,
// manual buttons ("Send notification" / "Send update notification"), each
// confirmed and logged by the server.
// ─────────────────────────────────────────────────────────────────────────────

function emptyForm() {
  return {
    id: '',
    symbol: '',
    entry_price: '',
    exit_price: '',
    stop_loss: '',
    enabled: true,
    live: false,   // editing a signal that app users currently see
    reached: false, // ...and its "entry reached" fact is live in the app
    entry_was: '',  // entry price when the edit started
  }
}

type SignalState = 'Draft' | 'Published' | 'Hidden'

// Missing `published` = legacy record = Published.
function signalState(s: SignalPick): SignalState {
  if (s.enabled === false || s.active === false) return 'Hidden'
  return s.published === false ? 'Draft' : 'Published'
}

export default function SignalsPanel() {
  const [signals, setSignals] = useState<SignalPick[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [form, setForm] = useState(emptyForm)
  const [submitting, setSubmitting] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [publishTarget, setPublishTarget] = useState<SignalPick | null>(null)
  const [alsoNotify, setAlsoNotify] = useState(false)
  const [entryStates, setEntryStates] = useState<Record<string, SignalEntryState>>({})
  const { status: pushStatus, refresh: refreshPush } = usePushStatus()

  const load = async () => {
    setLoading(true)
    try {
      const res  = await fetch('/api/signals?all=1')
      const data = await res.json() as { signals: SignalPick[]; error?: string }
      if (!res.ok) throw new Error(data.error || 'Failed to load signals')
      // Removed signals are only deactivated server-side; don't list them.
      setSignals(data.signals.filter(s => s.active !== false))
      setError(null)
      try {   // detection state is informational; never blocks the list
        const r = await fetch('/api/entries/signal-states')
        if (r.ok) setEntryStates((await r.json() as { states: Record<string, SignalEntryState> }).states)
      } catch { /* ignore */ }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load signals')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const resetForm = () => setForm(emptyForm())

  const editSignal = (signal: SignalPick) => {
    setForm({
      id: signal.id,
      symbol: signal.symbol,
      entry_price: signal.entry_price != null ? String(signal.entry_price) : '',
      exit_price: signal.exit_price != null ? String(signal.exit_price) : '',
      stop_loss: signal.stop_loss != null ? String(signal.stop_loss) : '',
      enabled: signal.enabled ?? signal.active ?? true,
      live: signalState(signal) === 'Published',
      reached: !!signal.entry_reached_at,
      entry_was: signal.entry_price != null ? String(signal.entry_price) : '',
    })
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const addSignal = async (event: FormEvent) => {
    event.preventDefault()
    if (!form.symbol.trim()) return
    setError(null)
    setNotice(null)

    const numOrNull = (v: string) => (v.trim() === '' ? null : Number(v))
    const entry_price = numOrNull(form.entry_price)
    const exit_price = numOrNull(form.exit_price)
    const stop_loss = numOrNull(form.stop_loss)
    if ([entry_price, exit_price, stop_loss].some(v => v !== null && Number.isNaN(v))) {
      setError('Entry, exit and stop loss must be numeric')
      return
    }

    setSubmitting(true)
    try {
      const res  = await fetch('/api/signals', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: form.id || undefined,
          symbol: form.symbol.trim().toUpperCase(),
          enabled: form.enabled,
          entry_price,
          exit_price,
          stop_loss,
        }),
      })
      const data = await res.json().catch(() => ({})) as { error?: string }
      if (!res.ok) throw new Error(data.error || 'Failed to save signal')
      setNotice(
        form.id
          ? form.live
            ? 'Saved. This signal is live, so app users now see the change. No notification was sent.'
            : 'Saved. No notification was sent.'
          : 'Saved as a Draft. Not visible in the app yet. Use "Publish to app" when ready.',
      )
      resetForm()
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save signal')
    } finally {
      setSubmitting(false)
    }
  }

  const removeSignal = async (id: string) => {
    try {
      const res  = await fetch(`/api/signals/${id}`, { method: 'DELETE' })
      const data = await res.json().catch(() => ({})) as { error?: string }
      if (!res.ok) throw new Error(data.error || 'Failed to remove signal')
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to remove signal')
    }
  }

  const unpublish = async (signal: SignalPick) => {
    if (busyId) return
    if (!window.confirm(
      `Unpublish ${signal.symbol}?\n\nIt will be hidden from the app and kept here as a Draft. No notification is sent.`,
    )) return

    setBusyId(signal.id)
    setError(null)
    setNotice(null)
    try {
      const res  = await fetch(`/api/signals/${signal.id}/unpublish`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirm: true }),
      })
      const data = await res.json().catch(() => ({})) as { error?: string }
      if (!res.ok) throw new Error(data.error || 'Failed to change publication')
      setNotice(`${signal.symbol} is unpublished and hidden from the app.`)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to change publication')
    } finally {
      setBusyId(null)
    }
  }

  const openPublish = (signal: SignalPick) => {
    if (busyId) return
    setAlsoNotify(false)          // always off by default
    setPublishTarget(signal)
    refreshPush()
  }

  const confirmPublish = async () => {
    const signal = publishTarget
    if (!signal || busyId) return
    setPublishTarget(null)
    setBusyId(signal.id)
    setError(null)
    setNotice(null)
    try {
      const res  = await fetch(`/api/signals/${signal.id}/publish`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirm: true, notify: alsoNotify }),
      })
      const data = await res.json().catch(() => ({})) as {
        error?: string
        notification?: { ok: boolean; audience?: number; error?: string }
      }
      if (!res.ok) throw new Error(data.error || 'Failed to change publication')
      const n = data.notification
      setNotice(
        !alsoNotify
          ? `${signal.symbol} is now published. App users see it the next time they open Signals. No notification was sent.`
          : n?.ok
            ? `${signal.symbol} is published and a notification was sent to ${n.audience} phone(s).`
            : `${signal.symbol} is published, but the notification was NOT sent: ${n?.error ?? 'unknown reason'}`,
      )
      await load()
      refreshPush()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to change publication')
    } finally {
      setBusyId(null)
    }
  }

  const sendNotification = async (signal: SignalPick, kind: 'new' | 'update', again = false) => {
    if (busyId) return
    const audience = pushStatus?.signal_devices
    const who = audience != null ? `${audience} phone(s) with signal alerts on` : 'phones with signal alerts on'
    const message = kind === 'new'
      ? `Send a "new signal" notification for ${signal.symbol} to ${who}?\n\nA notification cannot be recalled.`
      : `Send a "signal updated" notification for ${signal.symbol} to ${who}?\n\nA notification cannot be recalled.`
    if (!window.confirm(again ? `${message}\n\nThis was already sent once. Send it again?` : message)) return

    setBusyId(signal.id)
    setError(null)
    setNotice(null)
    try {
      const { ok, data } = await postGuarded(
        `/api/signals/${signal.id}/notify`,
        { kind, ...(again ? { send_again: true } : {}) },
      )
      if (!ok) throw new Error(data.error || 'Failed to send notification')
      setNotice(`Notification for ${signal.symbol} sent to ${data.audience} phone(s).`)
      await load()
      refreshPush()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to send notification')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="section">
      <div className="section-header">
        <div className="section-title">Signals</div>
      </div>

      {error && <div className="error-bar">{error}</div>}
      {notice && <div className="notice-bar">{notice}</div>}
      {form.id && form.live && (
        <div className="warn-bar">
          This signal is live. Saving changes what app users see. No notification is sent.
        </div>
      )}
      {form.id && form.reached && (
        <div className="warn-bar">
          "Entry reached" is live in the app for this signal. If you change the entry price or the stock,
          it is withdrawn from the app at save time and entry detection starts again.
        </div>
      )}
      {!form.id && (
        <div className="notice-bar">
          New signals are saved as Drafts and are not visible in the app until you publish them.
        </div>
      )}

      <form className="clean-form" onSubmit={addSignal}>
        <div className="clean-form-grid">
          <StockPicker
            label="Stock"
            value={form.symbol}
            onChange={symbol => setForm(f => ({ ...f, symbol }))}
            required
          />
          <label className="field">
            <span>Entry price</span>
            <input
              type="number"
              step="0.01"
              value={form.entry_price}
              onChange={e => setForm(f => ({ ...f, entry_price: e.target.value }))}
              placeholder="₹"
            />
          </label>
          <label className="field">
            <span>Exit price</span>
            <input
              type="number"
              step="0.01"
              value={form.exit_price}
              onChange={e => setForm(f => ({ ...f, exit_price: e.target.value }))}
              placeholder="₹"
            />
          </label>
          <label className="field">
            <span>Stop loss</span>
            <input
              type="number"
              step="0.01"
              value={form.stop_loss}
              onChange={e => setForm(f => ({ ...f, stop_loss: e.target.value }))}
              placeholder="₹"
            />
          </label>
        </div>

        <div className="clean-form-footer">
          <label className="switch-field">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={e => setForm(f => ({ ...f, enabled: e.target.checked }))}
            />
            <span className="switch-track" aria-hidden="true"><span className="switch-thumb" /></span>
            <span className="switch-label">Enabled</span>
          </label>

          <div className="clean-form-actions">
            {form.id && <button type="button" className="theme-btn" onClick={resetForm}>Cancel</button>}
            <button className="rescan-btn" type="submit" disabled={submitting}>
              {submitting ? 'Saving...' : form.id ? 'Update signal' : 'Add signal'}
            </button>
          </div>
        </div>
      </form>

      {loading ? (
        <div className="empty-state">Loading signals...</div>
      ) : signals.length === 0 ? (
        <div className="empty-state">No signals yet. Add your first stock above.</div>
      ) : (
        <div className="signal-list">
          {signals.map(s => {
            const state = signalState(s)
            const busy = busyId === s.id
            return (
            <div className={`signal-row${state === 'Hidden' ? ' disabled' : ''}`} key={s.id}>
              <div className="signal-row-main">
                <span className="card-sym">{s.symbol}</span>
                <span className={`card-val ${(s.change_pct ?? 0) >= 0 ? 'g' : 'r'}`}>
                  {inr(s.last_price)} · {pct(s.change_pct)}
                </span>
                {state === 'Hidden' && <span className="tag-disabled">Hidden</span>}
                {state === 'Draft' && <span className="tag-draft">Draft</span>}
                {state === 'Published' && <span className="tag-published">Published</span>}
                {state === 'Published' && s.push_state?.announced && (
                  <span className="tag-sent">
                    Notified{s.push_state.announced_at ? ` ${whenIST(s.push_state.announced_at)}` : ''}
                  </span>
                )}
                {state === 'Published' && s.push_state?.changed_since && (
                  <span className="tag-changed">Changed since last notification</span>
                )}
                {entryStates[s.id]?.armed && (
                  <span className="tag-sent">
                    Armed · {entryStates[s.id].direction === 'up' ? 'waiting to rise to entry' : 'waiting to fall to entry'}
                  </span>
                )}
                {entryStates[s.id]?.done && <span className="tag-disabled">Detection paused</span>}
                {entryStates[s.id]?.hit && !s.entry_reached_at && (
                  <span className="tag-hit">Entry hit {whenIST(entryStates[s.id].hit!.detected_at)}</span>
                )}
                {s.entry_reached_at && (
                  <span className="tag-published">Entry reached live {whenIST(s.entry_reached_at)}</span>
                )}
              </div>
              <div className="signal-row-prices">
                <PriceTag label="Entry" value={s.entry_price} />
                <PriceTag label="Exit" value={s.exit_price} />
                <PriceTag label="Stop loss" value={s.stop_loss} />
              </div>
              <div className="signal-row-actions">
                {state === 'Draft' && (
                  <button className="rescan-btn" disabled={busy} onClick={() => openPublish(s)}>
                    {busy ? 'Working...' : 'Publish to app'}
                  </button>
                )}
                {state === 'Published' && (
                  <button className="theme-btn" disabled={busy} onClick={() => unpublish(s)}>
                    {busy ? 'Working...' : 'Unpublish'}
                  </button>
                )}
                {state === 'Published' && !s.push_state?.announced && (
                  <button className="rescan-btn" disabled={busy} onClick={() => sendNotification(s, 'new')}>
                    Send notification
                  </button>
                )}
                {state === 'Published' && s.push_state?.announced && (
                  <button
                    className="theme-btn"
                    disabled={busy}
                    title="Already announced. Sends it again after an extra confirmation."
                    onClick={() => sendNotification(s, 'new', true)}
                  >
                    Send again
                  </button>
                )}
                {state === 'Published' && s.push_state?.announced && s.push_state.changed_since && (
                  <button className="rescan-btn" disabled={busy} onClick={() => sendNotification(s, 'update')}>
                    Send update notification
                  </button>
                )}
                <button className="theme-btn" onClick={() => editSignal(s)}>Edit</button>
                <button className="theme-btn" onClick={() => removeSignal(s.id)}>Remove</button>
              </div>
            </div>
            )
          })}
        </div>
      )}

      {publishTarget && (
        <div className="modal-backdrop" onClick={() => setPublishTarget(null)}>
          <div className="modal-card" onClick={e => e.stopPropagation()}>
            <div className="section-title">Publish {publishTarget.symbol}?</div>
            <p className="modal-text">
              Users of the app will see this signal the next time they open Signals.
            </p>
            <label className="check-row">
              <input
                type="checkbox"
                checked={alsoNotify}
                onChange={e => setAlsoNotify(e.target.checked)}
              />
              <span>
                Also send a phone notification
                {pushStatus
                  ? ` (${pushStatus.signal_devices} phone${pushStatus.signal_devices === 1 ? '' : 's'} with signal alerts on)`
                  : ''}
              </span>
            </label>
            <p className="modal-text">
              {alsoNotify
                ? 'A notification cannot be recalled once sent.'
                : 'No notification will be sent.'}
            </p>
            <div className="clean-form-actions">
              <button className="theme-btn" onClick={() => setPublishTarget(null)}>Cancel</button>
              <button className="rescan-btn" onClick={confirmPublish}>
                {alsoNotify ? 'Publish and notify' : 'Publish'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function PriceTag({ label, value }: { label: string; value?: number | null }) {
  return (
    <span className="price-tag">
      <small>{label}</small>
      <strong>{value != null ? inr(value) : '—'}</strong>
    </span>
  )
}

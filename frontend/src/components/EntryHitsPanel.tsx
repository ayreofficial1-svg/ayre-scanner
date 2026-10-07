import { useCallback, useEffect, useState } from 'react'
import type { EntryHit, EntryHitsResponse } from '../types'
import { inr } from '../utils'
import { whenIST } from '../pushApi'

// ─────────────────────────────────────────────────────────────────────────────
// EntryHitsPanel (admin only)
//
// Lists the entry touches the backend detected by itself. Detection is
// admin-only: nothing here reaches app users until you press a button and
// confirm. The panel asks OUR backend every 12 s while the tab is visible
// (never Fyers). The beep, the sound switch and the tab-title counter live in
// EntryHitsWatcher (page header, runs on every tab). Actions: Publish entry reached (admin signals), Create draft
// signal (scanner hits), Dismiss, Re-arm.
// ─────────────────────────────────────────────────────────────────────────────

const POLL_MS = 12000

const STATUS_LABEL: Record<EntryHit['status'], string> = {
  new: 'New',
  reviewed: 'Reviewed',
  dismissed: 'Dismissed',
  draft_created: 'Draft created',
  entry_reached_published: 'Entry reached published',
}

type Body = Record<string, unknown> & { error?: string; code?: string; reasons?: string[] }

async function post(url: string, body: Record<string, unknown> = {}) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = await res.json().catch(() => ({})) as Body
  return { ok: res.ok, status: res.status, data }
}

export default function EntryHitsPanel() {
  const [data, setData] = useState<EntryHitsResponse | null>(null)
  const [days, setDays] = useState(1)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [target, setTarget] = useState<EntryHit | null>(null)
  const [alsoNotify, setAlsoNotify] = useState(false)
  const [understand, setUnderstand] = useState(false)

  const load = useCallback(async () => {
    try {
      const res = await fetch(`/api/entries/hits?days=${days}`)
      const body = await res.json().catch(() => ({})) as EntryHitsResponse & { error?: string }
      if (!res.ok) throw new Error(body.error || 'Failed to load entry hits')
      setData(body)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load entry hits')
    }
  }, [days])

  useEffect(() => {
    load()
    const timer = window.setInterval(() => { if (!document.hidden) load() }, POLL_MS)
    const onVisible = () => { if (!document.hidden) load() }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [load])

  const run = async (hit: EntryHit, fn: () => Promise<void>) => {
    if (busyId) return
    setBusyId(hit.id)
    setError(null)
    setNotice(null)
    try { await fn() }
    catch (err) { setError(err instanceof Error ? err.message : 'Action failed') }
    finally { setBusyId(null) }
  }

  const dismiss = (hit: EntryHit) => run(hit, async () => {
    if (!window.confirm(`Dismiss the ${hit.symbol} hit? Detection for this signal stops until you re-arm it or change its entry price.`)) return
    const { ok, data: d } = await post(`/api/entries/hits/${hit.id}/dismiss`)
    if (!ok) throw new Error(d.error || 'Failed to dismiss')
    await load()
  })

  const rearm = (hit: EntryHit) => run(hit, async () => {
    if (!hit.signal_id) return
    const { ok, data: d } = await post(`/api/signals/${hit.signal_id}/rearm`)
    if (!ok) throw new Error(d.error || 'Failed to re-arm')
    setNotice(`${hit.symbol} re-armed. Detection starts afresh on the next sweep.`)
    await load()
  })

  const createDraft = (hit: EntryHit) => run(hit, async () => {
    if (!window.confirm(`Create a Draft signal for ${hit.symbol}?\n\nIt is not visible in the app and nothing is sent. You review it in the Signals panel and publish it yourself.`)) return
    const { ok, data: d } = await post(`/api/entries/hits/${hit.id}/create-draft`)
    if (!ok) throw new Error(d.error || 'Failed to create draft')
    setNotice(`Draft signal created for ${hit.symbol}. Find it in the Signals panel below.`)
    await load()
  })

  const openPublish = (hit: EntryHit) => {
    if (busyId) return
    setAlsoNotify(false)           // always off by default
    setUnderstand(false)
    setTarget(hit)
    load()
  }

  const confirmPublish = async (sendAgain = false) => {
    const hit = target
    if (!hit || busyId) return
    setBusyId(hit.id)
    setError(null)
    setNotice(null)
    try {
      const { ok, data: d } = await post(`/api/entries/hits/${hit.id}/publish-entry-reached`, {
        confirm: true,
        notify: alsoNotify,
        acknowledge_stale: understand,
        ...(sendAgain ? { send_again: true } : {}),
      })
      if (!ok) throw new Error(d.error || 'Failed to publish entry reached')
      const n = d.notification as { ok: boolean; audience?: number; error?: string; code?: string } | undefined
      if (n && !n.ok && n.code === 'already_sent' && !sendAgain) {
        if (window.confirm(`${n.error}\n\nSend it again anyway? A notification cannot be recalled.`)) {
          setBusyId(null)
          await confirmPublish(true)
          return
        }
      }
      setTarget(null)
      setNotice(
        !alsoNotify
          ? `Entry reached for ${hit.symbol} is published. App users see it the next time they open Signals. No notification was sent.`
          : n?.ok
            ? `Entry reached for ${hit.symbol} is published and a notification was sent to ${n.audience} phone(s).`
            : `Entry reached for ${hit.symbol} is published, but the notification was NOT sent: ${n?.error ?? 'unknown reason'}`,
      )
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to publish entry reached')
    } finally {
      setBusyId(null)
    }
  }

  const hits = data?.hits ?? []
  const needsAck = !!target && target.stale && !target.entry_reached_live
  const marketClosed = data ? !data.market_open : false
  const canSubmit = !!target && (!needsAck || understand) && !(alsoNotify && marketClosed)
    && (!target.entry_reached_live || alsoNotify)

  return (
    <div className="section">
      <div className="section-header">
        <div className="section-title">Entry hits</div>
        <div className="section-sub">Detected automatically. Admin only. Nothing reaches the app until you publish it.</div>
      </div>

      {error && <div className="error-bar">{error}</div>}
      {notice && <div className="notice-bar">{notice}</div>}

      <div className="signal-row-prices" style={{ marginBottom: '1rem' }}>
        <span className="price-tag">
          <small>Detection</small>
          <strong>{data ? (data.detection.enabled ? 'On' : 'Off') : '—'}</strong>
        </span>
        <span className="price-tag">
          <small>Armed (signals)</small>
          <strong>{data?.detection.armed_admin ?? '—'}</strong>
        </span>
        <span className="price-tag">
          <small>Market</small>
          <strong>{data ? (data.market_open ? 'Open' : 'Closed') : '—'}</strong>
        </span>
        <select value={days} onChange={e => setDays(Number(e.target.value))} className="theme-btn">
          <option value={1}>Today</option>
          <option value={3}>Last 3 days</option>
          <option value={7}>Last 7 days</option>
        </select>
        <button className="theme-btn" onClick={load}>Refresh</button>
      </div>

      {!data ? (
        <div className="empty-state">Loading entry hits...</div>
      ) : hits.length === 0 ? (
        <div className="empty-state">
          {data.detection.enabled ? 'No entry hits yet.' : 'Entry detection is switched off on the server.'}
        </div>
      ) : (
        <div className="signal-list eh-list">
          {hits.map(h => {
            const busy = busyId === h.id
            const done = h.status === 'dismissed' || h.status === 'entry_reached_published'
            return (
              <div className={`signal-row eh-row${h.status === 'new' ? ' hit-new' : ''}`} key={h.id}>
                <div className="eh-id">
                  <span className="eh-sym">{h.symbol}</span>
                  <div className="eh-tags">
                  <span className="tag-hit">{h.kind === 'admin' ? 'Admin signal' : 'Scanner'}</span>
                  <span className="tag-disabled">{STATUS_LABEL[h.status]}</span>
                  {h.signal_state && (
                    <span className={h.signal_state === 'Published' ? 'tag-published' : h.signal_state === 'Draft' ? 'tag-draft' : 'tag-disabled'}>
                      Signal {h.signal_state}
                    </span>
                  )}
                  {h.entry_reached_live && <span className="tag-published">Live in app</span>}
                  {h.extended && <span className="tag-changed">Extended</span>}
                  {h.late_start && <span className="tag-draft">Late start</span>}
                  </div>
                </div>
                <div className="eh-metrics">
                  <span className="price-tag eh-metric"><small>Level</small><strong>{h.level != null ? inr(h.level) : '—'}</strong></span>
                  <span className="price-tag eh-metric"><small>Direction</small><strong>{h.direction === 'up' ? 'Rose to' : h.direction === 'down' ? 'Fell to' : 'Touch'}</strong></span>
                  <span className="price-tag eh-metric"><small>Reached</small><strong>{whenIST(h.exact_minute || h.detected_at)}</strong></span>
                  <span className="price-tag eh-metric">
                    <small>Age</small>
                    <strong>{h.age_minutes == null ? '—' : h.age_minutes < 1 ? 'just now' : `${h.age_minutes} min ago`}</strong>
                  </span>
                  <span className="price-tag eh-metric"><small>At detection</small><strong>{inr(h.price_at_detection)}</strong></span>
                  <span className="price-tag eh-metric"><small>Price now</small><strong>{inr(h.price_now)}</strong></span>
                  <span className="price-tag eh-metric"><small>Source</small><strong>{h.source ?? '—'}</strong></span>
                </div>
                <div className="signal-row-actions eh-actions">
                  {h.kind === 'admin' && !h.entry_reached_live && h.status !== 'dismissed' && (
                    <button className="rescan-btn" disabled={busy} onClick={() => openPublish(h)}>
                      {busy ? 'Working...' : 'Publish entry reached…'}
                    </button>
                  )}
                  {h.kind === 'admin' && h.entry_reached_live && (
                    <button className="theme-btn" disabled={busy} onClick={() => openPublish(h)}>
                      Send notification…
                    </button>
                  )}
                  {h.kind === 'scanner' && !h.draft_signal_id && (
                    <button className="rescan-btn" disabled={busy} onClick={() => createDraft(h)}>
                      Create draft signal
                    </button>
                  )}
                  {!done && (
                    <button className="theme-btn" disabled={busy} onClick={() => dismiss(h)}>Dismiss</button>
                  )}
                  {h.kind === 'admin' && h.signal_id && (done || h.status === 'reviewed') && (
                    <button className="theme-btn" disabled={busy} onClick={() => rearm(h)}>Re-arm</button>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {target && (
        <div className="modal-backdrop" onClick={() => setTarget(null)}>
          <div className="modal-card" onClick={e => e.stopPropagation()}>
            <div className="section-title">
              {target.entry_reached_live ? 'Send notification for' : 'Publish entry reached for'} {target.symbol}?
            </div>
            <p className="modal-text">
              Level {inr(target.level)} was reached {target.age_minutes == null ? '' : target.age_minutes < 1 ? 'just now' : `${target.age_minutes} min ago`}
              {' '}at {inr(target.price_at_detection)}. Price now {inr(target.price_now)}.
              {target.entry_reached_live ? '' : ' App users will see "entry reached" on this signal the next time they open Signals.'}
            </p>

            {target.stale && !target.entry_reached_live && (
              <div className="warn-bar" style={{ marginBottom: 0 }}>
                <strong>This hit is old or extended.</strong>
                <ul className="warn-list">
                  {target.stale_reasons.map(r => <li key={r}>{r}</li>)}
                </ul>
                <label className="check-row" style={{ marginTop: '0.6rem' }}>
                  <input type="checkbox" checked={understand} onChange={e => setUnderstand(e.target.checked)} />
                  <span>I understand, publish it anyway</span>
                </label>
              </div>
            )}

            <label className="check-row">
              <input type="checkbox" checked={alsoNotify} onChange={e => setAlsoNotify(e.target.checked)} />
              <span>Also send a phone notification (phones with signal alerts on)</span>
            </label>
            {alsoNotify && marketClosed && (
              <div className="error-bar">The market is closed, so a notification cannot be sent. Untick the box to publish without one.</div>
            )}
            <p className="modal-text">
              {alsoNotify ? 'A notification cannot be recalled once sent.' : 'No notification will be sent.'}
            </p>
            <div className="clean-form-actions">
              <button className="theme-btn" onClick={() => setTarget(null)}>Cancel</button>
              <button
                className="rescan-btn"
                disabled={!canSubmit || busyId === target.id}
                onClick={() => confirmPublish()}
              >
                {busyId === target.id ? 'Working...' : alsoNotify ? (target.entry_reached_live ? 'Send notification' : 'Publish and notify') : 'Publish'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

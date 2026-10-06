import { useState } from 'react'
import type { FormEvent } from 'react'
import { postGuarded, usePushStatus, whenIST } from '../pushApi'

// ─────────────────────────────────────────────────────────────────────────────
// NotificationsPanel
//
// Custom message to every registered phone (also the quickest end-to-end test
// of Firebase), push status (configured, phones, today's sends vs the daily
// cap) and the audit log of every manual send. Nothing here is automatic.
// ─────────────────────────────────────────────────────────────────────────────

const TYPE_LABEL: Record<string, string> = {
  signal: 'New signal',
  signal_update: 'Signal update',
  exit: 'Exit call',
  general: 'Custom message',
}

export default function NotificationsPanel() {
  const { status, refresh } = usePushStatus()
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const send = async (event: FormEvent) => {
    event.preventDefault()
    if (submitting) return
    const t = title.trim()
    const b = body.trim()
    if (!t || !b) {
      setError('Title and message are both required')
      return
    }
    setError(null)
    setNotice(null)

    const who = status ? `${status.devices} registered phone(s)` : 'every registered phone'
    if (!window.confirm(`Send "${t}" to ${who}?\n\nA notification cannot be recalled.`)) return

    setSubmitting(true)
    try {
      const { ok, data } = await postGuarded('/api/push/send', { title: t, body: b })
      if (!ok) throw new Error(data.error || 'Failed to send notification')
      setTitle('')
      setBody('')
      setNotice(`Message queued for ${data.devices} phone(s).`)
      // The send finishes in the background; refresh shortly after.
      await refresh()
      window.setTimeout(refresh, 3000)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to send notification')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="section">
      <div className="section-header">
        <div className="section-title">Notifications</div>
        <div className="section-sub">Every notification is sent by hand</div>
      </div>

      {error && <div className="error-bar">{error}</div>}
      {notice && <div className="notice-bar">{notice}</div>}

      {status && (
        <div className="signal-row-prices" style={{ marginBottom: '1rem' }}>
          <span className="price-tag">
            <small>Push</small>
            <strong>{status.configured ? 'Configured' : 'Not configured'}</strong>
          </span>
          <span className="price-tag">
            <small>Phones</small>
            <strong>{status.devices}</strong>
          </span>
          <span className="price-tag">
            <small>With signal alerts</small>
            <strong>{status.signal_devices}</strong>
          </span>
          <span className="price-tag">
            <small>Sent today</small>
            <strong>{status.sends_today} / {status.daily_cap}</strong>
          </span>
        </div>
      )}

      <form className="clean-form" onSubmit={send}>
        <div className="clean-form-grid">
          <label className="field">
            <span>Title</span>
            <input
              type="text"
              maxLength={100}
              value={title}
              onChange={e => setTitle(e.target.value)}
              placeholder="Short heading"
              required
            />
          </label>
          <label className="field">
            <span>Message</span>
            <input
              type="text"
              maxLength={240}
              value={body}
              onChange={e => setBody(e.target.value)}
              placeholder="One or two short lines"
              required
            />
          </label>
        </div>
        <div className="clean-form-footer">
          <span className="switch-label">Goes to every registered phone</span>
          <div className="clean-form-actions">
            <button className="rescan-btn" type="submit" disabled={submitting}>
              {submitting ? 'Sending...' : 'Send message'}
            </button>
          </div>
        </div>
      </form>

      <div className="section-header" style={{ marginTop: '1.5rem' }}>
        <div className="section-title">Recent sends</div>
        <button className="theme-btn" onClick={refresh}>Refresh</button>
      </div>
      {!status || status.audit.length === 0 ? (
        <div className="empty-state">Nothing sent yet.</div>
      ) : (
        <div className="signal-list">
          {status.audit.map(a => (
            <div className="signal-row" key={a.id}>
              <div className="signal-row-main">
                <span className="card-sym">{a.key || '—'}</span>
                <span className="tag-disabled">{TYPE_LABEL[a.type] ?? a.type}</span>
                {a.status === 'refused' && <span className="tag-changed">Refused</span>}
                {a.status === 'sending' && <span className="tag-draft">Sending</span>}
                {a.status === 'done' && <span className="tag-sent">Sent</span>}
              </div>
              <div className="signal-row-prices">
                <span className="price-tag"><small>When</small><strong>{whenIST(a.at)}</strong></span>
                <span className="price-tag"><small>By</small><strong>{a.admin ?? '—'}</strong></span>
                {a.status !== 'refused' && (
                  <span className="price-tag">
                    <small>Delivered</small>
                    <strong>{a.sent ?? 0} / {a.audience ?? 0}{a.failed ? ` (${a.failed} failed)` : ''}</strong>
                  </span>
                )}
                {a.reason && (
                  <span className="price-tag"><small>Reason</small><strong>{a.reason}</strong></span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

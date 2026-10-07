import type { PushStatus } from '../types'
import { whenIST } from '../pushApi'

// ─────────────────────────────────────────────────────────────────────────────
// SendLogPanel — shown on the Notifications tab as "Sent notification log"
//
// The audit log of every manual send and every refusal (signals, entry
// reached, exit alerts, custom messages): when, by whom, and how many phones
// it reached. Read-only; the status comes from the Notifications tab.
// ─────────────────────────────────────────────────────────────────────────────

const TYPE_LABEL: Record<string, string> = {
  signal: 'New signal',
  signal_update: 'Signal update',
  entry_reached: 'Entry reached',
  exit: 'Exit call',
  general: 'Custom message',
}

export default function SendLogPanel({
  status,
  refresh,
}: {
  status: PushStatus | null
  refresh: () => Promise<void> | void
}) {
  return (
    <div className="section" id="panel-sent-log">
      <div className="section-header">
        <div className="section-title">Sent notification log</div>
        <button className="theme-btn" onClick={() => { refresh() }}>Refresh</button>
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

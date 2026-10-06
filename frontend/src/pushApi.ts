import { useCallback, useEffect, useState } from 'react'
import type { PushStatus } from './types'

// Shared helpers for every manual notification button.

export const whenIST = (iso?: string | null) => {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString('en-IN', {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

export interface GuardedResult {
  ok: boolean
  status: number
  data: Record<string, unknown> & { error?: string; code?: string }
}

/**
 * POST a manual-send request. If the server answers "duplicate" or
 * "already sent", ask once whether to send again and retry with
 * send_again = true. The server still enforces every rule.
 */
export async function postGuarded(
  url: string,
  body: Record<string, unknown>,
): Promise<GuardedResult> {
  const call = async (extra: Record<string, unknown>): Promise<GuardedResult> => {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...body, confirm: true, ...extra }),
    })
    const data = await res.json().catch(() => ({})) as GuardedResult['data']
    return { ok: res.ok, status: res.status, data }
  }

  const first = await call({})
  if (!first.ok && (first.data.code === 'duplicate' || first.data.code === 'already_sent')) {
    const again = window.confirm(
      `${first.data.error}\n\nSend it again anyway? A notification cannot be recalled.`,
    )
    if (again) return call({ send_again: true })
  }
  return first
}

export function usePushStatus() {
  const [status, setStatus] = useState<PushStatus | null>(null)
  const refresh = useCallback(async () => {
    try {
      const res = await fetch('/api/push/status')
      if (!res.ok) return
      setStatus(await res.json() as PushStatus)
    } catch { /* status is informational only */ }
  }, [])
  useEffect(() => { refresh() }, [refresh])
  return { status, refresh }
}

import { useCallback } from 'react'
import { usePushStatus } from '../pushApi'
import SignalsPanel from './SignalsPanel'
import type { SignalFocus } from './SignalsPanel'
import ExitCallsPanel from './ExitCallsPanel'
import CustomMessagePanel from './CustomMessagePanel'
import SendLogPanel from './SendLogPanel'

// ─────────────────────────────────────────────────────────────────────────────
// NotificationsTab
//
// Everything that publishes to the app or sends a phone notification lives
// here, top to bottom:
//   1. Draft & publish signals     (was: the Signals panel)
//   2. Send exit alert             (was: Exit calls)
//   3. Custom message to phones    (was: the old Notifications panel)
//   4. Sent notification log       (was: Recent sends)
//
// "Draft signal" on an Entry hit (Signals tab) lands here with `focus` set, which
// opens that draft in panel 1. The push status is loaded once here and shared, so
// the log and the status counters refresh right after any send from panels 1–2.
// ─────────────────────────────────────────────────────────────────────────────

export default function NotificationsTab({
  focus,
  onFocusDone,
}: {
  focus: SignalFocus | null
  onFocusDone: () => void
}) {
  const { status, refresh } = usePushStatus()

  // A send finishes in the background on the server: refresh now and again shortly after.
  const afterSend = useCallback(() => {
    refresh()
    window.setTimeout(refresh, 3000)
  }, [refresh])

  return (
    <>
      <SignalsPanel focus={focus} onFocusDone={onFocusDone} onPushActivity={afterSend} />
      <ExitCallsPanel onPushActivity={afterSend} />
      <CustomMessagePanel status={status} refresh={refresh} />
      <SendLogPanel status={status} refresh={refresh} />
    </>
  )
}

import { useState, useEffect } from 'react'

export default function Clock() {
  const [time, setTime] = useState('')

  useEffect(() => {
    const tick = () => {
      const n = new Date()
      // Always show IST (Asia/Kolkata), regardless of the viewer's own
      // device/browser timezone.
      setTime(
        n.toLocaleTimeString('en-GB', {
          timeZone: 'Asia/Kolkata',
          hour:   '2-digit',
          minute: '2-digit',
          second: '2-digit',
        })
      )
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])

  return <span className="live-time">{time}</span>
}

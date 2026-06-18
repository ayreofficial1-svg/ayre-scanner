import { useState, useEffect } from 'react'

export default function Clock() {
  const [time, setTime] = useState('')

  useEffect(() => {
    const tick = () => {
      const n = new Date()
      setTime(
        [n.getHours(), n.getMinutes(), n.getSeconds()]
          .map(x => String(x).padStart(2, '0'))
          .join(':')
      )
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])

  return <span className="live-time">{time}</span>
}

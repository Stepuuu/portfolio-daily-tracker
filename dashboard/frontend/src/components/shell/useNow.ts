import { useEffect, useState } from 'react'

/** The current time, updated every `intervalMs` (for "3 秒前" and staleness checks). */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), intervalMs)
    return () => window.clearInterval(timer)
  }, [intervalMs])
  return now
}

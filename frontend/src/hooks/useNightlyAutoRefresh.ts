import { useEffect, useRef } from 'react'

const IDLE_REFRESH_MS = 30 * 60 * 1000
const CHECK_INTERVAL_MS = 60 * 1000
const ACTIVITY_EVENTS = ['mousemove', 'mousedown', 'keydown', 'scroll', 'touchstart', 'pointerdown'] as const

function isNightlyWindow(date: Date): boolean {
  const hour = date.getHours()
  return hour >= 2 && hour < 4
}

export function useNightlyAutoRefresh() {
  const lastActiveAtRef = useRef(Date.now())

  useEffect(() => {
    const markActive = () => {
      lastActiveAtRef.current = Date.now()
    }

    ACTIVITY_EVENTS.forEach((eventName) => {
      window.addEventListener(eventName, markActive, { passive: true })
    })

    const timer = window.setInterval(() => {
      const now = Date.now()
      if (isNightlyWindow(new Date(now)) && now - lastActiveAtRef.current >= IDLE_REFRESH_MS) {
        window.location.reload()
      }
    }, CHECK_INTERVAL_MS)

    return () => {
      window.clearInterval(timer)
      ACTIVITY_EVENTS.forEach((eventName) => {
        window.removeEventListener(eventName, markActive)
      })
    }
  }, [])
}

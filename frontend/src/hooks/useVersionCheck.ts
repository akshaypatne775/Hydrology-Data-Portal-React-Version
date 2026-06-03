import { useCallback, useEffect, useRef, useState } from 'react'
import { apiFetch } from '../lib/apiBase'

type VersionResponse = {
  version?: string
}

const VERSION_POLL_MS = 60_000

export function useVersionCheck() {
  const initialVersionRef = useRef<string | null>(null)
  const [updateAvailable, setUpdateAvailable] = useState(false)

  useEffect(() => {
    let cancelled = false

    const checkVersion = async () => {
      try {
        const response = await apiFetch('/api/version')
        if (!response.ok) return
        const data = (await response.json()) as VersionResponse
        const nextVersion = String(data.version || '').trim()
        if (!nextVersion || cancelled) return
        if (!initialVersionRef.current) {
          initialVersionRef.current = nextVersion
          return
        }
        if (initialVersionRef.current !== nextVersion) {
          setUpdateAvailable(true)
        }
      } catch {
        // Keep the app quiet if the backend is restarting.
      }
    }

    void checkVersion()
    const timer = window.setInterval(() => void checkVersion(), VERSION_POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [])

  const refreshNow = useCallback(() => {
    window.location.reload()
  }, [])

  return { updateAvailable, refreshNow }
}

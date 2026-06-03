export const DROID_LOCATION_KEY = 'droid:location'

export type DroidLocation = {
  lat: number
  lng: number
  accuracy?: number
  capturedAt: string
  deviceLabel: string
}

export function getDeviceLabel(): string {
  const ua = navigator.userAgent
  const os = ua.includes('Windows') ? 'Windows' : ua.includes('Mac OS') ? 'macOS' : ua.includes('Android') ? 'Android' : ua.includes('iPhone') || ua.includes('iPad') ? 'iOS' : 'Unknown OS'
  const browser = ua.includes('Edg/') ? 'Edge' : ua.includes('Chrome/') ? 'Chrome' : ua.includes('Firefox/') ? 'Firefox' : ua.includes('Safari/') ? 'Safari' : 'Browser'
  return `${browser} on ${os}`
}

export function readCurrentDeviceLocation(): DroidLocation | null {
  try {
    const raw = window.localStorage.getItem(DROID_LOCATION_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<DroidLocation>
    if (
      typeof parsed.lat !== 'number' ||
      typeof parsed.lng !== 'number' ||
      parsed.deviceLabel !== getDeviceLabel()
    ) {
      return null
    }
    return {
      lat: parsed.lat,
      lng: parsed.lng,
      accuracy: typeof parsed.accuracy === 'number' ? parsed.accuracy : undefined,
      capturedAt: typeof parsed.capturedAt === 'string' ? parsed.capturedAt : '',
      deviceLabel: parsed.deviceLabel,
    }
  } catch {
    return null
  }
}

export function saveCurrentDeviceLocation(coords: GeolocationCoordinates): void {
  const payload: DroidLocation = {
    lat: coords.latitude,
    lng: coords.longitude,
    accuracy: coords.accuracy,
    capturedAt: new Date().toISOString(),
    deviceLabel: getDeviceLabel(),
  }
  window.localStorage.setItem(DROID_LOCATION_KEY, JSON.stringify(payload))
}

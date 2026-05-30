import { useState, type PropsWithChildren } from 'react'
import { readCurrentDeviceLocation, saveCurrentDeviceLocation } from '../utils/locationSession'
import './Auth/AuthScreen.css'

type LocationState = 'checking' | 'granted' | 'blocked'

export default function LocationGate({ children }: PropsWithChildren) {
  const [state, setState] = useState<LocationState>(() => (readCurrentDeviceLocation() ? 'granted' : 'blocked'))
  const [message, setMessage] = useState('Location access is required to open the portal. Click Allow Location when you are ready.')

  const requestLocation = () => {
    setState('checking')
    if (!navigator.geolocation) {
      setMessage('This browser does not support location access. Use Chrome, Edge, or another supported browser.')
      setState('blocked')
      return
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        saveCurrentDeviceLocation(pos.coords)
        setState('granted')
      },
      () => {
        setMessage('Please allow location permission. Portal access is blocked until location is allowed.')
        setState('blocked')
      },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 },
    )
  }

  if (state === 'granted') return <>{children}</>

  return (
    <div className="auth-root">
      <div className="auth-card" role="alertdialog" aria-label="Location required">
        <div className="auth-header">
          <p className="auth-kicker">Droid Cloud Security</p>
          <h1>Location Required</h1>
          <p>{message}</p>
        </div>
        <button type="button" className="auth-submit" onClick={requestLocation}>
          {state === 'checking' ? 'Checking Location...' : 'Allow Location'}
        </button>
      </div>
    </div>
  )
}

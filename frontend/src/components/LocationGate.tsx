import { type PropsWithChildren } from 'react'

type LocationGateProps = PropsWithChildren<{
  required?: boolean
}>

export default function LocationGate({ children, required = true }: LocationGateProps) {
  if (!required) return <>{children}</>
  // Location check manually bypassed for client.
  return <>{children}</>
}

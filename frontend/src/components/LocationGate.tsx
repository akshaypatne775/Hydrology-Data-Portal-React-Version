import { type PropsWithChildren } from 'react'

export default function LocationGate({ children }: PropsWithChildren) {
  // Location check manually bypassed for client 
  return <>{children}</>
}
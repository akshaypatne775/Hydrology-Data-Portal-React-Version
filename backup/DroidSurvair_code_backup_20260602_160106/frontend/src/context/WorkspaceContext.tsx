import {
  createContext,
  useContext,
  type PropsWithChildren,
} from 'react'
import { useWorkspaceState } from '../hooks/useWorkspaceState'

type WorkspaceContextValue = ReturnType<typeof useWorkspaceState>

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null)

export function WorkspaceProvider({ children }: PropsWithChildren) {
  const value = useWorkspaceState()
  return (
    <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>
  )
}

export function useWorkspaceContext(): WorkspaceContextValue {
  const ctx = useContext(WorkspaceContext)
  if (!ctx) {
    throw new Error('useWorkspaceContext must be used within WorkspaceProvider')
  }
  return ctx
}

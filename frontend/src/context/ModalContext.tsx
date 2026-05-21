import { createContext, useCallback, useContext, useMemo, useState, type PropsWithChildren } from 'react'
import './ModalContext.css'

type ModalRequest =
  | { kind: 'alert'; title: string; message: string; resolve: () => void }
  | { kind: 'confirm'; title: string; message: string; resolve: (value: boolean) => void }
  | { kind: 'prompt'; title: string; message: string; defaultValue?: string; resolve: (value: string | null) => void }

type ModalContextValue = {
  alert: (title: string, message: string) => Promise<void>
  confirm: (title: string, message: string) => Promise<boolean>
  prompt: (title: string, message: string, defaultValue?: string) => Promise<string | null>
}

const ModalContext = createContext<ModalContextValue | null>(null)

export function ModalProvider({ children }: PropsWithChildren) {
  const [request, setRequest] = useState<ModalRequest | null>(null)
  const [inputValue, setInputValue] = useState('')

  const alert = useCallback((title: string, message: string) => new Promise<void>((resolve) => {
    setRequest({ kind: 'alert', title, message, resolve: () => { setRequest(null); resolve() } })
  }), [])

  const confirm = useCallback((title: string, message: string) => new Promise<boolean>((resolve) => {
    setRequest({ kind: 'confirm', title, message, resolve: (value) => { setRequest(null); resolve(value) } })
  }), [])

  const prompt = useCallback((title: string, message: string, defaultValue = '') => new Promise<string | null>((resolve) => {
    setInputValue(defaultValue)
    setRequest({ kind: 'prompt', title, message, defaultValue, resolve: (value) => { setRequest(null); resolve(value) } })
  }), [])

  const value = useMemo(() => ({ alert, confirm, prompt }), [alert, confirm, prompt])

  return (
    <ModalContext.Provider value={value}>
      {children}
      {request ? (
        <div className="app-modal" role="dialog" aria-modal="true" aria-label={request.title}>
          <div className="app-modal__card">
            <h2>{request.title}</h2>
            <p>{request.message}</p>
            {request.kind === 'prompt' ? (
              <input
                className="app-modal__input"
                value={inputValue}
                onChange={(event) => setInputValue(event.target.value)}
                autoFocus
              />
            ) : null}
            <div className="app-modal__actions">
              {request.kind !== 'alert' ? (
                <button
                  type="button"
                  className="app-modal__button app-modal__button--ghost"
                  onClick={() => {
                    if (request.kind === 'confirm') request.resolve(false)
                    if (request.kind === 'prompt') request.resolve(null)
                  }}
                >
                  Cancel
                </button>
              ) : null}
              <button
                type="button"
                className="app-modal__button"
                onClick={() => {
                  if (request.kind === 'alert') request.resolve()
                  if (request.kind === 'confirm') request.resolve(true)
                  if (request.kind === 'prompt') request.resolve(inputValue)
                }}
              >
                {request.kind === 'alert' ? 'OK' : 'Continue'}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </ModalContext.Provider>
  )
}

export function useModal(): ModalContextValue {
  const ctx = useContext(ModalContext)
  if (!ctx) throw new Error('useModal must be used within ModalProvider')
  return ctx
}

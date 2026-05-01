import { useState, useEffect } from 'react'

// / admin token persisted to localStorage

export function useAdminToken() {
  const [token, setToken] = useState(() => localStorage.getItem('alphaloop-admin-token') || '')

  useEffect(() => {
    if (token) localStorage.setItem('alphaloop-admin-token', token)
  }, [token])

  const ensure = () => {
    if (token) return token
    const v = window.prompt('admin token (saved locally)')
    if (v) {
      setToken(v)
      return v
    }
    return null
  }

  const clear = () => {
    setToken('')
    localStorage.removeItem('alphaloop-admin-token')
  }

  return { token, ensure, clear }
}

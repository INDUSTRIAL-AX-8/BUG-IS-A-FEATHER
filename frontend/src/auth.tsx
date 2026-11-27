import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { api, setToken, setUnauthorizedHandler } from './api/client'
import type { Role, User } from './api/types'

interface AuthState {
  user: User | null
  ready: boolean
  login: (username: string, password: string) => Promise<void>
  logout: () => void
  can: (role: Role) => boolean
}

const LEVEL: Record<Role, number> = { VIEWER: 0, OPERATOR: 1, ADMIN: 2 }
const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [ready, setReady] = useState(false)

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setToken(null)
      setUser(null)
    })
    api
      .get<User>('/auth/me')
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setReady(true))
  }, [])

  const login = useCallback(async (username: string, password: string) => {
    const r = await api.post<{ token: string; user: User }>('/auth/login', { username, password })
    setToken(r.token)
    setUser(r.user)
  }, [])

  const logout = useCallback(() => {
    api.post('/auth/logout').catch(() => undefined)
    setToken(null)
    setUser(null)
  }, [])

  const can = useCallback((role: Role) => !!user && LEVEL[user.role] >= LEVEL[role], [user])

  return <AuthContext.Provider value={{ user, ready, login, logout, can }}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('AuthProvider 밖에서 useAuth 사용')
  return ctx
}

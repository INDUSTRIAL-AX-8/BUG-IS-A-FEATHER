import { useState } from 'react'
import { errorText } from '../api/client'
import { useAuth } from '../auth'
import { Field, Panel } from '../components/ui'

export function LoginPage() {
  const { login } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(username, password)
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login">
      <Panel title="불량 원인 추적 · 로그인">
        <form onSubmit={submit} className="stack">
          <Field label="아이디"><input autoFocus value={username} onChange={(e) => setUsername(e.target.value)} /></Field>
          <Field label="비밀번호"><input type="password" value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
          {error && <div className="notice error">{error}</div>}
          <button className="primary" type="submit" disabled={busy || !username}>로그인</button>
        </form>
      </Panel>
    </div>
  )
}

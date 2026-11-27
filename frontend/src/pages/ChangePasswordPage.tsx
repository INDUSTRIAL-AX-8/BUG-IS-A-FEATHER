import { useState } from 'react'
import { errorText } from '../api/client'
import { useAuth } from '../auth'
import { Field, PageHead, Panel } from '../components/ui'

const MIN_LENGTH = 8

/**
 * 본인 비밀번호 변경.
 * forced: 공개된 초기 비밀번호(admin1234 등)로 로그인한 직후. 바꾸기 전에는 서버가 다른 API 를 막으므로 이 화면만 보여준다.
 */
export function ChangePasswordPage({ forced = false }: { forced?: boolean }) {
  const { user, changePassword, logout } = useAuth()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)
  const [busy, setBusy] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setDone(false)
    if (next.length < MIN_LENGTH) return setError(`새 비밀번호는 ${MIN_LENGTH}자 이상이어야 합니다.`)
    if (next !== confirm) return setError('새 비밀번호와 확인 입력이 다릅니다.')
    setBusy(true)
    setError(null)
    try {
      await changePassword(current, next)
      setCurrent('')
      setNext('')
      setConfirm('')
      setDone(true)
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  const form = (
    <form onSubmit={submit} className="stack">
      {forced && (
        <div className="notice warn">
          <b>{user?.username}</b> 계정이 설치 문서에 공개된 초기 비밀번호를 쓰고 있습니다. 새 비밀번호를 정해야 사용할 수 있습니다.
        </div>
      )}
      <Field label="현재 비밀번호"><input type="password" autoFocus autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} /></Field>
      <Field label={`새 비밀번호 (${MIN_LENGTH}자 이상)`}><input type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} /></Field>
      <Field label="새 비밀번호 확인"><input type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} /></Field>
      {error && <div className="notice error">{error}</div>}
      {done && <div className="notice">비밀번호를 바꿨습니다.</div>}
      <button className="primary" type="submit" disabled={busy || !current || !next || !confirm}>비밀번호 변경</button>
      {forced && <button type="button" className="ghost" onClick={logout}>다른 계정으로 로그인</button>}
    </form>
  )

  if (forced) {
    return (
      <div className="login-center">
        <Panel title="불량 원인 추적 · 비밀번호 변경">{form}</Panel>
      </div>
    )
  }
  return (
    <>
      <PageHead title="비밀번호 변경" sub="본인 계정의 비밀번호를 바꿉니다. 변경 내역은 감사 로그에 남습니다." />
      <Panel style={{ maxWidth: 380 }}>{form}</Panel>
    </>
  )
}

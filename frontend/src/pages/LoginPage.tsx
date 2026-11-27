import { useState } from 'react'
import { errorText } from '../api/client'
import { useAuth } from '../auth'
import { Icon } from '../components/Icon'
import { Field } from '../components/ui'

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
      <section className="hero">
        <div className="glow" />
        <div>
          <div className="row" style={{ gap: 10, color: '#fff', fontWeight: 700, fontSize: 16 }}>
            <span style={{ width: 34, height: 34, borderRadius: 9, background: 'var(--primary)', display: 'grid', placeItems: 'center' }}><Icon name="logo" /></span>
            불량 원인 추적
          </div>
          <h1>불량이 왜 늘었는지,<br />데이터로 바로 답합니다.</h1>
          <p>생산 기록과 공정 조건, 검사 결과를 이어서 불량 원인을 자동으로 좁히고, 현장 판정과 경영 지표까지 한곳에서 관리합니다.</p>
        </div>
        <div className="feat">
          <div><Icon name="overview" />기간별 생산 · CAPA · 불량률 · 원인 Top</div>
          <div><Icon name="drivers" />조건 이탈 · 교란 요인을 가려낸 요인 분석</div>
          <div><Icon name="review" />자동 의심 → 실무자 판정 → 불량 데이터 적재</div>
          <div><Icon name="cube" />실시간 수집과 3D 디지털 트윈</div>
        </div>
      </section>
      <section className="form">
        <form onSubmit={submit}>
          <div>
            <h2>로그인</h2>
            <div className="muted" style={{ marginTop: 6 }}>사내 계정으로 로그인하세요.</div>
          </div>
          <Field label="아이디"><input autoFocus autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} /></Field>
          <Field label="비밀번호"><input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
          {error && <div className="notice error">{error}</div>}
          <button className="primary" type="submit" disabled={busy || !username}>{busy ? '로그인 중…' : '로그인'}</button>
          <div className="faint" style={{ fontSize: 12, textAlign: 'center' }}>폐쇄망 전용 · 외부로 데이터가 나가지 않습니다</div>
        </form>
      </section>
    </div>
  )
}

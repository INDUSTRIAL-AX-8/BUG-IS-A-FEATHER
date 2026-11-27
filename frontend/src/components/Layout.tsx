import { useEffect, useState, type ReactNode } from 'react'
import { NavLink } from 'react-router-dom'
import { useAuth } from '../auth'
import { ROLE_LABEL } from '../lib/format'
import { useTerms, type TermKey } from '../lib/terms'
import { ChatPanel } from './ChatPanel'
import { GlobalSearch } from './LotMemo'
import { useLive, useLiveRefresh } from '../lib/live'
import { api } from '../api/client'

const NAV: { to: string; label: string; num: string; term?: TermKey }[] = [
  { to: '/', label: '대시보드', num: '1' },
  { to: '/production', label: '생산 대시보드', num: '2' },
  { to: '/review', label: '불량 판정', num: '3' },
  { to: '/defect-data', label: '불량 데이터', num: '4' },
  { to: '/drivers', label: '요인 분석', num: '5', term: 'drivers' },
  { to: '/genealogy', label: '계보 그래프', num: '6', term: 'genealogyGraph' },
  { to: '/spc', label: '관리도', num: '7' },
  { to: '/defects', label: '불량 직접 등록', num: '8' },
  { to: '/master', label: '마스터 관리', num: '9' },
  { to: '/upload', label: '데이터 업로드', num: '10' },
  { to: '/settings', label: '설정', num: '11' },
]

export function Layout({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth()
  const { t } = useTerms()
  const live = useLive()
  const st = live.status
  const [pending, setPending] = useState(0)
  const loadPending = () => { api.get<{ pending_total: number }>('/suspects/summary').then((r) => setPending(r.pending_total)).catch(() => undefined) }
  useEffect(loadPending, [])
  useLiveRefresh(loadPending, 500)
  const [chatOpen, setChatOpen] = useState(false)
  return (
    <div className={`app ${chatOpen ? 'with-chat' : ''}`}>
      <header className="header">
        <span className="brand">불량 원인 추적</span>
        <GlobalSearch />
        <span className="spacer" />
        <NavLink to="/" className={`live-dot ${st?.running ? (st.phase === '이상' || st.phase === '정비' ? 'alarm' : 'on') : ''}`}
          title={st?.running ? `${st.equipment} 실시간 수집 중 (${st.phase})` : '실시간 수집 꺼짐 — 대시보드에서 시작'} style={{ textDecoration: 'none' }}>
          <i />{st?.running ? `실시간 ${st.phase}` : live.connected ? '실시간 대기' : '연결 끊김'}
        </NavLink>
        <button className="ghost" onClick={() => setChatOpen((v) => !v)} aria-pressed={chatOpen}>
          {chatOpen ? '어시스턴트 닫기' : '어시스턴트'}
        </button>
        <span className="user">
          {user?.display_name ?? user?.username} · {user ? ROLE_LABEL[user.role] : ''}
        </span>
        <button className="ghost" onClick={logout}>로그아웃</button>
      </header>
      <nav className="nav">
        {NAV.map((n) => (
          <NavLink key={n.to} to={n.to} end={n.to === '/'}>
            <span className="num">{n.num}</span>
            {n.term ? t(n.term) : n.label}
            {n.to === '/review' && pending > 0 && <span className="badge warn" style={{ marginLeft: 'auto', height: 18 }}>{pending}</span>}
          </NavLink>
        ))}
        <div className="sep" />
        <NavLink to="/notes"><span className="num" />메모 모아보기</NavLink>
        <NavLink to="/lots"><span className="num" />{t('lot')} 목록</NavLink>
        <NavLink to="/simulation"><span className="num" />3D 디지털 트윈</NavLink>
        <NavLink to="/accuracy"><span className="num" />정확도 지표</NavLink>
      </nav>
      <main className="main">{children}</main>
      <ChatPanel open={chatOpen} onClose={() => setChatOpen(false)} />
    </div>
  )
}

import { useState, type ReactNode } from 'react'
import { NavLink } from 'react-router-dom'
import { useAuth } from '../auth'
import { ROLE_LABEL } from '../lib/format'
import { useTerms, type TermKey } from '../lib/terms'
import { ChatPanel } from './ChatPanel'

const NAV: { to: string; label: string; num: string; term?: TermKey }[] = [
  { to: '/', label: '대시보드', num: '1' },
  { to: '/drivers', label: '요인 분석', num: '2', term: 'drivers' },
  { to: '/genealogy', label: '계보 그래프', num: '3', term: 'genealogyGraph' },
  { to: '/spc', label: '관리도', num: '4' },
  { to: '/defects', label: '불량 판정', num: '5', term: 'defectJudgment' },
  { to: '/master', label: '마스터 관리', num: '6' },
  { to: '/upload', label: '데이터 업로드', num: '7' },
  { to: '/settings', label: '설정', num: '8' },
]

export function Layout({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth()
  const { t } = useTerms()
  const [chatOpen, setChatOpen] = useState(false)
  return (
    <div className={`app ${chatOpen ? 'with-chat' : ''}`}>
      <header className="header">
        <span className="brand">불량 원인 추적</span>
        <span className="spacer" />
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
          </NavLink>
        ))}
        <div className="sep" />
        <NavLink to="/accuracy"><span className="num" />정확도 지표</NavLink>
        <NavLink to="/simulation"><span className="num" />3D 시뮬레이션</NavLink>
      </nav>
      <main className="main">{children}</main>
      <ChatPanel open={chatOpen} onClose={() => setChatOpen(false)} />
    </div>
  )
}

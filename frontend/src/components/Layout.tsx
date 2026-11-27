import { useEffect, useState, type ReactNode } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { useAuth } from '../auth'
import { ROLE_LABEL } from '../lib/format'
import { useTerms, type TermKey } from '../lib/terms'
import { ChatPanel } from './ChatPanel'
import { GlobalSearch } from './LotMemo'
import { useLive, useLiveRefresh } from '../lib/live'
import { api } from '../api/client'
import { Icon } from './Icon'
import { Toaster } from './Toast'

interface Item { to: string; label: string; icon: string; term?: TermKey; lotLabel?: boolean }
const GROUPS: { title: string; items: Item[] }[] = [
  { title: '현황', items: [
    { to: '/', label: '경영 현황', icon: 'overview' },
    { to: '/monitor', label: '현장 모니터링', icon: 'monitor' },
    { to: '/simulation', label: '3D 디지털 트윈', icon: 'cube' },
  ] },
  { title: '품질 관리', items: [
    { to: '/review', label: '불량 판정', icon: 'review' },
    { to: '/defect-data', label: '불량 데이터', icon: 'database' },
    { to: '/drivers', label: '요인 분석', icon: 'drivers', term: 'drivers' },
    { to: '/spc', label: '관리도', icon: 'spc' },
    { to: '/genealogy', label: '계보 그래프', icon: 'graph', term: 'genealogyGraph' },
    { to: '/accuracy', label: '정확도 지표', icon: 'target' },
  ] },
  { title: '제품 기록', items: [
    { to: '/lots', label: '목록', icon: 'list', lotLabel: true },
    { to: '/notes', label: '메모 모아보기', icon: 'note' },
  ] },
  { title: '데이터', items: [
    { to: '/defects', label: '불량 직접 등록', icon: 'plus' },
    { to: '/upload', label: '데이터 업로드', icon: 'upload' },
    { to: '/master', label: '마스터 관리', icon: 'master' },
    { to: '/settings', label: '설정', icon: 'settings' },
  ] },
]

function readCollapsed() {
  try { return localStorage.getItem('bif.nav.collapsed') === '1' } catch { return false }
}

export function Layout({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth()
  const { t } = useTerms()
  const live = useLive()
  const loc = useLocation()
  const st = live.status
  const [pending, setPending] = useState(0)
  const loadPending = () => { api.get<{ pending_total: number }>('/suspects/summary').then((r) => setPending(r.pending_total)).catch(() => undefined) }
  useEffect(loadPending, [])
  useLiveRefresh(loadPending, 500)
  const [chatOpen, setChatOpen] = useState(false)
  const [collapsed, setCollapsed] = useState(readCollapsed)
  const toggle = () => setCollapsed((v) => { try { localStorage.setItem('bif.nav.collapsed', v ? '0' : '1') } catch { /* 저장 못 해도 동작 */ } return !v })
  const label = (n: Item) => (n.term ? t(n.term) : n.lotLabel ? `${t('lot')} ${n.label}` : n.label)
  const current = GROUPS.flatMap((g) => g.items.map((i) => ({ g: g.title, i }))).find(({ i }) => (i.to === '/' ? loc.pathname === '/' : loc.pathname.startsWith(i.to)))
  const name = user?.display_name ?? user?.username ?? ''
  return (
    <div className={`app ${chatOpen ? 'with-chat' : ''} ${collapsed ? 'collapsed' : ''}`}>
      <nav className="nav" aria-label="주 메뉴">
        <div className="brand"><span className="logo"><Icon name="logo" /></span><span className="label">불량 원인 추적</span></div>
        <div className="menu">
          {GROUPS.map((g) => (
            <div key={g.title}>
              <div className="group">{g.title}</div>
              {g.items.map((n) => (
                <NavLink key={n.to} to={n.to} end={n.to === '/'} title={collapsed ? label(n) : undefined}>
                  <Icon name={n.icon} />
                  <span className="label">{label(n)}</span>
                  {n.to === '/review' && pending > 0 && <><span className="count">{pending > 999 ? '999+' : pending}</span><span className="dot" /></>}
                </NavLink>
              ))}
            </div>
          ))}
        </div>
        <div className="foot">
          <button onClick={toggle} title={collapsed ? '메뉴 펼치기' : '메뉴 접기'}><Icon name={collapsed ? 'expand' : 'collapse'} /><span className="label">메뉴 접기</span></button>
        </div>
      </nav>
      <header className="header">
        <span className="crumb">{current ? <>{current.g}<Icon name="chevron" size={14} /><b>{label(current.i)}</b></> : null}</span>
        <span className="spacer" />
        <GlobalSearch />
        <NavLink to="/monitor" className={`live-dot ${st?.running ? (st.phase === '이상' || st.phase === '정비' ? 'alarm' : 'on') : ''}`}
          title={st?.running ? `${st.equipment} 실시간 수집 중 (${st.phase})` : '실시간 수집 꺼짐 — 현장 모니터링에서 시작'}>
          <i />{st?.running ? `실시간 ${st.phase}` : live.connected ? '실시간 대기' : '연결 끊김'}
        </NavLink>
        <button className="ghost" onClick={() => setChatOpen((v) => !v)} aria-pressed={chatOpen}>
          <Icon name="chat" />{chatOpen ? '어시스턴트 닫기' : '어시스턴트'}
        </button>
        <span className="user">
          <span className="avatar" aria-hidden>{name.slice(0, 1).toUpperCase()}</span>
          <span><b style={{ color: 'var(--text)', fontWeight: 600 }}>{name}</b><br /><span style={{ fontSize: 11.5 }}>{user ? ROLE_LABEL[user.role] : ''}</span></span>
          <button className="ghost" onClick={logout} title="로그아웃" aria-label="로그아웃"><Icon name="logout" /></button>
        </span>
      </header>
      <main className="main">{children}</main>
      <ChatPanel open={chatOpen} onClose={() => setChatOpen(false)} />
      <Toaster />
    </div>
  )
}

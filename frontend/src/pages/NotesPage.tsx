import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { Lot } from '../api/types'
import { Help } from '../components/Help'
import { LotMemo } from '../components/LotMemo'
import { Empty, Loading, PageHead, Panel, StatusBadge } from '../components/ui'
import { fmtDateTime, fmtNum } from '../lib/format'
import { useAsync } from '../lib/hooks'
import { useTerms } from '../lib/terms'

interface Note { id: number; text: string; author: string | null; created_at: string }
type Group = Lot & { notes: Note[]; last_at: string }

/** 메모된 제품 모아보기: 메모가 달린 제품(LOT)만 최근 메모 순으로 모아 본다. */
export function NotesPage() {
  const { t } = useTerms()
  const [q, setQ] = useState('')
  const [open, setOpen] = useState<string | null>(null)
  const feed = useAsync(() => api.get<Group[]>('/lots/notes/feed', { q: q || undefined }), [q])
  const groups = feed.data ?? []
  return (
    <>
      <PageHead title="메모 모아보기" sub={`메모가 달린 ${t('lot')}만 최근 메모 순으로 모았습니다. 메모 내용이나 ${t('lotNo')}로 찾을 수 있습니다.`}>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="메모 내용 또는 번호 검색" style={{ width: 280 }} />
        <Help topic="memo" />
      </PageHead>
      <Loading error={feed.error} loading={feed.loading && !feed.data} />
      {feed.data && !groups.length && <Panel><Empty>{q ? '검색 결과가 없습니다.' : `아직 메모가 달린 ${t('lot')}이 없습니다. ${t('lot')} 목록·이력 추적·불량 판정 화면에서 메모를 남길 수 있습니다.`}</Empty></Panel>}
      <div className="grid cols-2" style={{ alignItems: 'start' }}>
        {groups.map((g) => (
          <Panel key={g.id} title={<><Link to={`/lots?lot=${encodeURIComponent(g.lot_no)}`}>{g.lot_no}</Link> <span className="muted" style={{ fontWeight: 400, fontSize: 13 }}>{g.item_type_name}</span></>}
            actions={<div className="row"><StatusBadge status={g.status} /><span className="faint" style={{ fontSize: 12 }}>메모 {g.notes.length} · {fmtDateTime(g.last_at)}</span></div>}>
            <div className="stack" style={{ gap: 6 }}>
              <div className="row" style={{ fontSize: 12.5 }}>
                <span className="muted">생성 {fmtDateTime(g.created_at)}{g.qty != null ? ` · 수량 ${fmtNum(g.qty, 0)}` : ''}</span>
                <span className="right row">
                  <Link to={`/genealogy?lot=${encodeURIComponent(g.lot_no)}`}>{t('genealogyGraph')}</Link>
                  <button className="small" onClick={() => setOpen(open === g.lot_no ? null : g.lot_no)}>{open === g.lot_no ? '닫기' : '메모 쓰기'}</button>
                </span>
              </div>
              {open === g.lot_no ? <LotMemo lotNo={g.lot_no} onChanged={feed.reload} compact /> : g.notes.map((n) => (
                <div key={n.id} style={{ borderLeft: '3px solid var(--border-strong)', padding: '2px 8px', fontSize: 13 }}>
                  <div style={{ whiteSpace: 'pre-wrap' }}>{n.text}</div>
                  <div className="faint" style={{ fontSize: 11.5 }}>{n.author ?? '-'} · {fmtDateTime(n.created_at)}</div>
                </div>
              ))}
            </div>
          </Panel>
        ))}
      </div>
    </>
  )
}

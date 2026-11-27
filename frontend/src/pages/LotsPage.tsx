import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import type { Lot, LotDetail } from '../api/types'
import { Help } from '../components/Help'
import { LotMemo } from '../components/LotMemo'
import { Empty, Field, Loading, PageHead, Panel, StatusBadge } from '../components/ui'
import { fmtDateTime, fmtNum, STATUS_LABEL } from '../lib/format'
import { useAsync } from '../lib/hooks'
import { useLiveRefresh } from '../lib/live'
import { useTerms } from '../lib/terms'

type Row = Lot & { note_count: number; last_note: string | null }

export function LotsPage() {
  const { t } = useTerms()
  const [params, setParams] = useSearchParams()
  const selected = params.get('lot')
  const [q, setQ] = useState('')
  const [status, setStatus] = useState('')
  const [onlyNotes, setOnlyNotes] = useState(params.get('notes') === '1')
  const rows = useAsync(() => api.get<Row[]>('/lots', { q, status, has_note: onlyNotes || undefined, limit: 300 }), [q, status, onlyNotes])
  useLiveRefresh(rows.reload)
  return (
    <>
      <PageHead title={`${t('lot')} 목록`} sub={`${t('lotNo')}·메모로 찾고, 메모가 있는 ${t('lot')}만 모아 볼 수 있습니다.`} />
      <div className="split-right" style={{ gridTemplateColumns: selected ? 'minmax(0,1fr) 440px' : 'minmax(0,1fr)' }}>
        <Panel flush>
          <div className="form-row" style={{ padding: 10, borderBottom: '1px solid var(--border)' }}>
            <Field label={`${t('lotNo')} 또는 메모 내용`}><input value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 260 }} placeholder="예: 0305, 재검" /></Field>
            <Field label="상태">
              <select value={status} onChange={(e) => setStatus(e.target.value)}>
                <option value="">전체</option>{Object.entries(STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </Field>
            <label className="check" style={{ height: 30 }}>
              <input type="checkbox" checked={onlyNotes} onChange={(e) => setOnlyNotes(e.target.checked)} />메모 있는 {t('lot')}만<Help topic="memo" />
            </label>
            <span className="muted right" style={{ alignSelf: 'center' }}>{rows.data?.length ?? 0}건 (최근 300건까지)</span>
          </div>
          <Loading error={rows.error} loading={rows.loading && !rows.data} />
          <div className="table-wrap" style={{ maxHeight: 'calc(100vh - 250px)' }}>
            <table className="t">
              <thead><tr><th>{t('lotNo')}</th><th>종류</th><th style={{ width: 130 }}>생성</th><th className="num" style={{ width: 80 }}>수량</th><th style={{ width: 80 }}>상태</th><th>메모</th></tr></thead>
              <tbody>
                {(rows.data ?? []).map((r) => (
                  <tr key={r.id} className={`clickable ${selected === r.lot_no ? 'selected' : ''}`} onClick={() => setParams({ lot: r.lot_no, ...(onlyNotes ? { notes: '1' } : {}) })}>
                    <td>{r.lot_no}</td><td>{r.item_type_name}</td><td>{fmtDateTime(r.created_at)}</td>
                    <td className="num">{fmtNum(r.qty, 0)}</td><td><StatusBadge status={r.status} /></td>
                    <td title={r.last_note ?? ''}>{r.note_count ? <><span className="badge neutral">{r.note_count}</span> {r.last_note}</> : <span className="faint">-</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {rows.data && !rows.data.length && <Empty>조건에 맞는 {t('lot')}이 없습니다.</Empty>}
          </div>
        </Panel>
        {selected && <LotPanel lotNo={selected} onChanged={rows.reload} onClose={() => setParams({})} />}
      </div>
    </>
  )
}

function LotPanel({ lotNo, onChanged, onClose }: { lotNo: string; onChanged: () => void; onClose: () => void }) {
  const { t } = useTerms()
  const lot = useAsync(() => api.get<LotDetail>(`/lots/${encodeURIComponent(lotNo)}`), [lotNo])
  const d = lot.data
  return (
    <Panel title={lotNo} actions={<button className="small" onClick={onClose}>닫기</button>}>
      <Loading error={lot.error} loading={lot.loading && !d} />
      {d && (
        <div className="stack" style={{ gap: 10 }}>
          <div className="row wrap" style={{ fontSize: 13 }}>
            <span className="muted">{d.item_type_name}</span><StatusBadge status={d.status} />
            <span className="muted">생성 {fmtDateTime(d.created_at)}</span>
            {d.qty != null && <span className="muted">수량 {fmtNum(d.qty, 0)}</span>}
          </div>
          <div className="row wrap" style={{ fontSize: 13 }}>
            <span>상위 {d.parent_count} · 하위 {d.child_count} · {t('defectJudgment')} {d.judgment_count}건</span>
          </div>
          <div className="row">
            <Link to={`/genealogy?lot=${encodeURIComponent(lotNo)}`}><button>{t('genealogyGraph')}</button></Link>
            <Link to="/defects"><button>{t('defectJudgment')}</button></Link>
          </div>
          <LotMemo lotNo={lotNo} onChanged={() => { lot.reload(); onChanged() }} />
        </div>
      )}
    </Panel>
  )
}

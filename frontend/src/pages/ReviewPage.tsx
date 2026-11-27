import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, errorText } from '../api/client'
import type { DefectType } from '../api/types'
import { useAuth } from '../auth'
import { toast } from '../components/Toast'
import { Help } from '../components/Help'
import { Empty, Field, Level, Loading, PageHead, Panel } from '../components/ui'
import { fmtDateTime, fmtNum } from '../lib/format'
import { useAsync } from '../lib/hooks'
import { useLiveRefresh } from '../lib/live'
import { useTerms } from '../lib/terms'

export interface Suspect {
  id: number; lot_id: number; lot_no: string; lot_qty: number | null; process_step_id: number | null; stage: string | null
  equipment: string | null; source: string; source_label: string; suspected_qty: number | null; defect_type_id: number | null
  defect_type: string | null; detail: string | null; detected_at: string; status: 'PENDING' | 'CONFIRMED' | 'REJECTED'
  reviewed_by: string | null; reviewed_at: string | null; review_note: string | null; confirmed_qty: number | null
}
interface StageSum { process_step_id: number | null; stage: string; seq: number; PENDING: number; CONFIRMED: number; REJECTED: number; pending_qty: number; by_source: Record<string, number> }
interface Summary { stages: StageSum[]; pending_total: number; reject_reasons: string[]; sources: Record<string, string> }

const STATUS_TXT = { PENDING: ['검토 대기', 'warn'], CONFIRMED: ['불량 확정', 'danger'], REJECTED: ['불량 아님', 'ok'] } as const

export function ReviewPage() {
  const { t } = useTerms()
  const { can } = useAuth()
  const [stage, setStage] = useState<number | null | 'all'>('all')
  const [status, setStatus] = useState<'PENDING' | 'CONFIRMED' | 'REJECTED' | 'ALL'>('PENDING')
  const [source, setSource] = useState('')
  const summary = useAsync(() => api.get<Summary>('/suspects/summary'), [])
  const list = useAsync(() => api.get<Suspect[]>('/suspects', {
    status, source, process_step_id: stage === 'all' || stage === null ? undefined : stage,
  }), [status, source, stage])
  const [sel, setSel] = useState<number | null>(null)
  const [checked, setChecked] = useState<Set<number>>(new Set())
  const [msg, setMsg] = useState<string | null>(null)
  useLiveRefresh(() => { summary.reload(); list.reload() }, 600)
  const rows = list.data ?? []
  const current = rows.find((r) => r.id === sel) ?? null
  useEffect(() => { if (sel && !rows.some((r) => r.id === sel)) setSel(null) }, [rows, sel])

  const reload = () => { summary.reload(); list.reload(); setChecked(new Set()) }
  const bulk = async (action: 'confirm' | 'reject') => {
    let reason: string | null = null
    if (action === 'reject') { reason = prompt('불량 아님 사유 (예: 재검 결과 양품, 검사 과검출)', '검사 과검출'); if (!reason) return }
    else if (!confirm(`선택한 ${checked.size}건을 의심 수량 그대로 불량 확정합니다. 수량이 없는 의심(조건 이탈·관리도 위반)은 건너뜁니다.`)) return
    try {
      const r = await api.post<{ done: number; skipped: number[] }>('/suspects/bulk', { ids: [...checked], action, reason })
      setMsg(`${r.done}건 처리${r.skipped.length ? `, ${r.skipped.length}건 건너뜀 (수량·유형 입력 필요 또는 이미 검토됨)` : ''}`)
      reload()
    } catch (e) { setMsg(errorText(e)) }
  }

  return (
    <>
      <PageHead title="불량 판정" sub={`실시간 수집·검사에서 자동으로 걸린 '불량 의심'을 공정 단계별로 모았습니다. 실무자가 판정해 확정한 것만 불량 데이터로 적재됩니다.`}>
        <Link to="/defect-data"><button>적재된 불량 데이터 보기</button></Link>
      </PageHead>
      <div className="tabs" role="tablist">
        {([['PENDING', '판정 대기', summary.data?.pending_total], ['CONFIRMED', '불량 확정 (적재됨)', undefined], ['REJECTED', '불량 아님', undefined], ['ALL', '전체', undefined]] as const).map(([k, label, n]) => (
          <button key={k} role="tab" aria-selected={status === k} className={status === k ? 'on' : ''} onClick={() => setStatus(k)}>{label}{n != null && <span className="n">{n}</span>}</button>
        ))}
      </div>
      <div className="grid" style={{ gridTemplateColumns: `repeat(${Math.max(1, (summary.data?.stages.length ?? 0) + 1)}, minmax(0, 1fr))`, marginBottom: 12 }}>
        <StageCard title="전체" active={stage === 'all'} onClick={() => setStage('all')}
          pending={summary.data?.pending_total ?? 0} confirmed={(summary.data?.stages ?? []).reduce((a, s) => a + s.CONFIRMED, 0)}
          rejected={(summary.data?.stages ?? []).reduce((a, s) => a + s.REJECTED, 0)} />
        {(summary.data?.stages ?? []).map((s) => (
          <StageCard key={String(s.process_step_id)} title={s.stage} active={stage === s.process_step_id} onClick={() => setStage(s.process_step_id)}
            pending={s.PENDING} confirmed={s.CONFIRMED} rejected={s.REJECTED} sources={s.by_source} />
        ))}
      </div>
      <div className="split-right" style={{ gridTemplateColumns: 'minmax(0,1fr) 420px' }}>
        <Panel flush title={<>{status === 'PENDING' ? '판정할 의심' : status === 'CONFIRMED' ? '불량 확정된 의심' : status === 'REJECTED' ? '불량 아님 처리된 의심' : '전체 의심'}<Help title="불량 의심">조건 이탈: 그 단계 조건이 허용(학습) 범위를 벗어남 — 그 시간 생산분 재검 필요. 관리도 위반: 넬슨룰 신호. 검사 불합격: 외관·비전 검사가 걸러 낸 수량(과검출 포함). 실무자가 재검·확인 후 확정하거나 아니라고 판단합니다.</Help></>}
          actions={can('OPERATOR') && checked.size > 0 ? (
            <div className="row"><span className="muted">{checked.size}건 선택</span>
              <button className="small danger" onClick={() => bulk('confirm')}>일괄 불량 확정</button>
              <button className="small" onClick={() => bulk('reject')}>일괄 불량 아님</button></div>) : undefined}>
          <div className="form-row" style={{ padding: 10, borderBottom: '1px solid var(--border)' }}>
            <select value={source} onChange={(e) => setSource(e.target.value)}>
              <option value="">출처 전체</option>
              {Object.entries(summary.data?.sources ?? {}).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </div>
          {msg && <div className="notice" style={{ margin: 8 }}>{msg}</div>}
          <Loading error={list.error} loading={list.loading && !list.data} />
          <div className="table-wrap" style={{ maxHeight: 'calc(100vh - 330px)' }}>
            <table className="t">
              <thead><tr><th style={{ width: 30 }} /><th style={{ width: 110 }}>발견</th><th>{t('lot')}</th><th style={{ width: 90 }}>단계</th><th style={{ width: 90 }}>출처</th><th className="num" style={{ width: 70 }}>의심 수</th><th>내용</th><th style={{ width: 90 }}>상태</th></tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} className={`clickable ${sel === r.id ? 'selected' : ''}`} onClick={() => setSel(r.id)}>
                    <td onClick={(e) => e.stopPropagation()}>{r.status === 'PENDING' && can('OPERATOR') && (
                      <input type="checkbox" checked={checked.has(r.id)} onChange={(e) => { const s = new Set(checked); if (e.target.checked) s.add(r.id); else s.delete(r.id); setChecked(s) }} />)}</td>
                    <td>{fmtDateTime(r.detected_at).slice(5)}</td>
                    <td>{r.lot_no}</td><td>{r.stage}</td><td>{r.source_label}</td>
                    <td className="num">{r.suspected_qty != null ? fmtNum(r.suspected_qty, 0) : <span className="faint">전체</span>}</td>
                    <td title={r.detail ?? ''}>{r.detail}</td>
                    <td><Level level={STATUS_TXT[r.status][1]}>{STATUS_TXT[r.status][0]}</Level></td>
                  </tr>
                ))}
              </tbody>
            </table>
            {list.data && !rows.length && <Empty>{status === 'PENDING' ? '검토 대기 중인 의심이 없습니다. 현장 모니터링에서 실시간 수집을 시작하면 쌓입니다.' : '해당 건이 없습니다.'}</Empty>}
          </div>
        </Panel>
        {current ? <ReviewPanel s={current} reasons={summary.data?.reject_reasons ?? []} onDone={(text) => { toast(text); reload() }} /> : (
          <Panel title="판단"><div className="muted" style={{ fontSize: 13, lineHeight: 1.7 }}>
            왼쪽에서 의심 건을 고르세요.<br />
            1. 해당 {t('lot')}을 재검·확인합니다.<br />
            2. 불량이 맞으면 <b>불량 확정</b> (수량·유형 확인) → 불량 데이터로 적재, 원인 분석과 생산 대시보드에 반영<br />
            3. 아니면 <b>불량 아님</b> + 사유 → 과검출·센서 오류 이력으로 남음
          </div></Panel>
        )}
      </div>
    </>
  )
}

function StageCard(p: { title: string; active: boolean; onClick: () => void; pending: number; confirmed: number; rejected: number; sources?: Record<string, number> }) {
  return (
    <button className="panel metric" onClick={p.onClick} style={{ textAlign: 'left', height: 92, alignItems: 'stretch', cursor: 'pointer', borderColor: p.active ? 'var(--primary)' : undefined, background: p.active ? 'var(--primary-soft)' : undefined }}>
      <span className="label">{p.title}</span>
      <span className="value" style={{ color: p.pending ? 'var(--warn)' : undefined }}>{p.pending}<span style={{ fontSize: 13, fontWeight: 400, color: 'var(--muted)' }}> 대기</span></span>
      <span className="hint">확정 {p.confirmed} · 아님 {p.rejected}{p.sources && Object.keys(p.sources).length ? ` · ${Object.entries(p.sources).map(([k, v]) => `${k} ${v}`).join(', ')}` : ''}</span>
    </button>
  )
}

function ReviewPanel({ s, reasons, onDone }: { s: Suspect; reasons: string[]; onDone: (msg: string) => void }) {
  const { can } = useAuth()
  const { t } = useTerms()
  const types = useAsync(() => api.get<DefectType[]>('/defect-types', { active_only: true }), [])
  const [qty, setQty] = useState<string>(s.suspected_qty != null ? String(s.suspected_qty) : '')
  const [typeId, setTypeId] = useState<number | ''>(s.defect_type_id ?? '')
  const [memo, setMemo] = useState('')
  const [reason, setReason] = useState(reasons[0] ?? '')
  const [msg, setMsg] = useState<string | null>(null)
  useEffect(() => { setQty(s.suspected_qty != null ? String(s.suspected_qty) : ''); setTypeId(s.defect_type_id ?? ''); setMemo(''); setMsg(null) }, [s.id, s.suspected_qty, s.defect_type_id])
  const runs = useAsync(() => api.get<{ process_step_name: string; equipment_name: string | null; conditions: { name: string; value: number; unit: string | null; out_of_range: boolean }[] }[]>(`/lots/${encodeURIComponent(s.lot_no)}/runs`), [s.lot_no])
  const pending = s.status === 'PENDING'
  const confirm = async () => {
    try {
      await api.post(`/suspects/${s.id}/confirm`, { qty: qty ? Number(qty) : null, defect_type_id: typeId || null, memo: memo || null })
      onDone(`${s.lot_no} [${s.stage}] 불량 ${qty}개 확정 → 불량 데이터로 적재했습니다.`)
    } catch (e) { setMsg(errorText(e)) }
  }
  const reject = async () => {
    try { await api.post(`/suspects/${s.id}/reject`, { reason: memo ? `${reason} / ${memo}` : reason }); onDone(`${s.lot_no} [${s.stage}] 불량 아님 (${reason}) 처리했습니다.`) } catch (e) { setMsg(errorText(e)) }
  }
  const stageRuns = useMemo(() => runs.data ?? [], [runs.data])
  return (
    <Panel title={`${s.stage ?? ''} · ${s.source_label}`}>
      <div className="stack" style={{ gap: 10 }}>
        <div className="row wrap" style={{ fontSize: 13 }}>
          <b>{s.lot_no}</b><span className="muted">생산 {fmtNum(s.lot_qty, 0)}</span><span className="muted">발견 {fmtDateTime(s.detected_at)}</span>
          <Link to={`/genealogy?lot=${encodeURIComponent(s.lot_no)}`}>{t('genealogyGraph')}</Link>
        </div>
        <div className="notice">{s.detail}</div>
        <div>
          <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>이 {t('lot')}이 거친 공정</div>
          {stageRuns.map((r, i) => (
            <div key={i} style={{ fontSize: 12.5, padding: '2px 0', borderBottom: '1px solid var(--border)' }}>
              <b>{r.process_step_name}</b> <span className="muted">{r.equipment_name}</span>{' '}
              {r.conditions.map((c) => <span key={c.name} style={{ marginRight: 6, color: c.out_of_range ? 'var(--danger)' : 'var(--muted)' }}>{c.name} {c.value}{c.unit ?? ''}{c.out_of_range ? ' ▲' : ''}</span>)}
            </div>
          ))}
        </div>
        {pending && can('OPERATOR') ? (
          <>
            <div className="form-grid">
              <Field label={s.suspected_qty == null ? '재검 결과 불량 수량 *' : '확정 불량 수량'}>
                <input type="number" min={1} value={qty} onChange={(e) => setQty(e.target.value)} placeholder={s.suspected_qty == null ? '재검 후 입력' : ''} />
              </Field>
              <Field label="불량 유형">
                <select value={typeId} onChange={(e) => setTypeId(e.target.value ? Number(e.target.value) : '')}>
                  <option value="">선택</option>{(types.data ?? []).map((x) => <option key={x.id} value={x.id}>{x.code} · {x.name}</option>)}
                </select>
              </Field>
            </div>
            <Field label="메모"><input value={memo} onChange={(e) => setMemo(e.target.value)} placeholder="재검 방법, 확인 내용" /></Field>
            <div className="row">
              <button className="primary" style={{ background: 'var(--danger)', borderColor: 'var(--danger)' }} onClick={confirm} disabled={!qty || !typeId}>불량 확정 → 적재</button>
              <select value={reason} onChange={(e) => setReason(e.target.value)} style={{ flex: 1 }}>{reasons.map((r) => <option key={r}>{r}</option>)}</select>
              <button onClick={reject}>불량 아님</button>
            </div>
          </>
        ) : (
          <div className="muted" style={{ fontSize: 13 }}>
            {s.status === 'PENDING' ? '판단은 실무자 이상이 할 수 있습니다.' :
              `${s.status === 'CONFIRMED' ? `불량 확정 ${fmtNum(s.confirmed_qty, 0)}개` : `불량 아님 — ${s.review_note ?? ''}`} · ${s.reviewed_by ?? '-'} · ${fmtDateTime(s.reviewed_at)}`}
          </div>
        )}
        {msg && <div className="notice">{msg}</div>}
      </div>
    </Panel>
  )
}

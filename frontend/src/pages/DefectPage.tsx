import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import type { CauseAnalysis, Judgment } from '../api/causes'
import { api, errorText } from '../api/client'
import type { DefectType, LotDetail } from '../api/types'
import { useAuth } from '../auth'
import { ScoreDetail } from '../components/ScoreDetail'
import { Empty, Field, Loading, PageHead, Panel, ScoreBar, StatusBadge } from '../components/ui'
import { fmtDateTime, NODE_LABEL } from '../lib/format'
import { useAsync } from '../lib/hooks'
import { useTerms } from '../lib/terms'
import { LotMemo } from '../components/LotMemo'

export function DefectPage() {
  const { can } = useAuth()
  const { t } = useTerms()
  const types = useAsync(() => api.get<DefectType[]>('/defect-types', { active_only: true }), [])
  const recent = useAsync(() => api.get<Judgment[]>('/defects', { limit: 50 }), [])
  const lotRef = useRef<HTMLInputElement>(null)
  const [lotNo, setLotNo] = useState('')
  const [lot, setLot] = useState<LotDetail | null>(null)
  const [typeId, setTypeId] = useState<number | ''>('')
  const [memo, setMemo] = useState('')
  const [msg, setMsg] = useState<{ kind: 'error' | 'ok'; text: string } | null>(null)
  const [result, setResult] = useState<{ judgment: Judgment; analysis: CauseAnalysis } | null>(null)
  const [busy, setBusy] = useState(false)
  const [detail, setDetail] = useState<number>(0)

  useEffect(() => lotRef.current?.focus(), [])

  // 바코드 스캐너는 키보드처럼 입력 후 Enter 를 보낸다
  const lookup = async (e?: React.FormEvent) => {
    e?.preventDefault()
    const no = lotNo.trim()
    if (!no) return
    setMsg(null)
    try {
      setLot(await api.get<LotDetail>(`/lots/${encodeURIComponent(no)}`))
    } catch (err) {
      setLot(null)
      setMsg({ kind: 'error', text: errorText(err) })
      lotRef.current?.select()
    }
  }

  const save = async () => {
    if (!lot || !typeId) return
    setBusy(true)
    try {
      const r = await api.post<{ judgment: Judgment; analysis: CauseAnalysis }>('/defects', { lot_no: lot.lot_no, defect_type_id: typeId, memo: memo || null })
      setResult(r)
      setDetail(0)
      setMsg({ kind: 'ok', text: `${lot.lot_no} ${t('defectJudgment')}을 저장했습니다.` })
      setLot(null)
      setLotNo('')
      setMemo('')
      recent.reload()
      lotRef.current?.focus()
    } catch (err) {
      setMsg({ kind: 'error', text: errorText(err) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <PageHead title={t('defectJudgment')} sub={`${t('scanHint')} → 불량 유형 선택 → 저장. 저장 즉시 원인 후보를 계산합니다.`} />
      <div className="grid cols-2">
        <div className="stack">
          <Panel title={`${t('defectJudgment')} 입력`}>
            {!can('OPERATOR') && <div className="notice warn" style={{ marginBottom: 8 }}>조회자는 {t('judge')}할 수 없습니다.</div>}
            <form onSubmit={lookup} className="row" style={{ alignItems: 'end' }}>
              <Field label={t('lotNo')} style={{ flex: 1 }}>
                <input ref={lotRef} className="big" value={lotNo} onChange={(e) => setLotNo(e.target.value)} placeholder="스캔하거나 입력 후 Enter" />
              </Field>
              <button type="submit" style={{ height: 42 }}>조회</button>
            </form>
            {lot && (
              <div className="stack" style={{ marginTop: 12, gap: 10 }}>
                <div className="row wrap">
                  <b>{lot.lot_no}</b><span className="muted">{lot.item_type_name}</span><StatusBadge status={lot.status} />
                  <span className="muted">생성 {fmtDateTime(lot.created_at)}</span>
                  {lot.judgment_count > 0 && <span className="badge warn">■ 기존 {t('judge')} {lot.judgment_count}건</span>}
                </div>
                <div className="form-grid">
                  <Field label="불량 유형">
                    <select value={typeId} onChange={(e) => setTypeId(e.target.value ? Number(e.target.value) : '')}>
                      <option value="">선택</option>
                      {(types.data ?? []).map((t) => <option key={t.id} value={t.id}>{t.code} · {t.name}</option>)}
                    </select>
                  </Field>
                  <Field label="메모"><input value={memo} onChange={(e) => setMemo(e.target.value)} /></Field>
                </div>
                <LotMemo lotNo={lot.lot_no} compact />
                <div className="row">
                  <button className="primary" disabled={!typeId || busy || !can('OPERATOR')} onClick={save}>{busy ? '저장 중…' : `${t('defectJudgment')} 저장`}</button>
                  <button onClick={() => { setLot(null); setLotNo(''); lotRef.current?.focus() }}>취소</button>
                </div>
              </div>
            )}
            {msg && <div className={`notice ${msg.kind === 'error' ? 'error' : ''}`} style={{ marginTop: 10, whiteSpace: 'pre-wrap' }}>{msg.text}</div>}
          </Panel>

          {result && (
            <Panel title={`원인 후보 · ${result.judgment.lot_no}`} actions={<Link to={`/genealogy?lot=${encodeURIComponent(result.judgment.lot_no)}`}>{t('genealogyGraph')}에서 보기</Link>} flush>
              <table className="t">
                <thead><tr><th style={{ width: 40 }}>순위</th><th style={{ width: 50 }}>종류</th><th>노드</th><th style={{ width: 120 }}>점수</th></tr></thead>
                <tbody>
                  {result.analysis.candidates.slice(0, 5).map((c, i) => (
                    <tr key={c.node} className={`clickable ${detail === i ? 'selected' : ''}`} onClick={() => setDetail(i)}>
                      <td>{c.rank}</td><td>{NODE_LABEL[c.type]}</td><td>{c.label}</td>
                      <td><div className="row"><ScoreBar value={c.score} max={result.analysis.candidates[0].score || 1} /><span style={{ fontSize: 12 }}>{c.score.toFixed(3)}</span></div></td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {result.analysis.candidates[detail] && (
                <div style={{ padding: 12 }}><ScoreDetail c={result.analysis.candidates[detail]} labels={result.analysis.factor_labels} /></div>
              )}
            </Panel>
          )}
        </div>

        <Panel title={`최근 ${t('judge')}`} flush>
          <Loading error={recent.error} loading={recent.loading && !recent.data} />
          <div className="table-wrap" style={{ maxHeight: 'calc(100vh - 180px)' }}>
            <table className="t">
              <thead><tr><th style={{ width: 130 }}>{t('judge')} 시각</th><th>{t('lot')}</th><th>불량 유형</th><th style={{ width: 80 }}>{t('judge')}자</th><th>확정 원인</th></tr></thead>
              <tbody>
                {(recent.data ?? []).map((j) => (
                  <tr key={j.id}>
                    <td>{fmtDateTime(j.judged_at)}</td>
                    <td><Link to={`/genealogy?lot=${encodeURIComponent(j.lot_no)}`}>{j.lot_no}</Link></td>
                    <td>{j.defect_type}</td>
                    <td>{j.judged_by ?? '-'}</td>
                    <td title={j.root_causes.map((r) => r.label).join(', ')}>
                      {j.root_causes.length ? j.root_causes.map((r) => `${NODE_LABEL[r.type]} ${r.label}`).join(', ') : <span className="faint">미확정</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {recent.data && recent.data.length === 0 && <Empty>{t('judge')} 내역이 없습니다.</Empty>}
          </div>
        </Panel>
      </div>
    </>
  )
}

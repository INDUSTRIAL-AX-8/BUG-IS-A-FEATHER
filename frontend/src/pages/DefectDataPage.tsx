import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import type { Judgment } from '../api/causes'
import { api } from '../api/client'
import type { DefectType } from '../api/types'
import { Help } from '../components/Help'
import { Empty, Field, Loading, Metric, PageHead, Panel } from '../components/ui'
import { fmtDateTime, fmtNum } from '../lib/format'
import { useAsync } from '../lib/hooks'
import { useLiveRefresh } from '../lib/live'
import { useTerms } from '../lib/terms'

type J = Judgment & { defect_qty?: number | null }

/** 불량 데이터: 실무자가 판정해 적재된 불량(불량 판정 기록) 전체. 원인 분석·생산 대시보드가 이 데이터를 쓴다. */
export function DefectDataPage() {
  const { t } = useTerms()
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [typeId, setTypeId] = useState<number | ''>('')
  const [q, setQ] = useState('')
  const types = useAsync(() => api.get<DefectType[]>('/defect-types'), [])
  const rows = useAsync(() => api.get<J[]>('/defects', { from: from || undefined, to: to || undefined, defect_type_id: typeId || undefined, lot_no: q || undefined, limit: 2000 }), [from, to, typeId, q])
  useLiveRefresh(rows.reload, 800)
  const data = rows.data ?? []
  const stats = useMemo(() => {
    const qty = data.reduce((a, j) => a + (j.defect_qty ?? 1), 0)
    const viaReview = data.filter((j) => j.memo?.includes('의심 확정')).length
    return { count: data.length, qty, viaReview, direct: data.length - viaReview }
  }, [data])
  const path = (j: J) => {
    const m = j.memo?.match(/^\[([^\]]+)\] 의심 확정/)
    return m ? m[1] : '이력·직접 등록'
  }
  const csvHref = `/api/defects/export.csv${from || to ? `?${new URLSearchParams({ ...(from ? { from } : {}), ...(to ? { to } : {}) })}` : ''}`
  return (
    <>
      <PageHead title="불량 데이터" sub="불량 판정으로 확정되어 적재된 불량 기록입니다. 요인 분석·원인 추적·생산 대시보드가 이 데이터를 씁니다.">
        <a href={csvHref}><button>CSV 내려받기</button></a>
        <Link to="/review"><button className="primary">불량 판정하러 가기</button></Link>
      </PageHead>
      <div className="grid cols-4" style={{ marginBottom: 12 }}>
        <Metric label="적재된 불량 판정" value={fmtNum(stats.count, 0)} hint="건" />
        <Metric label="불량 수량 합계" value={fmtNum(stats.qty, 0)} hint="개" />
        <Metric label={<>판정 화면에서 확정<Help title="판정 경로">불량 판정 화면에서 자동 의심을 실무자가 확정한 것입니다. 단계·출처(조건 이탈/관리도 위반/검사 불합격)가 함께 기록됩니다.</Help></> as unknown as string} value={fmtNum(stats.viaReview, 0)} hint="건" />
        <Metric label="이력·직접 등록" value={fmtNum(stats.direct, 0)} hint="과거 기록 가져오기 또는 불량 직접 등록" />
      </div>
      <Panel flush>
        <div className="form-row" style={{ padding: 10, borderBottom: '1px solid var(--border)' }}>
          <Field label="시작"><input type="datetime-local" value={from} onChange={(e) => setFrom(e.target.value)} /></Field>
          <Field label="끝"><input type="datetime-local" value={to} onChange={(e) => setTo(e.target.value)} /></Field>
          <Field label="불량 유형">
            <select value={typeId} onChange={(e) => setTypeId(e.target.value ? Number(e.target.value) : '')}>
              <option value="">전체</option>{(types.data ?? []).map((x) => <option key={x.id} value={x.id}>{x.code} · {x.name}</option>)}
            </select>
          </Field>
          <Field label={t('lotNo')}><input value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 200 }} /></Field>
        </div>
        <Loading error={rows.error} loading={rows.loading && !rows.data} />
        <div className="table-wrap" style={{ maxHeight: 'calc(100vh - 330px)' }}>
          <table className="t">
            <thead><tr><th style={{ width: 130 }}>판정 시각</th><th>{t('lot')}</th><th className="num" style={{ width: 90 }}>불량 수량</th><th>불량 유형</th><th style={{ width: 160 }}>판정 경로 (단계·출처)</th><th style={{ width: 90 }}>판정자</th><th>메모</th></tr></thead>
            <tbody>
              {data.map((j) => (
                <tr key={j.id}>
                  <td>{fmtDateTime(j.judged_at)}</td>
                  <td><Link to={`/lots?lot=${encodeURIComponent(j.lot_no)}`}>{j.lot_no}</Link></td>
                  <td className="num">{fmtNum(j.defect_qty ?? 1, 0)}</td>
                  <td>{j.defect_type}</td>
                  <td>{path(j)}</td>
                  <td>{j.judged_by ?? '-'}</td>
                  <td title={j.memo ?? ''}>{j.memo}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {rows.data && !data.length && <Empty>적재된 불량이 없습니다.</Empty>}
        </div>
      </Panel>
    </>
  )
}

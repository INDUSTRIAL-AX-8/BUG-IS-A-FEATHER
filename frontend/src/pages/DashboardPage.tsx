import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { ViolationRow } from '../api/types'
import { Loading, Metric, PageHead, Panel, RuleBadge } from '../components/ui'
import { fmtDateTime } from '../lib/format'
import { useAsync } from '../lib/hooks'

export function DashboardPage() {
  const v = useAsync(() => api.get<ViolationRow[]>('/spc/violations', { acknowledged: false, limit: 50 }), [])
  return (
    <>
      <PageHead title="대시보드" />
      <div className="grid cols-4" style={{ marginBottom: 12 }}>
        <Metric label="미확인 SPC 위반" value={v.data?.length ?? '-'} hint="최근 50건 기준" />
      </div>
      <ViolationsPanel rows={v.data} error={v.error} loading={v.loading} />
    </>
  )
}

export function ViolationsPanel(props: { rows: ViolationRow[] | null; error: string | null; loading: boolean; height?: number }) {
  return (
    <Panel title="미확인 SPC 위반" actions={<Link to="/spc">관리도 보기</Link>} flush>
      <Loading error={props.error} loading={props.loading && !props.rows} />
      <div className="table-wrap" style={{ maxHeight: props.height ?? 360 }}>
        <table className="t">
          <thead>
            <tr><th style={{ width: 130 }}>발생 시각</th><th>부품/공정 · 특성</th><th style={{ width: 52 }}>룰</th><th>관련 LOT</th></tr>
          </thead>
          <tbody>
            {(props.rows ?? []).map((r) => (
              <tr key={r.id}>
                <td>{fmtDateTime(r.occurred_at)}</td>
                <td><Link to={`/spc?char=${r.characteristic_id}`}>{r.owner} · {r.characteristic}</Link></td>
                <td title={r.description}><RuleBadge rule={r.rule_no} /></td>
                <td title={r.lot_nos.join(', ')}>
                  {r.lot_nos.slice(0, 3).map((no) => no && (
                    <Link key={no} to={`/genealogy?lot=${encodeURIComponent(no)}`} style={{ marginRight: 6 }}>{no}</Link>
                  ))}
                  {r.lot_nos.length > 3 && <span className="faint">외 {r.lot_nos.length - 3}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {props.rows && props.rows.length === 0 && <div className="empty">미확인 위반이 없습니다.</div>}
      </div>
    </Panel>
  )
}

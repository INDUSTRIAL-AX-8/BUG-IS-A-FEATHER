import { useMemo } from 'react'
import { fmtDateTime } from '../lib/format'
import { useLive } from '../lib/live'
import { Help } from './Help'
import { Panel } from './ui'

// 실시간 DB 적재 로그 — 수집기가 DB 에 쓰는 행을 들어오는 순서대로 보여 준다 (RTDB 콘솔처럼).
const TABLE_LABEL: Record<string, string> = {
  lot: '생산 실적(LOT)', process_run: '공정 처리', condition_value: '조건값', defect_suspect: '불량 의심', measurement: '측정값',
}
const TABLE_COLOR: Record<string, string> = {
  lot: '#24508f', process_run: '#5a6370', condition_value: '#2c6e49', defect_suspect: '#9a6200', measurement: '#2c6e49',
}

export function RtdbPanel({ height = 330 }: { height?: number }) {
  const live = useLive()
  const rows = useMemo(() => live.records.flatMap((r) => (r.inserts ?? []).map((x, i) => ({ ...x, at: r.at, seq: `${r.lot_id}-${i}` }))).reverse(), [live.records])
  const counts = useMemo(() => {
    const c: Record<string, number> = {}
    for (const r of live.records) for (const x of r.inserts ?? []) c[x.table] = (c[x.table] ?? 0) + 1
    return c
  }, [live.records])
  const running = live.status?.running
  const sensors = [...live.sensors].reverse().slice(0, 6)
  return (
    <Panel flush title={<>실시간 DB 적재<Help title="실시간 DB (RTDB)">수집기가 DB 에 쓰는 행을 들어오는 순서대로 보여 줍니다. 한 기록마다 생산 실적 1행, 공정 단계별 처리·조건값, 불량 의심이 쌓이고 3D 모형이 같은 데이터로 움직입니다. (시연 데이터)</Help></>}
      actions={<span style={{ fontSize: 12, color: running ? 'var(--ok)' : 'var(--faint)' }}>{running ? `● 수신 중 · ${live.status?.ticks ?? 0}건` : '○ 대기'}</span>}>
      <div className="row wrap" style={{ gap: 6, padding: '6px 10px', borderBottom: '1px solid var(--border)' }}>
        {Object.keys(TABLE_LABEL).filter((t) => counts[t]).map((t) => (
          <span key={t} className="badge neutral" style={{ borderLeft: `3px solid ${TABLE_COLOR[t]}` }}>{TABLE_LABEL[t]} {counts[t]}</span>
        ))}
        {!Object.keys(counts).length && <span className="faint" style={{ fontSize: 12 }}>대시보드에서 실시간 수집을 시작하면 행이 쌓입니다.</span>}
      </div>
      {running && (
        <div style={{ borderBottom: '1px solid var(--border)', background: 'var(--surface-2)' }}>
          <div className="faint" style={{ fontSize: 11.5, padding: '4px 10px' }}>센서 스트림 ({live.status?.clock === 'real' ? '몇 초마다' : '-'}, DB 에는 구간이 끝날 때 1건으로 집계 적재)</div>
          <table className="t" style={{ fontSize: 12 }}><tbody>
            {sensors.map((x, i) => (
              <tr key={x.at + i} className={i === 0 ? 'flash' : ''} style={{ height: 24 }}>
                <td style={{ width: 70 }} className="mono">{fmtDateTime(x.at).slice(11)}</td>
                <td style={{ width: 92 }}><span style={{ color: '#6b4fa0', fontWeight: 600 }}>sensor</span></td>
                <td><span className="muted">{x.equipment} 생산 {x.produced}/{x.planned} · {Object.entries(x.values).map(([k, v]) => `${k} ${v}`).join(', ')}</span></td>
              </tr>
            ))}
          </tbody></table>
        </div>
      )}
      <div className="table-wrap" style={{ maxHeight: height }}>
        <table className="t" style={{ fontSize: 12 }}>
          <tbody>
            {rows.slice(0, 120).map((r, i) => (
              <tr key={r.seq} className={i < 12 && running ? 'flash' : ''} style={{ height: 26 }}>
                <td style={{ width: 70 }} className="mono">{fmtDateTime(r.at).slice(11)}</td>
                <td style={{ width: 92 }}><span style={{ color: TABLE_COLOR[r.table] ?? 'inherit', fontWeight: 600 }}>{r.table}</span></td>
                <td title={`${r.key} · ${r.summary}`}><span className="mono">{r.key}</span> <span className="muted">{r.summary}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}

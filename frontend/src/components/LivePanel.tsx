import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, errorText } from '../api/client'
import { useAuth } from '../auth'
import { fmtDateTime, fmtNum, fmtPct } from '../lib/format'
import { useAsync } from '../lib/hooks'
import { useLive } from '../lib/live'
import { useTerms } from '../lib/terms'
import { AXIS_STYLE, CHART_FONT, EChart, type EChartsOption } from './EChart'
import { Help } from './Help'
import { Level, Panel } from './ui'

interface Target { equipment_id: number; equipment: string; runs: number }

/** 실시간 수집(시연) 제어 + 흐름 차트 + 기록·경보 피드. */
export function LivePanel() {
  const { can } = useAuth()
  const { t } = useTerms()
  const live = useLive()
  const st = live.status
  const targets = useAsync(() => api.get<Target[]>('/analysis/targets'), [])
  const [eq, setEq] = useState<number | ''>('')
  const [scenario, setScenario] = useState('drift')
  const [interval, setIntervalSec] = useState(2)
  const [msg, setMsg] = useState<string | null>(null)
  const call = async (path: string, body?: unknown) => {
    setMsg(null)
    try { await api.post(path, body) } catch (e) { setMsg(errorText(e)) }
  }
  const recs = live.records.filter((r) => !st?.equipment_id || r.equipment_id === st.equipment_id)
  const drift = st?.drift_condition ?? recs[recs.length - 1]?.drift_condition ?? null

  const option = useMemo<EChartsOption | null>(() => {
    if (!recs.length || !drift) return null
    const last = recs[recs.length - 1]
    const lim = last.limits[drift]
    const x = recs.map((r) => fmtDateTime(r.at).slice(5))
    return {
      animation: false,
      grid: { left: 46, right: 50, top: 26, bottom: 24 },
      legend: { top: 0, textStyle: { fontFamily: CHART_FONT, fontSize: 11 } },
      tooltip: { trigger: 'axis', textStyle: { fontFamily: CHART_FONT, fontSize: 12 } },
      xAxis: { type: 'category', data: x, ...AXIS_STYLE, axisLabel: { ...AXIS_STYLE.axisLabel, hideOverlap: true } },
      yAxis: [
        { type: 'value', name: '불량률 %', nameTextStyle: { fontSize: 11 }, ...AXIS_STYLE },
        { type: 'value', name: drift, scale: true, nameTextStyle: { fontSize: 11 }, ...AXIS_STYLE, splitLine: { show: false } },
      ],
      series: [
        { name: '불량률', type: 'bar', data: recs.map((r) => ({ value: +(r.rate * 100).toFixed(2), itemStyle: { color: r.out_of_range.length ? '#e3a49e' : '#a9c1e2' } })) },
        { name: drift, type: 'line', yAxisIndex: 1, data: recs.map((r) => r.values[drift]), symbolSize: 5, lineStyle: { color: '#1d4580', width: 2 }, itemStyle: { color: '#1d4580' },
          markLine: lim && (lim[0] != null || lim[1] != null) ? { symbol: 'none', silent: true, label: { formatter: lim[2] ? '학습 경계' : '허용 경계', fontSize: 11 }, lineStyle: { color: '#a8261d', type: 'dashed' },
            data: [lim[1] != null ? { yAxis: lim[1] } : { yAxis: lim[0] as number }] } : undefined },
      ],
    }
  }, [recs, drift])

  const phaseLevel = st?.phase === '이상' ? 'danger' : st?.phase === '정비' ? 'warn' : 'ok'
  return (
    <Panel title={<>실시간 수집 <Help topic="live" /></>} actions={
      <div className="row">
        {st?.running ? <Level level={phaseLevel}>{st.equipment} · {st.phase}</Level> : <span className="faint">꺼짐</span>}
        {st?.running && <span className="muted" style={{ fontSize: 12 }}>기록 시각 {fmtDateTime(st.sim_time)}</span>}
      </div>}>
      <div className="stack" style={{ gap: 10 }}>
        {can('OPERATOR') ? (
          <div className="form-row">
            <select value={eq} onChange={(e) => setEq(e.target.value ? Number(e.target.value) : '')} disabled={st?.running} title={t('equipment')}>
              <option value="">{t('equipment')} 자동 선택</option>
              {(targets.data ?? []).map((x) => <option key={x.equipment_id} value={x.equipment_id}>{x.equipment}</option>)}
            </select>
            <select value={scenario} onChange={(e) => setScenario(e.target.value)} disabled={st?.running}>
              {Object.entries(st?.scenarios ?? { drift: '이상 발생 → 정비 → 회복', steady: '정상 운전' }).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            <select value={interval} onChange={(e) => setIntervalSec(Number(e.target.value))} disabled={st?.running} title="한 기록이 들어오는 간격">
              {[1, 2, 3, 5, 10].map((s) => <option key={s} value={s}>{s}초마다 1건</option>)}
            </select>
            {!st?.running
              ? <button className="primary" onClick={() => call('/live/start', { equipment_id: eq || null, scenario, interval_sec: interval })}>시작</button>
              : <>
                  <button onClick={() => call('/live/maintenance')} title="다음 기록에 정비 정지를 넣고 값을 정상으로 되돌립니다">정비 실시</button>
                  <button className="danger" onClick={() => call('/live/stop')}>정지</button>
                </>}
            <Link to="/simulation" className="right">3D 디지털 트윈에서 보기</Link>
          </div>
        ) : <div className="faint" style={{ fontSize: 12.5 }}>실시간 수집 시작·정지는 실무자 이상이 할 수 있습니다.</div>}
        {msg && <div className="notice error">{msg}</div>}
        {option ? <EChart option={option} height={210} /> : <div className="empty">시작하면 새 기록이 들어오는 대로 여기에 쌓입니다.</div>}
        <div className="grid cols-2" style={{ alignItems: 'start' }}>
          <div className="table-wrap" style={{ maxHeight: 220 }}>
            <table className="t">
              <thead><tr><th style={{ width: 92 }}>시각</th><th>{t('lot')}</th>{drift && <th className="num">{drift}</th>}<th className="num">불량</th></tr></thead>
              <tbody>
                {[...recs].reverse().slice(0, 15).map((r, i) => (
                  <tr key={r.lot_id} className={`${r.out_of_range.length ? 'feed-row-alarm' : ''} ${i === 0 ? 'flash' : ''}`}>
                    <td>{fmtDateTime(r.at).slice(5)}</td>
                    <td><Link to={`/genealogy?lot=${encodeURIComponent(r.lot_no)}`}>{r.lot_no}</Link></td>
                    {drift && <td className="num">{fmtNum(r.values[drift], 1)}</td>}
                    <td className="num">{r.defects}/{r.produced} ({fmtPct(r.rate)})</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="table-wrap" style={{ maxHeight: 220 }}>
            <table className="t">
              <thead><tr><th style={{ width: 92 }}>시각</th><th>경보·이벤트</th></tr></thead>
              <tbody>
                {live.alarms.slice(0, 15).map((a, i) => (
                  <tr key={i}><td>{fmtDateTime(a.at).slice(5)}</td>
                    <td style={{ color: a.type === 'alarm' ? 'var(--danger)' : 'var(--warn)' }} title={a.type === 'alarm' ? a.text : a.note ?? ''}>
                      {a.type === 'alarm' ? `▲ ${a.text}` : `■ ${a.kind === 'STOP' ? '정비 정지' : a.kind} — ${a.note ?? ''}`}
                    </td></tr>
                ))}
              </tbody>
            </table>
            {!live.alarms.length && <div className="empty">경보 없음</div>}
          </div>
        </div>
      </div>
    </Panel>
  )
}

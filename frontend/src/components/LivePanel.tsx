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
  const [speed, setSpeed] = useState(60)
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
        { type: 'value', name: '의심률 %', nameTextStyle: { fontSize: 11 }, ...AXIS_STYLE },
        { type: 'value', name: drift, scale: true, nameTextStyle: { fontSize: 11 }, ...AXIS_STYLE, splitLine: { show: false } },
      ],
      series: [
        { name: '의심률', type: 'bar', data: recs.map((r) => ({ value: +(r.rate * 100).toFixed(2), itemStyle: { color: r.out_of_range.length ? '#e3a49e' : '#a9c1e2' } })) },
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
        {st?.running && <span className="muted" style={{ fontSize: 12 }}>{st.clock === 'real' ? `실제 시계 · ${st.speeds?.[String(st.speed)] ?? `${st.speed}배`}` : '빠른 시연'}</span>}
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
            <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))} disabled={st?.running} title="실제 시계 기준 배속. 1배 = 실제 1시간마다 1시간 기록">
              {Object.entries(st?.speeds ?? { 1: '실시간', 10: '10배', 60: '60배 (1분 = 1시간)', 360: '360배 (10초 = 1시간)', 3600: '3600배 (1초 = 1시간)' }).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            {!st?.running
              ? <button className="primary" onClick={() => call('/live/start', { equipment_id: eq || null, scenario, speed })}>시작</button>
              : <>
                  <button onClick={() => call('/live/maintenance')} title="다음 기록에 정비 정지를 넣고 값을 정상으로 되돌립니다">정비 실시</button>
                  <button className="danger" onClick={() => call('/live/stop')}>정지</button>
                </>}
            <Link to="/review" className="right">불량 의심 검토</Link>
            <Link to="/simulation">3D 디지털 트윈에서 보기</Link>
          </div>
        ) : <div className="faint" style={{ fontSize: 12.5 }}>실시간 수집 시작·정지는 실무자 이상이 할 수 있습니다.</div>}
        {msg && <div className="notice error">{msg}</div>}
        {st?.running && <ProgressBar />}
        {option ? <EChart option={option} height={210} /> : <div className="empty">시작하면 새 기록이 들어오는 대로 여기에 쌓입니다.</div>}
        <div className="grid cols-2" style={{ alignItems: 'start' }}>
          <div className="table-wrap" style={{ maxHeight: 220 }}>
            <table className="t">
              <thead><tr><th style={{ width: 92 }}>시각</th><th>{t('lot')}</th>{drift && <th className="num">{drift}</th>}<th className="num">불량 의심</th></tr></thead>
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


/** 진행 중인 기록 구간: 센서 스트림(몇 초마다)으로 생산 수·조건값이 올라가고, 구간이 끝나면 1건으로 적재된다. */
function ProgressBar() {
  const live = useLive()
  const p = live.progress
  const st = live.status
  if (!p) return <div className="faint" style={{ fontSize: 12.5 }}>센서 스트림 대기 중… (구간이 끝나면 기록 1건이 적재됩니다)</div>
  const drift = p.drift_condition
  return (
    <div className="row wrap" style={{ gap: 12, fontSize: 13, padding: '6px 8px', border: '1px solid var(--border)', borderRadius: 3, background: 'var(--surface-2)' }}>
      <span>지금 <b>{fmtDateTime(p.at)}</b></span>
      <span className="muted">진행 중 구간 {fmtDateTime(p.window_start).slice(11)}~{fmtDateTime(p.window_end).slice(11)}</span>
      <span style={{ flex: '1 1 160px', minWidth: 160 }}><span className="bar" style={{ display: 'block' }}><span style={{ width: `${p.fraction * 100}%` }} /></span></span>
      <span>생산 <b>{p.produced}</b> / {p.planned}</span>
      {drift && <span style={{ color: p.out_of_range.includes(drift) ? 'var(--danger)' : undefined }}>{drift} {p.values[drift]}{p.units[drift] ?? ''}</span>}
      {st?.clock === 'real' && st.speed === 1 && <span className="faint">실시간: 1시간마다 기록 1건</span>}
    </div>
  )
}

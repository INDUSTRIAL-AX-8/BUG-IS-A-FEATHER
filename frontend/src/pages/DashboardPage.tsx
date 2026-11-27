import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import type { Judgment } from '../api/causes'
import { api, errorText } from '../api/client'
import type { ViolationRow } from '../api/types'
import { useAuth } from '../auth'
import { AXIS_STYLE, CHART_FONT, EChart, type EChartsOption } from '../components/EChart'
import { Empty, Level, Loading, Metric, PageHead, Panel, RuleBadge, ScoreBar } from '../components/ui'
import { fmtDateTime, fmtNum, fmtPct, NODE_LABEL } from '../lib/format'
import { useAsync } from '../lib/hooks'
import { LivePanel } from '../components/LivePanel'
import { useLiveRefresh } from '../lib/live'
import { Help } from '../components/Help'

export interface WatchItem {
  characteristic_id: number
  characteristic: string
  unit: string | null
  item_type: string | null
  process_step: string | null
  status: 'OK' | 'INSUFFICIENT' | 'FAILED'
  message: string | null
  risk_score: number | null
  expected_breach_at: string | null
  trend: 'up' | 'down' | 'flat' | null
  order: number[] | null
  run_at: string
}

interface ForecastDetail {
  disclaimer: string
  characteristic: { id: number; name: string; unit: string | null; usl: number | null; lsl: number | null; target: number | null }
  limits: { center: number; ucl: number; lcl: number; provisional: boolean } | null
  actual: { time: string; value: number }[]
  forecast: {
    status: string
    message: string | null
    run_at: string
    order: number[] | null
    risk_score: number | null
    expected_breach_at: string | null
    trend: string | null
    series: { index: string[]; mean: number[]; lower: number[]; upper: number[]; breach_prob: number[] } | null
  } | null
}

interface Accuracy {
  overall: { total: number; top1: number | null; top3: number | null; top5: number | null }
}

const TREND_TEXT: Record<string, string> = { up: '↑ 상승', down: '↓ 하강', flat: '→ 평탄' }

export function riskLevel(r: number | null): 'ok' | 'warn' | 'danger' {
  if (r === null) return 'ok'
  return r >= 0.5 ? 'danger' : r >= 0.2 ? 'warn' : 'ok'
}
const RISK_TEXT = { ok: '정상', warn: '주의', danger: '위험' }

export function DashboardPage() {
  const violations = useAsync(() => api.get<ViolationRow[]>('/spc/violations', { acknowledged: false, limit: 200 }), [])
  const watch = useAsync(() => api.get<{ disclaimer: string; items: WatchItem[] }>('/forecast/watchlist'), [])
  const defects = useAsync(() => api.get<Judgment[]>('/defects', { limit: 12 }), [])
  const summary = useAsync(() => api.get<{ total: number; confirmed: number }>('/defects/summary'), [])
  const accuracy = useAsync(() => api.get<Accuracy>('/accuracy').catch(() => null), [])
  const [selected, setSelected] = useState<number | null>(null)
  useLiveRefresh(() => { violations.reload(); defects.reload(); summary.reload() }, 1500)

  const watchItems = watch.data?.items ?? []
  const provisionalCount = (violations.data ?? []).filter((v) => v.provisional).length
  const alarms = (violations.data ?? []).length - provisionalCount
  const risky = watchItems.filter((w) => (w.risk_score ?? 0) >= 0.5).length
  const sel = selected ?? watchItems.find((w) => w.status === 'OK')?.characteristic_id ?? null

  return (
    <>
      <PageHead title="현장 모니터링" sub="실시간 수집 · SPC 위반 · ARIMA 주의 부품 · 최근 불량" />
      <div className="grid cols-4" style={{ marginBottom: 12 }}>
        <Metric label="불량 판정 (누적)" value={summary.data?.total ?? '-'} hint={summary.data ? `원인 확정 ${summary.data.confirmed}건` : undefined} />
        <Metric label="미확인 SPC 위반" value={violations.data ? alarms : '-'}
          hint={provisionalCount ? `임시 한계 ${provisionalCount}건 제외 (기준 구간 미지정)` : '확인 처리 전 위반'} />
        <Metric label="주의 부품 (위험 ≥ 0.5)" value={watch.data ? risky : '-'} hint="ARIMA 예측 기준" />
        <Metric label="원인 Top-3 정확도" value={accuracy.data?.overall.top3 != null ? fmtPct(accuracy.data.overall.top3) : '-'}
          hint={accuracy.data ? <Link to="/accuracy">확정 {accuracy.data.overall.total}건 기준 · 자세히</Link> : '확정 데이터 없음'} />
      </div>

      <div style={{ marginBottom: 12 }}><LivePanel /></div>
      <div className="grid cols-2" style={{ marginBottom: 12 }}>
        <WatchlistPanel data={watch.data} error={watch.error} loading={watch.loading} selected={sel} onSelect={setSelected} onRan={watch.reload} />
        {sel ? <ForecastChart charId={sel} /> : <Panel title="예측 차트"><Empty>주의 부품을 선택하세요.</Empty></Panel>}
      </div>

      <div className="grid cols-2">
        <Panel title="최근 불량" actions={<Link to="/defects">불량 판정</Link>} flush>
          <Loading error={defects.error} loading={defects.loading && !defects.data} />
          <table className="t">
            <thead><tr><th style={{ width: 130 }}>판정 시각</th><th>LOT</th><th>유형</th><th>확정 원인</th></tr></thead>
            <tbody>
              {(defects.data ?? []).map((j) => (
                <tr key={j.id}>
                  <td>{fmtDateTime(j.judged_at)}</td>
                  <td><Link to={`/genealogy?lot=${encodeURIComponent(j.lot_no)}`}>{j.lot_no}</Link></td>
                  <td>{j.defect_type}</td>
                  <td>{j.root_causes.length ? j.root_causes.map((r) => `${NODE_LABEL[r.type]} ${r.label}`).join(', ') : <span className="faint">미확정</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {defects.data && !defects.data.length && <Empty>불량 판정이 없습니다.</Empty>}
        </Panel>
        <ViolationsPanel rows={violations.data} error={violations.error} loading={violations.loading} height={420} />
      </div>
    </>
  )
}

function WatchlistPanel(props: {
  data: { disclaimer: string; items: WatchItem[] } | null
  error: string | null
  loading: boolean
  selected: number | null
  onSelect: (id: number) => void
  onRan: () => void
}) {
  const { can } = useAuth()
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const run = async () => {
    setBusy(true)
    setMsg(null)
    try {
      await api.post('/forecast/run')
      props.onRan()
    } catch (e) {
      setMsg(errorText(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Panel title={<>주의 부품 순위 (ARIMA)<Help topic="arima" /></>} flush
      actions={can('OPERATOR') && <button className="small" disabled={busy} onClick={run}>{busy ? '예측 중…' : '지금 예측 실행'}</button>}>
      <div className="notice" style={{ margin: 8 }}>{props.data?.disclaimer ?? '통계적 예측이며 확정 판단이 아닙니다.'} 관리한계·규격 중 안쪽 경계를 넘을 확률을 위험 점수로 씁니다.</div>
      {msg && <div className="notice error" style={{ margin: 8 }}>{msg}</div>}
      <Loading error={props.error} loading={props.loading && !props.data} />
      <div className="table-wrap" style={{ maxHeight: 330 }}>
        <table className="t">
          <thead>
            <tr><th>부품/공정</th><th>특성</th><th style={{ width: 150 }}>위험 점수</th><th style={{ width: 130 }}>예상 이탈 시점</th><th style={{ width: 70 }}>추세</th></tr>
          </thead>
          <tbody>
            {(props.data?.items ?? []).map((w) => {
              const lv = riskLevel(w.risk_score)
              return (
                <tr key={w.characteristic_id} className={`clickable ${props.selected === w.characteristic_id ? 'selected' : ''}`}
                  onClick={() => w.status === 'OK' && props.onSelect(w.characteristic_id)} title={w.message ?? ''}>
                  <td>{w.item_type ?? w.process_step}</td>
                  <td>{w.characteristic}</td>
                  <td>
                    {w.status === 'OK' ? (
                      <div className="row"><Level level={lv}>{RISK_TEXT[lv]}</Level><ScoreBar value={w.risk_score ?? 0} /><span style={{ fontSize: 12 }}>{fmtNum(w.risk_score, 2)}</span></div>
                    ) : <span className="badge neutral">{w.status === 'INSUFFICIENT' ? '데이터 부족' : '적합 실패'}</span>}
                  </td>
                  <td>{fmtDateTime(w.expected_breach_at)}</td>
                  <td>{w.trend ? TREND_TEXT[w.trend] : '-'}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {props.data && !props.data.items.length && <Empty>예측 결과가 없습니다. "지금 예측 실행"을 누르세요.</Empty>}
      </div>
    </Panel>
  )
}

function ForecastChart({ charId }: { charId: number }) {
  const d = useAsync(() => api.get<ForecastDetail>(`/forecast/${charId}`), [charId])
  const option = useMemo<EChartsOption | null>(() => {
    const x = d.data
    if (!x || !x.actual.length) return null
    const s = x.forecast?.series
    const actualX = x.actual.map((p) => fmtDateTime(p.time))
    const fcX = s ? s.index.map((t) => fmtDateTime(t)) : []
    const cats = [...actualX, ...fcX]
    const n = x.actual.length
    const pad = (arr: (number | null)[], before: number) => [...Array(before).fill(null), ...arr]
    const lines: object[] = []
    if (x.limits) {
      lines.push({ yAxis: x.limits.ucl, lineStyle: { color: '#24508f' }, label: { formatter: 'UCL' } })
      lines.push({ yAxis: x.limits.lcl, lineStyle: { color: '#24508f' }, label: { formatter: 'LCL' } })
    }
    if (x.characteristic.usl != null) lines.push({ yAxis: x.characteristic.usl, lineStyle: { color: '#a8261d', type: 'dashed' }, label: { formatter: 'USL' } })
    if (x.characteristic.lsl != null) lines.push({ yAxis: x.characteristic.lsl, lineStyle: { color: '#a8261d', type: 'dashed' }, label: { formatter: 'LSL' } })
    return {
      animation: false,
      grid: { left: 56, right: 44, top: 24, bottom: 28 },
      legend: { top: 0, right: 0, textStyle: { fontFamily: CHART_FONT, fontSize: 11 }, data: ['실측', '예측', '예측 구간'] },
      tooltip: { trigger: 'axis', textStyle: { fontFamily: CHART_FONT, fontSize: 12 } },
      xAxis: { type: 'category', data: cats, ...AXIS_STYLE, axisLabel: { ...AXIS_STYLE.axisLabel, hideOverlap: true } },
      yAxis: { type: 'value', scale: true, ...AXIS_STYLE },
      dataZoom: [{ type: 'inside', start: Math.max(0, 100 - (120 / cats.length) * 100), end: 100 }],
      series: [
        { name: '실측', type: 'line', data: x.actual.map((p) => p.value), symbolSize: 3, lineStyle: { width: 1, color: '#4b5563' }, itemStyle: { color: '#4b5563' },
          markLine: { symbol: 'none', silent: true, label: { fontSize: 11, position: 'end' }, data: lines } },
        // 예측 구간: 하한을 기준선으로 쌓아 띠를 그린다
        { name: '예측 구간', type: 'line', stack: 'band', data: s ? pad(s.lower, n) : [], symbol: 'none', lineStyle: { opacity: 0 }, silent: true },
        { name: '예측 구간', type: 'line', stack: 'band', data: s ? pad(s.upper.map((u, i) => u - s.lower[i]), n) : [], symbol: 'none', lineStyle: { opacity: 0 }, areaStyle: { color: '#a9c1e2', opacity: 0.55 }, silent: true },
        { name: '예측', type: 'line', data: s ? pad([x.actual[n - 1].value, ...s.mean], n - 1) : [], symbolSize: 4, lineStyle: { width: 2, type: 'dashed', color: '#1d4580' }, itemStyle: { color: '#1d4580' } },
      ],
    }
  }, [d.data])
  const f = d.data?.forecast
  return (
    <Panel title={d.data ? `예측 · ${d.data.characteristic.name} (${d.data.characteristic.unit ?? '-'})` : '예측'}
      actions={<Link to={`/spc?char=${charId}`}>관리도</Link>}>
      <Loading error={d.error} loading={d.loading && !d.data} />
      {f && (
        <div className="row wrap" style={{ fontSize: 13, marginBottom: 6, gap: 14 }}>
          <span>모형 ARIMA({f.order?.join(',') ?? '-'})</span>
          <span>위험 점수 <b>{fmtNum(f.risk_score, 2)}</b></span>
          <span>예상 이탈 {fmtDateTime(f.expected_breach_at)}</span>
          <span className="faint">실행 {fmtDateTime(f.run_at)}</span>
          {d.data?.limits?.provisional && <span className="badge warn">■ 임시 관리한계 기준</span>}
        </div>
      )}
      {option ? <EChart option={option} height={300} /> : d.data && <Empty>표시할 데이터가 없습니다.</Empty>}
      <div className="faint" style={{ fontSize: 12, marginTop: 4 }}>{d.data?.disclaimer}</div>
    </Panel>
  )
}

export function ViolationsPanel(props: { rows: ViolationRow[] | null; error: string | null; loading: boolean; height?: number }) {
  return (
    <Panel title={<>미확인 SPC 위반<Help topic="nelson" /></>} actions={<Link to="/spc">관리도 보기</Link>} flush>
      <Loading error={props.error} loading={props.loading && !props.rows} />
      <div className="table-wrap" style={{ maxHeight: props.height ?? 360 }}>
        <table className="t">
          <thead>
            <tr><th style={{ width: 130 }}>발생 시각</th><th>부품/공정 · 특성</th><th style={{ width: 96 }}>룰</th><th>관련 LOT</th></tr>
          </thead>
          <tbody>
            {(props.rows ?? []).map((r) => (
              <tr key={r.id}>
                <td>{fmtDateTime(r.occurred_at)}</td>
                <td><Link to={`/spc?char=${r.characteristic_id}`}>{r.owner} · {r.characteristic}</Link></td>
                <td title={r.description}><RuleBadge rule={r.rule_no} />{r.provisional && <span className="badge neutral" style={{ marginLeft: 4 }} title="고정된 기준 구간 없이 처음 점들로 만든 임시 한계의 위반입니다. 경보로 세지 않습니다.">임시</span>}</td>
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

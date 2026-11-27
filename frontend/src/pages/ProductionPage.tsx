import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { AXIS_STYLE, CHART_FONT, EChart, type EChartsOption } from '../components/EChart'
import { Help } from '../components/Help'
import { Empty, Loading, Metric, PageHead, Panel, ScoreBar } from '../components/ui'
import { fmtNum, fmtPct } from '../lib/format'
import { useAsync } from '../lib/hooks'
import { useLiveRefresh } from '../lib/live'
import { useTerms } from '../lib/terms'

interface Day { date: string; produced: number; defects: number; rate: number; capa: number; utilization: number | null; hours: number; stop_hours: number }
interface Cause { cause: string; kind: string; node: string; excess_defects: number | null; share: number | null; rate_out?: number; rate_in?: number; rows_out?: number; range?: string; evidence: string; confirmed_count?: number }
interface Dash {
  message?: string
  equipment: { id: number; name: string; capacity_per_hour: number; capacity_source: string }[]
  period: { from: string; to: string }
  kpi: { produced: number; capa: number; utilization: number | null; confirmed_defects: number; defect_rate: number; good: number; stop_hours: number; stops: number; pending_suspects: number; pending_qty: number }
  series: Day[]
  by_type: { type: string; qty: number }[]
  by_stage: { stage: string; qty: number }[]
  top_causes: Cause[]
}

export function ProductionPage() {
  const { t } = useTerms()
  const d = useAsync(() => api.get<Dash>('/production/dashboard'), [])
  useLiveRefresh(d.reload, 1200)
  const x = d.data
  const trend = useMemo<EChartsOption | null>(() => {
    if (!x?.series?.length) return null
    return {
      animation: false,
      grid: { left: 56, right: 56, top: 30, bottom: 30 },
      legend: { top: 0, textStyle: { fontFamily: CHART_FONT, fontSize: 11 } },
      tooltip: { trigger: 'axis', textStyle: { fontFamily: CHART_FONT, fontSize: 12 } },
      xAxis: { type: 'category', data: x.series.map((s) => s.date.slice(5)), ...AXIS_STYLE },
      yAxis: [
        { type: 'value', name: '개', nameTextStyle: { fontSize: 11 }, ...AXIS_STYLE },
        { type: 'value', name: '불량률 %', nameTextStyle: { fontSize: 11 }, ...AXIS_STYLE, splitLine: { show: false } },
      ],
      series: [
        { name: '양품', type: 'bar', stack: 'p', data: x.series.map((s) => s.produced - s.defects), itemStyle: { color: '#7499cc' } },
        { name: '확정 불량', type: 'bar', stack: 'p', data: x.series.map((s) => s.defects), itemStyle: { color: '#c8423a' } },
        { name: 'CAPA', type: 'line', step: 'middle', data: x.series.map((s) => Math.round(s.capa)), symbol: 'none', lineStyle: { color: '#1c222b', type: 'dashed', width: 1.5 } },
        { name: '불량률', type: 'line', yAxisIndex: 1, data: x.series.map((s) => +(s.rate * 100).toFixed(2)), symbolSize: 6, lineStyle: { color: '#c8423a', width: 2 }, itemStyle: { color: '#c8423a' } },
      ],
    }
  }, [x])
  const pareto = useMemo<EChartsOption | null>(() => {
    if (!x?.by_type?.length) return null
    const total = x.by_type.reduce((a, b) => a + b.qty, 0)
    let acc = 0
    return {
      animation: false,
      grid: { left: 56, right: 48, top: 24, bottom: 30 },
      tooltip: { trigger: 'axis', textStyle: { fontFamily: CHART_FONT, fontSize: 12 } },
      xAxis: { type: 'category', data: x.by_type.map((b) => b.type), ...AXIS_STYLE },
      yAxis: [{ type: 'value', name: '불량 수', nameTextStyle: { fontSize: 11 }, ...AXIS_STYLE },
        { type: 'value', max: 100, name: '누적 %', nameTextStyle: { fontSize: 11 }, ...AXIS_STYLE, splitLine: { show: false } }],
      series: [
        { name: '불량 수', type: 'bar', data: x.by_type.map((b) => b.qty), itemStyle: { color: '#c8423a' }, label: { show: true, position: 'top', fontSize: 11 } },
        { name: '누적 비율', type: 'line', yAxisIndex: 1, data: x.by_type.map((b) => { acc += b.qty; return +((acc / total) * 100).toFixed(1) }), lineStyle: { color: '#1c222b' }, itemStyle: { color: '#1c222b' } },
      ],
    }
  }, [x])

  if (!x) return <><PageHead title="생산 대시보드" /><Loading error={d.error} loading={d.loading} /></>
  if (x.message) return <><PageHead title="생산 대시보드" /><Panel><Empty>{x.message}</Empty></Panel></>
  const k = x.kpi
  const maxCause = Math.max(1, ...x.top_causes.map((c) => c.excess_defects ?? 0))
  return (
    <>
      <PageHead title="생산 대시보드" sub={`${x.equipment.map((e) => e.name).join(', ')} · ${x.period.from?.slice(0, 10)} ~ ${x.period.to?.slice(0, 10)} · 불량은 실무자가 확정해 적재한 데이터만 집계`} />
      <div className="grid cols-4" style={{ marginBottom: 12 }}>
        <Metric label="생산량" value={fmtNum(k.produced, 0)} hint={`양품 ${fmtNum(k.good, 0)}`} />
        <Metric label={<>CAPA 대비 가동률<Help title="CAPA 가동률">생산량 ÷ (시간당 생산 능력 × 계획 시간). 계획 시간에는 정지 시간도 포함합니다. 시간당 능력은 {x.equipment.map((e) => `${e.name} ${fmtNum(e.capacity_per_hour, 0)}개/시간 (${e.capacity_source})`).join(', ')}.</Help></> as unknown as string}
          value={fmtPct(k.utilization)} hint={`CAPA ${fmtNum(k.capa, 0)} · 정지 ${k.stops}회 ${fmtNum(k.stop_hours, 1)}시간`} />
        <Metric label="확정 불량" value={`${fmtNum(k.confirmed_defects, 0)} (${fmtPct(k.defect_rate)})`} hint="불량 데이터로 적재된 수량" />
        <Metric label="검토 대기 의심" value={k.pending_suspects} hint={<Link to="/review">{fmtNum(k.pending_qty, 0)}개 의심 · 검토하러 가기</Link>} />
      </div>
      <div className="grid cols-2" style={{ marginBottom: 12 }}>
        <Panel title="일별 생산 · CAPA · 확정 불량률">{trend ? <EChart option={trend} height={300} /> : <Empty>데이터 없음</Empty>}</Panel>
        <Panel title={<>불량 원인 Top<Help title="초과 불량">그 조건이 허용(또는 학습) 범위를 벗어난 시간의 불량 수에서, 모든 조건이 정상인 시간의 불량률로 기대되는 수를 뺀 값입니다. 그 조건 이탈 때문에 늘었다고 볼 수 있는 불량의 양입니다.</Help></>} flush>
          <table className="t">
            <thead><tr><th style={{ width: 34 }}>순위</th><th>원인</th><th style={{ width: 160 }}>초과 불량 (비중)</th><th>근거</th></tr></thead>
            <tbody>
              {x.top_causes.map((c, i) => (
                <tr key={c.node + i} style={{ height: 'auto' }}>
                  <td>{i + 1}</td>
                  <td style={{ whiteSpace: 'normal' }}><b>{c.cause}</b>{c.range && <div className="faint" style={{ fontSize: 11.5 }}>기준 {c.range}</div>}</td>
                  <td>{c.excess_defects != null ? <div className="row"><ScoreBar value={c.excess_defects} max={maxCause} /><span style={{ fontSize: 12, whiteSpace: 'nowrap' }}>{fmtNum(c.excess_defects, 0)} ({fmtPct(c.share)})</span></div> : `확정 ${c.confirmed_count}건`}</td>
                  <td style={{ whiteSpace: 'normal', fontSize: 12.5 }}>{c.evidence}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!x.top_causes.length && <Empty>범위를 벗어난 조건과 연결된 확정 불량이 아직 없습니다.</Empty>}
          <div style={{ padding: 8 }}><Link to="/drivers">{t('drivers')}에서 근거 자세히 보기</Link></div>
        </Panel>
      </div>
      <div className="grid cols-2">
        <Panel title="불량 유형 (파레토)">{pareto ? <EChart option={pareto} height={260} /> : <Empty>확정 불량 없음</Empty>}</Panel>
        <Panel title="불량을 잡은 단계" flush>
          <table className="t">
            <thead><tr><th>단계</th><th className="num">확정 불량</th><th style={{ width: 200 }} /></tr></thead>
            <tbody>{x.by_stage.map((s) => (
              <tr key={s.stage}><td>{s.stage}</td><td className="num">{fmtNum(s.qty, 0)}</td><td><ScoreBar value={s.qty} max={Math.max(1, ...x.by_stage.map((b) => b.qty))} /></td></tr>
            ))}</tbody>
          </table>
          {!x.by_stage.length && <Empty>확정 불량 없음</Empty>}
          <div className="faint" style={{ fontSize: 12, padding: 8 }}>'이력·직접 등록' = 과거 기록에서 가져왔거나 불량 등록 화면에서 직접 등록한 불량</div>
        </Panel>
      </div>
    </>
  )
}

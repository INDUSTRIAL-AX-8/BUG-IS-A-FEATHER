import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { Characteristic, ChartData } from '../api/types'
import { fmtDateTime, fmtNum } from '../lib/format'
import { useAsync } from '../lib/hooks'
import { AXIS_STYLE, CHART_FONT, EChart, type EChartsOption } from './EChart'
import { Empty } from './ui'

/** 노드와 관련된 특성의 관리도 축소판. highlightLot 이 들어간 포인트를 강조. */
export function MiniChart(props: { itemTypeId?: number | null; stepId?: number | null; highlightLot?: number | null; around?: string | null }) {
  const chars = useAsync(() => api.get<Characteristic[]>('/characteristics', { active_only: true }), [])
  const related = useMemo(
    () => (chars.data ?? []).filter((c) =>
      (props.itemTypeId && c.item_type_id === props.itemTypeId) || (props.stepId && c.process_step_id === props.stepId)),
    [chars.data, props.itemTypeId, props.stepId],
  )
  const [pick, setPick] = useState<number | null>(null)
  const charId = pick && related.some((c) => c.id === pick) ? pick : related[0]?.id ?? null

  if (!chars.data) return null
  if (!related.length) return <Empty>관련 측정 특성이 없습니다.</Empty>
  return (
    <div>
      <div className="row" style={{ marginBottom: 4 }}>
        <select value={charId ?? ''} onChange={(e) => setPick(Number(e.target.value))} style={{ flex: 1 }}>
          {related.map((c) => <option key={c.id} value={c.id}>{c.name} ({c.unit ?? '-'})</option>)}
        </select>
        {charId && <Link to={`/spc?char=${charId}`} style={{ fontSize: 12 }}>관리도 열기</Link>}
      </div>
      {charId && <MiniChartBody charId={charId} highlightLot={props.highlightLot} around={props.around} />}
    </div>
  )
}

function MiniChartBody({ charId, highlightLot, around }: { charId: number; highlightLot?: number | null; around?: string | null }) {
  const chart = useAsync(() => api.get<ChartData>(`/spc/${charId}/chart`), [charId])
  const option = useMemo<EChartsOption | null>(() => {
    const d = chart.data
    if (!d || !d.points.length) return null
    let pts = d.points
    const hitIdx = highlightLot ? pts.findIndex((p) => p.lot_ids.includes(highlightLot)) : -1
    let center = hitIdx
    if (center < 0 && around) center = pts.findIndex((p) => p.time >= around)
    if (center < 0) center = pts.length - 1
    const start = Math.max(0, center - 40)
    pts = pts.slice(start, start + 80)
    return {
      animation: false,
      grid: { left: 46, right: 10, top: 8, bottom: 20 },
      tooltip: {
        trigger: 'axis', textStyle: { fontFamily: CHART_FONT, fontSize: 11 },
        formatter: (items: { dataIndex: number }[]) => {
          const p = pts[items[0].dataIndex]
          return `${fmtDateTime(p.time)}<br/>${fmtNum(p.value, 4)}${p.rules.length ? ' · R' + p.rules.join(',R') : ''}`
        },
      },
      xAxis: { type: 'category', data: pts.map((p) => fmtDateTime(p.time)), ...AXIS_STYLE, axisLabel: { show: false } },
      yAxis: { type: 'value', scale: true, ...AXIS_STYLE },
      series: [
        { type: 'line', data: pts.map((p) => p.value), symbolSize: 3, lineStyle: { width: 1, color: '#6b7280' }, itemStyle: { color: '#6b7280' } },
        { type: 'line', data: pts.map((p) => p.ucl), symbol: 'none', lineStyle: { width: 1, color: '#24508f' }, silent: true },
        { type: 'line', data: pts.map((p) => p.lcl), symbol: 'none', lineStyle: { width: 1, color: '#24508f' }, silent: true },
        { type: 'scatter', data: pts.map((p) => (p.rules.length ? p.value : null)), symbol: 'diamond', symbolSize: 7, itemStyle: { color: '#a8261d' } },
        {
          type: 'scatter', symbolSize: 12, symbol: 'circle',
          data: pts.map((p) => (highlightLot && p.lot_ids.includes(highlightLot) ? p.value : null)),
          itemStyle: { color: 'transparent', borderColor: '#1d4580', borderWidth: 2 },
        },
      ],
    }
  }, [chart.data, highlightLot, around])
  if (!chart.data) return <div className="empty">불러오는 중…</div>
  return option ? <EChart option={option} height={150} /> : <Empty>측정값 없음</Empty>
}

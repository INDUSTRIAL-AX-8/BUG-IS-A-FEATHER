import { useEffect, useRef } from 'react'
import * as echarts from 'echarts/core'
import { LineChart, ScatterChart, BarChart, PieChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  MarkLineComponent,
  MarkAreaComponent,
  DataZoomComponent,
  LegendComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([
  LineChart, ScatterChart, BarChart, PieChart, GridComponent, TooltipComponent, MarkLineComponent,
  MarkAreaComponent, DataZoomComponent, LegendComponent, CanvasRenderer,
])

export type EChartsOption = echarts.EChartsCoreOption

export const CHART_FONT = "'Pretendard', 'Malgun Gothic', sans-serif"
/** 차트 공통 색: 남색 계열 주색 + 의미색(불량 빨강, 기준 회색) */
export const CHART_COLORS = {
  primary: '#2453c4', primarySoft: '#a8bdec', good: '#6f92dd', defect: '#e0533d', defectSoft: '#f4a597',
  ink: '#161c26', grid: '#eef1f5', axis: '#cdd3db', label: '#5b6574', ok: '#18794e', warn: '#d98a12',
  series: ['#2453c4', '#6f92dd', '#14a38b', '#d98a12', '#8a63d2', '#e0533d', '#5b6574', '#a8bdec'],
}

// 모든 차트에 적용되는 테마 (폰트·축·툴팁·범례). 차트별로는 데이터와 의미색만 정한다
echarts.registerTheme('bif', {
  color: CHART_COLORS.series,
  backgroundColor: 'transparent',
  textStyle: { fontFamily: CHART_FONT },
  legend: { textStyle: { color: CHART_COLORS.label, fontSize: 12 }, itemWidth: 14, itemHeight: 8, icon: 'roundRect' },
  tooltip: { backgroundColor: '#161c26', borderWidth: 0, padding: [8, 12], textStyle: { color: '#f2f5f9', fontSize: 12 },
    extraCssText: 'border-radius:8px;box-shadow:0 8px 24px rgba(16,24,40,.18);' },
  categoryAxis: { axisLine: { lineStyle: { color: CHART_COLORS.axis } }, axisTick: { show: false }, axisLabel: { color: CHART_COLORS.label, fontSize: 11 }, splitLine: { lineStyle: { color: CHART_COLORS.grid } } },
  valueAxis: { axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: CHART_COLORS.label, fontSize: 11 }, splitLine: { lineStyle: { color: CHART_COLORS.grid } }, nameTextStyle: { color: CHART_COLORS.label } },
  timeAxis: { axisLine: { lineStyle: { color: CHART_COLORS.axis } }, axisLabel: { color: CHART_COLORS.label, fontSize: 11 }, splitLine: { lineStyle: { color: CHART_COLORS.grid } } },
  bar: { itemStyle: { borderRadius: [3, 3, 0, 0] } },
  line: { symbolSize: 5, lineStyle: { width: 2 } },
})

export function EChart(props: {
  option: EChartsOption
  height: number
  onClick?: (params: { dataIndex: number; seriesName?: string; data?: unknown }) => void
  group?: string
}) {
  const ref = useRef<HTMLDivElement>(null)
  const chart = useRef<echarts.ECharts | null>(null)
  const clickRef = useRef(props.onClick)
  clickRef.current = props.onClick

  useEffect(() => {
    if (!ref.current) return
    const c = echarts.init(ref.current, 'bif', { renderer: 'canvas' })
    chart.current = c
    c.on('click', (p) => clickRef.current?.(p as never))
    if (props.group) {
      c.group = props.group
      echarts.connect(props.group)
    }
    const ro = new ResizeObserver(() => c.resize())
    ro.observe(ref.current)
    return () => {
      ro.disconnect()
      c.dispose()
      chart.current = null
    }
  }, [props.group])

  useEffect(() => {
    chart.current?.setOption(props.option, true)
  }, [props.option])

  return <div ref={ref} style={{ width: '100%', height: props.height }} />
}

export const AXIS_STYLE = {
  axisLine: { lineStyle: { color: CHART_COLORS.axis } },
  axisLabel: { color: CHART_COLORS.label, fontFamily: CHART_FONT, fontSize: 11 },
  splitLine: { lineStyle: { color: CHART_COLORS.grid } },
}

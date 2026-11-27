import { useEffect, useRef } from 'react'
import * as echarts from 'echarts/core'
import { LineChart, ScatterChart, BarChart } from 'echarts/charts'
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
  LineChart, ScatterChart, BarChart, GridComponent, TooltipComponent, MarkLineComponent,
  MarkAreaComponent, DataZoomComponent, LegendComponent, CanvasRenderer,
])

export type EChartsOption = echarts.EChartsCoreOption

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
    const c = echarts.init(ref.current, undefined, { renderer: 'canvas' })
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

export const CHART_FONT = "'Pretendard', 'Malgun Gothic', sans-serif"
export const AXIS_STYLE = {
  axisLine: { lineStyle: { color: '#b8bec7' } },
  axisLabel: { color: '#5a6370', fontFamily: CHART_FONT, fontSize: 11 },
  splitLine: { lineStyle: { color: '#eceef1' } },
}

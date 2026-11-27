import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { AXIS_STYLE, CHART_FONT, EChart, type EChartsOption } from '../components/EChart'
import { Empty, Loading, Metric, PageHead, Panel } from '../components/ui'
import { fmtDateTime, fmtPct } from '../lib/format'
import { useAsync } from '../lib/hooks'

interface Rates { total: number; top1: number | null; top3: number | null; top5: number | null }
interface Accuracy {
  overall: Rates
  bucket: string
  series: ({ period: string; cumulative: Rates } & Rates)[]
  recent: { judgment_id: number; judged_at: string; rank: number | null; candidates: number }[]
  note: string
}

export function AccuracyPage() {
  const [bucket, setBucket] = useState('week')
  const acc = useAsync(() => api.get<Accuracy>('/accuracy', { bucket }), [bucket])
  const a = acc.data
  const option = useMemo<EChartsOption | null>(() => {
    if (!a || !a.series.length) return null
    const x = a.series.map((s) => s.period)
    const pct = (v: number | null) => (v == null ? null : Math.round(v * 1000) / 10)
    const line = (name: string, data: (number | null)[], color: string, dashed = false) => ({
      name, type: 'line', data, symbolSize: 5, lineStyle: { color, width: 2, type: dashed ? 'dashed' : 'solid' }, itemStyle: { color },
    })
    return {
      animation: false,
      grid: { left: 48, right: 20, top: 30, bottom: 30 },
      legend: { top: 0, textStyle: { fontFamily: CHART_FONT, fontSize: 11 } },
      tooltip: { trigger: 'axis', valueFormatter: (v: number) => (v == null ? '-' : `${v}%`), textStyle: { fontFamily: CHART_FONT, fontSize: 12 } },
      xAxis: { type: 'category', data: x, ...AXIS_STYLE },
      yAxis: { type: 'value', min: 0, max: 100, ...AXIS_STYLE, axisLabel: { ...AXIS_STYLE.axisLabel, formatter: '{value}%' } },
      series: [
        line('Top-1 (기간)', a.series.map((s) => pct(s.top1)), '#7499cc'),
        line('Top-3 (기간)', a.series.map((s) => pct(s.top3)), '#24508f'),
        line('Top-3 (누적)', a.series.map((s) => pct(s.cumulative.top3)), '#1c222b', true),
      ],
    }
  }, [a])
  return (
    <>
      <PageHead title="정확도 지표" sub="확정 원인이 판정 시점 원인 후보 Top-1/3/5 안에 있었던 비율. 판정·확정이 쌓일수록 올라가는지 확인합니다.">
        <select value={bucket} onChange={(e) => setBucket(e.target.value)}>
          <option value="day">일별</option><option value="week">주별</option><option value="month">월별</option>
        </select>
      </PageHead>
      <Loading error={acc.error} loading={acc.loading && !a} />
      {a && (
        <>
          <div className="grid cols-4" style={{ marginBottom: 12 }}>
            <Metric label="확정 원인 있는 판정" value={a.overall.total} hint="집계 대상" />
            <Metric label="Top-1 정확도" value={fmtPct(a.overall.top1)} />
            <Metric label="Top-3 정확도" value={fmtPct(a.overall.top3)} />
            <Metric label="Top-5 정확도" value={fmtPct(a.overall.top5)} />
          </div>
          <div className="grid cols-2">
            <Panel title="기간별 추이">
              {option ? <EChart option={option} height={320} /> : <Empty>확정 원인이 있는 판정이 아직 없습니다. 계보 그래프에서 "이 노드를 원인으로 확정"을 누르면 쌓입니다.</Empty>}
              <div className="faint" style={{ fontSize: 12, marginTop: 6 }}>{a.note}</div>
            </Panel>
            <Panel title="기간별 표" flush>
              <table className="t">
                <thead><tr><th>기간</th><th className="num">건수</th><th className="num">Top-1</th><th className="num">Top-3</th><th className="num">Top-5</th><th className="num">누적 Top-3</th></tr></thead>
                <tbody>
                  {a.series.map((s) => (
                    <tr key={s.period}><td>{s.period}</td><td className="num">{s.total}</td><td className="num">{fmtPct(s.top1)}</td>
                      <td className="num">{fmtPct(s.top3)}</td><td className="num">{fmtPct(s.top5)}</td><td className="num">{fmtPct(s.cumulative.top3)}</td></tr>
                  ))}
                </tbody>
              </table>
              <div className="panel-head" style={{ borderTop: '1px solid var(--border)' }}><h3>최근 확정 건</h3></div>
              <table className="t">
                <thead><tr><th>판정</th><th>판정 시각</th><th className="num">확정 원인의 당시 순위</th></tr></thead>
                <tbody>
                  {a.recent.map((r) => (
                    <tr key={r.judgment_id}><td>판정 #{r.judgment_id}</td><td>{fmtDateTime(r.judged_at)}</td>
                      <td className="num">{r.rank == null ? <span className="faint">후보 밖</span> : `${r.rank}위 / ${r.candidates}`}</td></tr>
                  ))}
                </tbody>
              </table>
              <div style={{ padding: 8 }}><Link to="/defects">불량 판정 화면</Link></div>
            </Panel>
          </div>
        </>
      )}
    </>
  )
}

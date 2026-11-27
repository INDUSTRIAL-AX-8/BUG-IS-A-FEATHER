import { useMemo } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import { CHART_COLORS, EChart, type EChartsOption } from '../components/EChart'
import { Help } from '../components/Help'
import { Icon } from '../components/Icon'
import { Empty, Loading, Metric, PageHead, Panel } from '../components/ui'
import { fmtNum, fmtPct } from '../lib/format'
import { useAsync } from '../lib/hooks'
import { useLiveRefresh } from '../lib/live'
import { useTerms } from '../lib/terms'

interface Bucket { date: string; produced: number; defects: number; rate: number | null; capa: number; utilization: number | null; stop_hours: number }
interface Kpi { produced: number; capa: number; utilization: number | null; confirmed_defects: number; defect_rate: number; good: number; stop_hours: number; stops: number; pending_suspects: number; pending_qty: number }
interface Cause { cause: string; kind: string; excess_defects: number | null; share: number | null; rate_out?: number; rate_in?: number; range?: string; evidence: string; confirmed_count?: number }
interface Overview {
  message?: string
  equipment: { id: number; name: string; capacity_per_hour: number; capacity_source: string }[]
  period: { from: string; to: string }
  kpi: Kpi
  previous: { period: { from: string; to: string }; kpi: Kpi } | null
  targets: { defect_rate?: number; utilization?: number }
  bucket: 'day' | 'week'
  trend: Bucket[]
  by_type: { type: string; qty: number }[]
  by_equipment: { id: number; name: string; produced: number; defects: number; rate: number; utilization: number | null }[]
  top_causes: Cause[]
  summary: string[]
}

const PRESETS: { key: string; label: string; days: number | null }[] = [
  { key: '7d', label: '최근 7일', days: 7 },
  { key: '30d', label: '최근 30일', days: 30 },
  { key: '90d', label: '최근 90일', days: 90 },
  { key: 'all', label: '전체', days: null },
]

const iso = (d: Date) => {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}
const day = (s?: string | null) => (s ? s.slice(0, 10) : '')

/** 경영 현황: 기간을 고르면 생산·CAPA·불량·원인을 한 화면에. 기간은 주소에 남아 링크로 공유된다. */
export function OverviewPage() {
  const { t } = useTerms()
  const [sp, setSp] = useSearchParams()
  const range = useAsync(() => api.get<{ from: string | null; to: string | null }>('/production/range'), [])
  const preset = sp.get('p') ?? (sp.get('from') ? 'custom' : '30d')
  const anchor = range.data?.to ? new Date(range.data.to) : null
  const { from, to } = useMemo(() => {
    if (preset === 'custom') return { from: sp.get('from') ?? '', to: sp.get('to') ?? '' }
    const p = PRESETS.find((x) => x.key === preset)
    if (!anchor || !p) return { from: '', to: '' }
    if (p.days == null) return { from: day(range.data?.from), to: day(range.data?.to) }
    const s = new Date(anchor)
    s.setDate(s.getDate() - p.days + 1)
    return { from: iso(s), to: iso(anchor) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preset, sp, range.data])
  const ready = preset === 'custom' || !!range.data
  const d = useAsync(() => (ready ? api.get<Overview>('/production/overview', { from: from ? `${from}T00:00:00` : undefined, to: to ? `${to}T23:59:59` : undefined }) : Promise.resolve(null)), [from, to, ready])
  useLiveRefresh(d.reload, 1500)
  const x = d.data

  const setPreset = (key: string) => setSp(key === '30d' ? {} : { p: key }, { replace: true })
  const setCustom = (f: string, tt: string) => setSp({ from: f, to: tt }, { replace: true })

  const trend = useMemo<EChartsOption | null>(() => {
    if (!x?.trend?.length) return null
    const goal = x.targets.defect_rate
    return {
      animation: false,
      grid: { left: 52, right: 52, top: 40, bottom: 28 },
      legend: { top: 0, left: 0 },
      tooltip: { trigger: 'axis', valueFormatter: (v: number) => (typeof v === 'number' ? v.toLocaleString() : String(v)) },
      xAxis: { type: 'category', data: x.trend.map((s) => (x.bucket === 'week' ? `${s.date.slice(5)} 주` : s.date.slice(5))) },
      yAxis: [{ type: 'value', name: '개' }, { type: 'value', name: '불량률 %', splitLine: { show: false } }],
      series: [
        { name: '양품', type: 'bar', stack: 'p', barMaxWidth: 28, data: x.trend.map((s) => Math.round(s.produced - s.defects)), itemStyle: { color: CHART_COLORS.primarySoft, borderRadius: 0 } },
        { name: '불량', type: 'bar', stack: 'p', barMaxWidth: 28, data: x.trend.map((s) => Math.round(s.defects)), itemStyle: { color: CHART_COLORS.defect } },
        { name: 'CAPA', type: 'line', step: 'middle', symbol: 'none', data: x.trend.map((s) => (s.capa ? Math.round(s.capa) : null)), lineStyle: { color: CHART_COLORS.ink, type: 'dashed', width: 1.5 }, itemStyle: { color: CHART_COLORS.ink } },
        { name: '불량률', type: 'line', yAxisIndex: 1, smooth: true, connectNulls: false, data: x.trend.map((s) => (s.rate == null ? null : +(s.rate * 100).toFixed(2))), lineStyle: { color: CHART_COLORS.defect, width: 2.5 }, itemStyle: { color: CHART_COLORS.defect },
          markLine: goal ? { symbol: 'none', label: { formatter: `목표 ${(goal * 100).toFixed(1)}%`, color: CHART_COLORS.ok, fontSize: 11 }, lineStyle: { color: CHART_COLORS.ok, type: 'dotted' }, data: [{ yAxis: +(goal * 100).toFixed(2) }] } : undefined },
      ],
    }
  }, [x])

  const donut = useMemo<EChartsOption | null>(() => {
    if (!x?.by_type?.length) return null
    const total = x.by_type.reduce((a, b) => a + b.qty, 0)
    return {
      animation: false,
      tooltip: { trigger: 'item', formatter: (p: { name: string; value: number; percent: number }) => `${p.name}<br/>${Math.round(p.value).toLocaleString()}개 · ${p.percent}%` },
      legend: { orient: 'vertical', right: 0, top: 'middle', formatter: (n: string) => { const v = x.by_type.find((b) => b.type === n)?.qty ?? 0; return `${n}  ${Math.round((v / total) * 100)}%` } },
      series: [{ type: 'pie', radius: ['52%', '78%'], center: ['32%', '50%'], avoidLabelOverlap: true, label: { show: true, position: 'center', formatter: `{a|${Math.round(total).toLocaleString()}}\n{b|불량 개수}`, rich: { a: { fontSize: 22, fontWeight: 700, color: CHART_COLORS.ink }, b: { fontSize: 11, color: CHART_COLORS.label, padding: [4, 0, 0, 0] } } },
        itemStyle: { borderColor: '#fff', borderWidth: 2 }, data: x.by_type.map((b) => ({ name: b.type, value: b.qty })) }],
    }
  }, [x])

  const k = x?.kpi
  const pk = x?.previous?.kpi
  const pct = (a: number, b: number) => (b ? (a - b) / b : 0)
  const causes = (x?.top_causes ?? []).filter((c) => c.excess_defects != null)
  const maxCause = Math.max(1, ...causes.map((c) => c.excess_defects ?? 0))

  return (
    <>
      <PageHead title="경영 현황" sub={x && !x.message ? `${x.equipment.map((e) => e.name).join(', ')} · ${day(x.period.from)} ~ ${day(x.period.to)}${pk ? ` · 비교: 직전 같은 기간 (${day(x.previous!.period.from)} ~ ${day(x.previous!.period.to)})` : ''}` : '기간을 고르면 생산 · CAPA · 불량 · 원인을 한눈에 봅니다'}>
        <div className="seg" role="group" aria-label="기간">
          {PRESETS.map((p) => <button key={p.key} className={preset === p.key ? 'on' : ''} onClick={() => setPreset(p.key)}>{p.label}</button>)}
          <button className={preset === 'custom' ? 'on' : ''} onClick={() => setCustom(from || day(range.data?.from), to || day(range.data?.to))}><Icon name="calendar" size={15} />직접 지정</button>
        </div>
        {preset === 'custom' && (
          <div className="row">
            <input type="date" value={from} max={to || undefined} onChange={(e) => setCustom(e.target.value, to)} aria-label="시작일" />
            <span className="faint">~</span>
            <input type="date" value={to} min={from || undefined} onChange={(e) => setCustom(from, e.target.value)} aria-label="종료일" />
          </div>
        )}
      </PageHead>
      {anchor && preset !== 'custom' && <div className="faint" style={{ fontSize: 12, marginTop: -12, marginBottom: 14 }}>기준일 {iso(anchor)} (가장 최근 생산 기록)</div>}

      <Loading error={d.error ?? range.error} loading={!x && (d.loading || range.loading)} />
      {x?.message && <Panel><Empty>{x.message}</Empty></Panel>}
      {x && k && !x.message && (
        <>
          {k.produced === 0 ? <Panel><Empty>선택한 기간에 생산 기록이 없습니다. 다른 기간을 골라 보세요.</Empty></Panel> : (
            <>
              <div className="kpi-grid">
                <Metric label="생산량" value={fmtNum(k.produced, 0)} unit="개"
                  delta={pk ? { value: pct(k.produced, pk.produced), text: fmtPct(Math.abs(pct(k.produced, pk.produced))), goodWhen: 'up' } : null}
                  hint={`양품 ${fmtNum(k.good, 0)}개`} />
                <Metric label={<>CAPA 가동률<Help title="CAPA 가동률">생산량 ÷ (시간당 생산 능력 × 계획 시간). 정지 시간도 계획 시간에 넣습니다. 시간당 능력: {x.equipment.map((e) => `${e.name} ${fmtNum(e.capacity_per_hour, 0)}개 (${e.capacity_source})`).join(', ')}.</Help></>}
                  value={k.utilization != null ? (k.utilization * 100).toFixed(1) : '-'} unit="%"
                  meter={k.utilization} target={x.targets.utilization ?? null}
                  delta={pk && k.utilization != null && pk.utilization != null ? { value: k.utilization - pk.utilization, text: `${(Math.abs(k.utilization - pk.utilization) * 100).toFixed(1)}%p`, goodWhen: 'up' } : null}
                  hint={x.targets.utilization ? `목표 ${(x.targets.utilization * 100).toFixed(0)}%` : `정지 ${k.stops}회`} />
                <Metric label={<>불량률<Help title="불량률">실무자가 불량 판정으로 확정해 적재한 불량만 셉니다. 판정 대기 중인 의심은 들어가지 않습니다.</Help></>}
                  value={(k.defect_rate * 100).toFixed(2)} unit="%"
                  tone={x.targets.defect_rate && k.defect_rate > x.targets.defect_rate ? 'danger' : 'ok'}
                  delta={pk ? { value: k.defect_rate - pk.defect_rate, text: `${(Math.abs(k.defect_rate - pk.defect_rate) * 100).toFixed(2)}%p`, goodWhen: 'down' } : null}
                  hint={x.targets.defect_rate ? `목표 ${(x.targets.defect_rate * 100).toFixed(1)}% ${k.defect_rate > x.targets.defect_rate ? '초과' : '이내'}` : undefined} />
                <Metric label="불량 수량" value={fmtNum(k.confirmed_defects, 0)} unit="개"
                  delta={pk ? { value: pct(k.confirmed_defects, pk.confirmed_defects), text: fmtPct(Math.abs(pct(k.confirmed_defects, pk.confirmed_defects))), goodWhen: 'down' } : null}
                  hint={k.pending_suspects ? <Link to="/review">판정 대기 {k.pending_suspects}건</Link> : '판정 대기 없음'} />
              </div>

              {x.summary.length > 0 && (
                <div className="insight">
                  <span className="ico"><Icon name="bulb" /></span>
                  <ul>{x.summary.map((s, i) => <li key={i}>{s}</li>)}</ul>
                </div>
              )}

              <div className="grid" style={{ gridTemplateColumns: 'minmax(0, 1.7fr) minmax(0, 1fr)', marginBottom: 16 }}>
                <Panel title={`${x.bucket === 'week' ? '주별' : '일별'} 생산 · CAPA · 불량률`}>{trend ? <EChart option={trend} height={320} /> : <Empty>데이터 없음</Empty>}</Panel>
                <Panel title={<>불량 원인 Top<Help title="초과 불량">그 조건이 범위를 벗어난 시간의 불량에서, 모든 조건이 정상일 때의 불량률로 기대되는 양을 뺀 수입니다. 그 조건 때문에 늘었다고 볼 수 있는 불량입니다.</Help></>}
                  actions={<Link to="/drivers" style={{ fontSize: 12.5 }}>{t('drivers')} →</Link>}>
                  {causes.length ? (
                    <div className="rank">
                      {causes.slice(0, 5).map((c, i) => (
                        <div className="item" key={c.cause}>
                          <span className="no">{i + 1}</span>
                          <span className="name" title={c.cause}>{c.cause}</span>
                          <span className="val">{fmtPct(c.share)}</span>
                          <div className="bar"><span style={{ width: `${((c.excess_defects ?? 0) / maxCause) * 100}%` }} /></div>
                          <span className="sub">초과 불량 {fmtNum(c.excess_defects, 0)}개 · 범위 밖 {fmtPct(c.rate_out)} vs 정상 {fmtPct(c.rate_in)}{c.range ? ` · 기준 ${c.range}` : ''}</span>
                        </div>
                      ))}
                    </div>
                  ) : <Empty>이 기간에 조건 이탈과 연결된 불량이 없습니다.</Empty>}
                </Panel>
              </div>

              <div className="grid" style={{ gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)' }}>
                <Panel title="불량 유형">{donut ? <EChart option={donut} height={240} /> : <Empty>확정 불량 없음</Empty>}</Panel>
                <Panel title="설비별 현황" flush>
                  <table className="t">
                    <thead><tr><th>설비</th><th className="num" style={{ width: 110 }}>생산량</th><th style={{ width: 170 }}>CAPA 가동률</th><th className="num" style={{ width: 100 }}>불량률</th></tr></thead>
                    <tbody>{x.by_equipment.map((e) => (
                      <tr key={e.id}>
                        <td><b>{e.name}</b></td>
                        <td className="num">{fmtNum(e.produced, 0)}</td>
                        <td><div className="row"><div className="meter" style={{ flex: 1 }}><span style={{ width: `${Math.min(100, (e.utilization ?? 0) * 100)}%` }} /></div><span style={{ fontSize: 12.5, width: 44, textAlign: 'right' }}>{fmtPct(e.utilization)}</span></div></td>
                        <td className="num" style={{ color: x.targets.defect_rate && e.rate > x.targets.defect_rate ? 'var(--danger)' : undefined, fontWeight: 600 }}>{(e.rate * 100).toFixed(2)}%</td>
                      </tr>
                    ))}</tbody>
                  </table>
                  <div className="faint" style={{ fontSize: 12, padding: '10px 16px' }}>
                    설비 정지 {k.stops}회 · {fmtNum(k.stop_hours, 1)}시간 · CAPA {fmtNum(k.capa, 0)}개
                  </div>
                </Panel>
              </div>
            </>
          )}
        </>
      )}
    </>
  )
}

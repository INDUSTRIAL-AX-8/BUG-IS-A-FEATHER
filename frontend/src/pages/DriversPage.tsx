import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, errorText } from '../api/client'
import { useAuth } from '../auth'
import { AXIS_STYLE, CHART_FONT, EChart, type EChartsOption } from '../components/EChart'
import { Empty, Level, Loading, PageHead, Panel } from '../components/ui'
import { fmtDateTime, fmtNum, fmtPct } from '../lib/format'
import { useAsync } from '../lib/hooks'
import { useTerms } from '../lib/terms'

interface Target { equipment_id: number; equipment: string; process_step: string | null; runs: number; events: number }
interface Levels { [level: string]: { rows: number; rate: number | null; obs_exp?: number | null } }
interface FactorRow {
  name: string; kind: 'numeric' | 'categorical'; value_kind: string | null; unit: string | null
  p: number; r2: number; odds_ratio_per_sd?: number; levels?: Levels; status?: string
  adjusted_p?: number | null; similar_condition?: { factor: string; range: number[] | null; levels: Levels }
  with?: string; r?: number
}
interface Threshold { factor: string; direction: string; boundary: number; good_side_limit: number; rate_good: number; rate_bad: number; learned_min: number | null; learned_max: number | null }
interface DataRow { time: string; label: string; n: number; k: number; rate: number; numeric: Record<string, number>; categorical: Record<string, string>; top_type: string | null; bad_zone: boolean | null }
interface Result {
  status: string; message?: string
  target: { equipment: string | null }
  summary?: { rows: number; produced: number; defects: number; rate: number; from: string; to: string; overdispersion: number }
  model?: { pseudo_r2: number }
  factors: FactorRow[]
  threshold: Threshold | null
  thresholds?: Threshold[]
  timeline?: { events: { kind: string; start: string; end: string | null; note: string | null; rate_before: number; rate_after: number }[] }
  verdict: { headline: string; confidence: string; primary: string | null; reasons: string[]; recommendations: string[] }
  caveats: string[]
  rows: DataRow[]
}

const STATUS: Record<string, [string, 'danger' | 'warn' | 'ok' | 'neutral']> = {
  PRIMARY: ['주요 원인 후보', 'danger'], SECONDARY: ['보조 요인', 'warn'], INDEPENDENT: ['별도 영향 가능', 'warn'],
  INSEPARABLE: ['구분 불가', 'neutral'], EXPLAINED: ['다른 요인으로 설명됨', 'ok'], NOT_SIGNIFICANT: ['관련 없음', 'ok'],
  SIGNIFICANT_UNADJUSTED: ['단독으로만 관련', 'neutral'],
}

export function DriversPage() {
  const { t } = useTerms()
  const { can } = useAuth()
  const [params, setParams] = useSearchParams()
  const targets = useAsync(() => api.get<Target[]>('/analysis/targets'), [])
  const eqId = Number(params.get('equipment')) || targets.data?.[0]?.equipment_id || null
  const result = useAsync<Result | null>(() => (eqId ? api.get<Result>('/analysis/drivers', { equipment_id: eqId }) : Promise.resolve(null)), [eqId])
  const [msg, setMsg] = useState<string | null>(null)
  const r = result.data

  const apply = async () => {
    try {
      const out = await api.post<{ learned: { name: string; learned_min: number | null; learned_max: number | null; role: string }[] }>(`/analysis/drivers/apply?equipment_id=${eqId}`)
      setMsg(out.learned.length
        ? `학습 범위를 기록했습니다: ${out.learned.map((l) => `${l.name} ${l.learned_min != null ? `≥ ${l.learned_min}` : ''}${l.learned_max != null ? `≤ ${l.learned_max}` : ''} (${l.role})`).join(', ')}. 이제 이 범위를 벗어난 ${t('lot')}은 원인 후보 점수의 '${t('condition')} 이탈'에 반영됩니다.`
        : '기록할 학습 범위가 없습니다 (확신도 부족 또는 현장 허용 범위가 이미 있음).')
    } catch (e) { setMsg(errorText(e)) }
  }

  return (
    <>
      <PageHead title={t('drivers')} sub={`${t('equipment')} 기록 전체에서 불량을 늘린 ${t('condition')}·작업자 요인을 자동으로 찾고, 겉보기 원인(교란)을 걸러 냅니다.`}>
        <select value={eqId ?? ''} onChange={(e) => setParams({ equipment: e.target.value })}>
          {(targets.data ?? []).map((x) => <option key={x.equipment_id} value={x.equipment_id}>{x.equipment} ({x.runs}건)</option>)}
        </select>
        {can('OPERATOR') && <button onClick={apply} disabled={!r || r.status !== 'OK'}>학습 범위 반영</button>}
      </PageHead>
      {msg && <div className="notice" style={{ marginBottom: 12 }}>{msg}</div>}
      <Loading error={result.error || targets.error} loading={result.loading && !r} />
      {targets.data && !targets.data.length && <Panel><Empty>분석할 {t('equipment')} 기록이 없습니다. 데이터 업로드 또는 공정 기록 가져오기를 먼저 하세요.</Empty></Panel>}
      {r && r.status !== 'OK' && <Panel><Empty>{r.message}</Empty></Panel>}
      {r && r.status === 'OK' && <ResultView r={r} />}
    </>
  )
}

function ResultView({ r }: { r: Result }) {
  const { t } = useTerms()
  const conf = r.verdict.confidence
  const lv = conf === '높음' ? 'danger' : conf === '보통' ? 'warn' : 'neutral'
  return (
    <div className="stack">
      <Panel title="결론">
        <div className="stack" style={{ gap: 10 }}>
          <div className="row wrap" style={{ gap: 10 }}>
            <Level level={lv}>확신도 {conf}</Level>
            <b style={{ fontSize: 16 }}>{r.verdict.headline}</b>
          </div>
          <div className="muted" style={{ fontSize: 13 }}>
            {fmtDateTime(r.summary?.from)} ~ {fmtDateTime(r.summary?.to)} · 기록 {r.summary?.rows}건 · 생산 {fmtNum(r.summary?.produced, 0)} · 불량 {fmtNum(r.summary?.defects, 0)} ({fmtPct(r.summary?.rate)})
          </div>
          <div className="grid cols-2" style={{ alignItems: 'start' }}>
            <div>
              <h3 style={{ marginBottom: 6 }}>판단 근거</h3>
              <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.7 }}>{r.verdict.reasons.map((x, i) => <li key={i}>{x}</li>)}</ul>
            </div>
            <div>
              <h3 style={{ marginBottom: 6 }}>권고</h3>
              <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.7 }}>{r.verdict.recommendations.map((x, i) => <li key={i}>{x}</li>)}</ul>
            </div>
          </div>
        </div>
      </Panel>
      <div className="grid cols-2">
        <Panel title={`시간별 불량률과 ${r.verdict.primary ?? '주요인'}`}><TimelineChart r={r} /></Panel>
        <Panel title={`불량률 vs ${r.verdict.primary ?? '주요인'}`}><ScatterChart r={r} /></Panel>
      </div>
      <Panel title="요인별 판정" flush>
        <table className="t">
          <thead><tr><th style={{ width: 150 }}>요인</th><th style={{ width: 70 }}>값 종류</th><th style={{ width: 150 }}>판정</th><th style={{ width: 120 }}>단독 효과</th><th>같은 조건 비교 / 수준별 불량률</th></tr></thead>
          <tbody>
            {r.factors.map((f) => {
              const [label, level] = STATUS[f.status ?? ''] ?? ['-', 'neutral']
              const sc = f.similar_condition
              return (
                <tr key={f.name} style={{ height: 'auto' }}>
                  <td>{f.name}</td>
                  <td>{f.value_kind === 'measured' ? '측정값' : f.value_kind === 'setpoint' ? '설정값' : f.kind === 'categorical' ? '범주' : '-'}</td>
                  <td><Level level={level}>{label}</Level></td>
                  <td>{f.kind === 'numeric' ? `1σ당 ×${fmtNum(f.odds_ratio_per_sd, 2)}` : '수준 비교'} <span className="faint">p {f.p < 0.001 ? '<0.001' : fmtNum(f.p, 3)}</span></td>
                  <td style={{ whiteSpace: 'normal', padding: '6px 10px', fontSize: 12.5 }}>
                    {sc ? <>같은 {sc.factor}{sc.range ? ` (${fmtNum(sc.range[0], 1)}~${fmtNum(sc.range[1], 1)})` : ''}: {Object.entries(sc.levels).filter(([, v]) => v.rows).map(([k, v]) => `${k} ${fmtPct(v.rate)}${v.obs_exp != null ? ` (예상 대비 ${v.obs_exp.toFixed(2)}배)` : ''}`).join(' · ')}</>
                      : f.levels ? Object.entries(f.levels).map(([k, v]) => `${k} ${fmtPct(v.rate)} (${v.rows}건)`).join(' · ')
                      : f.status === 'INSEPARABLE' ? `${f.with} 와 r=${fmtNum(f.r, 2)}` : ''}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </Panel>
      <div className="grid cols-2">
        <Panel title="학습된 관리 경계">
          {(r.thresholds ?? []).length ? (
            <table className="t">
              <thead><tr><th>요인</th><th>경계</th><th className="num">나쁜 구간 불량률</th><th className="num">그 밖</th></tr></thead>
              <tbody>{(r.thresholds ?? []).map((x) => (
                <tr key={x.factor}><td>{x.factor}</td><td>{x.learned_max != null ? `≤ ${x.learned_max}` : `≥ ${x.learned_min}`}</td>
                  <td className="num">{fmtPct(x.rate_bad)}</td><td className="num">{fmtPct(x.rate_good)}</td></tr>
              ))}</tbody>
            </table>
          ) : <Empty>유의한 연속 요인이 없어 경계를 학습하지 않았습니다.</Empty>}
          <div className="faint" style={{ fontSize: 12, marginTop: 6 }}>"학습 범위 반영"을 누르면 현장 허용 범위가 없는 {t('condition')} 항목에 기록되고, 원인 후보 점수와 {t('genealogyGraph')}에서 이탈로 표시됩니다 (감사 로그).</div>
        </Panel>
        <Panel title="데이터 주의사항">
          <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.7, fontSize: 13 }}>{r.caveats.map((c, i) => <li key={i}>{c}</li>)}</ul>
        </Panel>
      </div>
      <Panel title={`기록 (${r.rows.length}건)`} flush>
        <div className="table-wrap" style={{ maxHeight: 360 }}>
          <table className="t">
            <thead><tr><th style={{ width: 130 }}>시각</th><th>{t('lot')}</th>{Object.keys(r.rows[0]?.categorical ?? {}).map((c) => <th key={c}>{c}</th>)}
              {Object.keys(r.rows[0]?.numeric ?? {}).map((c) => <th key={c} className="num">{c}</th>)}<th className="num">생산</th><th className="num">불량</th><th className="num">불량률</th><th>최다 유형</th></tr></thead>
            <tbody>{r.rows.map((x) => (
              <tr key={x.time + x.label} style={x.bad_zone ? { background: 'var(--danger-soft)' } : undefined}>
                <td>{fmtDateTime(x.time)}</td>
                <td><Link to={`/genealogy?lot=${encodeURIComponent(x.label)}`}>{x.label}</Link></td>
                {Object.values(x.categorical).map((v, i) => <td key={i}>{v}</td>)}
                {Object.keys(r.rows[0].numeric).map((c) => <td key={c} className="num">{fmtNum(x.numeric[c], 1)}</td>)}
                <td className="num">{fmtNum(x.n, 0)}</td><td className="num">{fmtNum(x.k, 0)}</td><td className="num">{fmtPct(x.rate)}</td><td>{x.top_type ?? '-'}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
        <div className="faint" style={{ fontSize: 12, padding: 8 }}>붉은 행 = 주요인이 학습 경계를 넘은 구간</div>
      </Panel>
    </div>
  )
}

function TimelineChart({ r }: { r: Result }) {
  const opt = useMemo<EChartsOption | null>(() => {
    if (!r.rows.length) return null
    const x = r.rows.map((d) => fmtDateTime(d.time))
    const p = r.verdict.primary
    const th = r.threshold
    const events = r.timeline?.events ?? []
    return {
      animation: false,
      grid: { left: 50, right: 56, top: 30, bottom: 30 },
      legend: { top: 0, textStyle: { fontFamily: CHART_FONT, fontSize: 11 } },
      tooltip: { trigger: 'axis', textStyle: { fontFamily: CHART_FONT, fontSize: 12 } },
      xAxis: { type: 'category', data: x, ...AXIS_STYLE, axisLabel: { ...AXIS_STYLE.axisLabel, hideOverlap: true } },
      yAxis: [
        { type: 'value', name: '불량률 %', nameTextStyle: { fontSize: 11, color: '#5a6370' }, ...AXIS_STYLE },
        { type: 'value', name: p ?? '', scale: true, nameTextStyle: { fontSize: 11, color: '#5a6370' }, ...AXIS_STYLE, splitLine: { show: false } },
      ],
      series: [
        { name: '불량률', type: 'bar', data: r.rows.map((d) => +(d.rate * 100).toFixed(2)), itemStyle: { color: '#a9c1e2' },
          markArea: events.length ? { silent: true, itemStyle: { color: 'rgba(154,98,0,0.12)' }, label: { fontSize: 11, color: '#9a6200' },
            data: events.map((e) => { const i = r.rows.findIndex((d) => d.time >= (e.end ?? e.start)); const j = Math.max(0, i - 1); return [{ name: e.kind === 'STOP' ? '정지' : e.kind, xAxis: x[j] }, { xAxis: x[Math.max(i, 0)] }] }) } : undefined },
        ...(p ? [{ name: p, type: 'line', yAxisIndex: 1, data: r.rows.map((d) => d.numeric[p]), symbolSize: 5, lineStyle: { color: '#1d4580', width: 2 }, itemStyle: { color: '#1d4580' },
          markLine: th ? { symbol: 'none', silent: true, label: { formatter: '학습 경계', fontSize: 11 }, lineStyle: { color: '#a8261d', type: 'dashed' }, data: [{ yAxis: th.good_side_limit }] } : undefined }] : []),
      ],
    }
  }, [r])
  return opt ? <EChart option={opt} height={300} /> : <Empty>데이터 없음</Empty>
}

function ScatterChart({ r }: { r: Result }) {
  const opt = useMemo<EChartsOption | null>(() => {
    const p = r.verdict.primary
    if (!p || !r.rows.length || r.rows[0].numeric[p] === undefined) return null
    const cats = r.rows.map((d) => Object.values(d.categorical)[0] ?? '전체')
    const levels = Array.from(new Set(cats))
    const colors = ['#24508f', '#a8261d', '#2c6e49', '#9a6200']
    const symbols = ['circle', 'triangle', 'rect', 'diamond']
    return {
      animation: false,
      grid: { left: 50, right: 20, top: 30, bottom: 36 },
      legend: { top: 0, textStyle: { fontFamily: CHART_FONT, fontSize: 11 } },
      tooltip: { trigger: 'item', textStyle: { fontFamily: CHART_FONT, fontSize: 12 },
        formatter: (q: { data: number[]; seriesName: string }) => `${q.seriesName}<br/>${p} ${q.data[0]}<br/>불량률 ${q.data[1]}%` },
      xAxis: { type: 'value', name: p, scale: true, nameLocation: 'middle', nameGap: 24, nameTextStyle: { fontSize: 11 }, ...AXIS_STYLE },
      yAxis: { type: 'value', name: '불량률 %', nameTextStyle: { fontSize: 11 }, ...AXIS_STYLE },
      series: levels.map((lv, i) => ({
        name: lv, type: 'scatter', symbol: symbols[i % 4], symbolSize: 10, itemStyle: { color: colors[i % 4] },
        data: r.rows.filter((_, j) => cats[j] === lv).map((d) => [d.numeric[p], +(d.rate * 100).toFixed(2)]),
      })),
    }
  }, [r])
  return opt ? (
    <>
      <EChart option={opt} height={300} />
      <div className="faint" style={{ fontSize: 12 }}>모양·색 = {Object.keys(r.rows[0]?.categorical ?? {})[0] ?? '구분'}. 같은 {r.verdict.primary} 값에서 모양별 불량률이 겹치면 그 범주 요인은 원인이 아닙니다.</div>
    </>
  ) : <Empty>연속 주요인이 없습니다.</Empty>
}

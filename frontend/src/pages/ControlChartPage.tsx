import { useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api, errorText } from '../api/client'
import type { Characteristic, ChartData, SpcCharSummary } from '../api/types'
import { useAuth } from '../auth'
import { AXIS_STYLE, CHART_FONT, EChart, type EChartsOption } from '../components/EChart'
import { Empty, Field, Loading, PageHead, Panel, RuleBadge } from '../components/ui'
import { CHART_LABEL, fmtDateTime, fmtNum } from '../lib/format'
import { useAsync } from '../lib/hooks'
import { useLiveRefresh } from '../lib/live'
import { Help } from '../components/Help'

const DISPERSION_LABEL: Record<string, string> = { IMR: 'MR', XBAR_R: 'R', XBAR_S: 'S' }

export function ControlChartPage() {
  const [params, setParams] = useSearchParams()
  const selected = Number(params.get('char')) || null
  const list = useAsync(() => api.get<SpcCharSummary[]>('/spc/characteristics'), [])
  const [filter, setFilter] = useState('')

  const rows = (list.data ?? []).filter(
    (c) => !filter || `${c.owner ?? ''} ${c.name}`.toLowerCase().includes(filter.toLowerCase()),
  )

  return (
    <>
      <PageHead title="관리도" sub="넬슨룰 위반 포인트는 룰 번호와 함께 표시됩니다. 포인트를 누르면 해당 LOT 계보로 이동합니다." />
      <div className="split">
        <Panel title="측정 특성" flush>
          <div style={{ padding: 8 }}>
            <input placeholder="특성/부품 검색" value={filter} onChange={(e) => setFilter(e.target.value)} style={{ width: '100%' }} />
          </div>
          <Loading error={list.error} loading={list.loading && !list.data} />
          <div className="table-wrap" style={{ maxHeight: 'calc(100vh - 210px)' }}>
            <table className="t">
              <thead>
                <tr><th>부품/공정 · 특성</th><th style={{ width: 64 }}>관리도</th><th className="num" style={{ width: 56 }} title="미확인 이상 사건 수 (같은 이상에 함께 걸린 위반은 1건)">미확인</th></tr>
              </thead>
              <tbody>
                {rows.map((c) => (
                  <tr key={c.id} className={`clickable ${c.id === selected ? 'selected' : ''}`}
                    onClick={() => setParams({ char: String(c.id) })}>
                    <td title={`${c.owner} · ${c.name}`}><span className="muted">{c.owner}</span> · {c.name}</td>
                    <td>{CHART_LABEL[c.chart_type]}</td>
                    <td className="num" title={`위반 ${c.open_violations}건`}>{c.open_events > 0 ? <b style={{ color: 'var(--danger)' }}>{c.open_events}</b> : 0}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
        {selected ? <ChartView charId={selected} onChanged={list.reload} /> : <Panel><Empty>왼쪽에서 측정 특성을 선택하세요.</Empty></Panel>}
      </div>
    </>
  )
}

function ChartView({ charId, onChanged }: { charId: number; onChanged: () => void }) {
  const nav = useNavigate()
  const { can } = useAuth()
  const chart = useAsync(() => api.get<ChartData>(`/spc/${charId}/chart`), [charId])
  useLiveRefresh(chart.reload, 1000)
  const [visible, setVisible] = useRuleView()
  const raw = chart.data
  // 실무자 보기 필터: 체크한 룰의 위반만 표시 (판정·저장은 그대로)
  const d = useMemo(() => raw && {
    ...raw,
    points: raw.points.map((p) => ({ ...p, rules: p.rules.filter((x) => visible.has(x)) })),
    violations: raw.violations.filter((v) => visible.has(v.rule_no)),
    // 사건은 볼 룰이 하나라도 걸린 것만, 라벨도 볼 룰만
    events: raw.events
      .map((e) => ({ ...e, rule_nos: e.rule_nos.filter((r) => visible.has(r)) }))
      .filter((e) => e.rule_nos.length > 0),
  }, [raw, visible])

  const options = useMemo(() => (d ? buildOptions(d) : null), [d])

  const openLot = (dataIndex: number) => {
    const p = d?.points[dataIndex]
    const lot = p?.lot_nos.find(Boolean)
    if (lot) nav(`/genealogy?lot=${encodeURIComponent(lot)}`)
  }

  if (!d) return <Panel><Loading error={chart.error} loading={chart.loading} /></Panel>
  const c = d.characteristic

  return (
    <div className="stack">
      <Panel
        title={<>{c.name} <span className="muted" style={{ fontWeight: 400 }}>({c.unit ?? '-'}) · {CHART_LABEL[d.chart_type]} · n={c.subgroup_size}</span></>}
        actions={<button className="small" onClick={chart.reload}>새로고침</button>}
      >
        <div className="row wrap" style={{ marginBottom: 8, gap: 16, fontSize: 13 }}>
          {d.baseline_scope === 'PARENT_LOT' && <span className="muted">마지막 체제 기준 →</span>}
          <span>CL <b>{fmtNum(d.limits?.center)}</b></span><Help topic="controlLimit" />
          <span>UCL <b>{fmtNum(d.limits?.ucl)}</b></span>
          <span>LCL <b>{fmtNum(d.limits?.lcl)}</b></span>
          <span className="muted">USL {fmtNum(c.usl)} · LSL {fmtNum(c.lsl)} · Target {fmtNum(c.target)}</span>
          <span className="right">
            {d.baseline_scope === 'PARENT_LOT'
              ? <span className="badge neutral">· 상위 LOT(체제)별 기준선<Help topic="regime" /></span>
              : d.provisional
              ? <span className="badge warn">■ 임시 한계<Help topic="provisional" /></span>
              : <span className="badge ok">● 고정 한계 v{d.limits?.version} ({fmtDateTime(d.limits?.baseline_from)} ~ {fmtDateTime(d.limits?.baseline_to)})</span>}
          </span>
        </div>
        {d.message && <div className="notice warn" style={{ marginBottom: 8 }}>{d.message}</div>}
        <RuleViewBar visible={visible} setVisible={setVisible} rules={d.rules} hidden={(raw?.violations.length ?? 0) - d.violations.length} />
        {options ? (
          <>
            <EChart option={options.main} height={330} onClick={(p) => openLot(p.dataIndex)} group={`spc-${charId}`} />
            <EChart option={options.disp} height={150} group={`spc-${charId}`} />
          </>
        ) : <Empty>표시할 데이터가 없습니다.</Empty>}
      </Panel>

      <div className="grid cols-2">
        <Panel title={<>이상 사건 ({d.events.length}){d.events.length !== raw?.events.length && <span className="faint" style={{ fontWeight: 400 }}> / 전체 {raw?.events.length}</span>}
          <span className="faint" style={{ fontWeight: 400 }}> · 위반 {d.violations.length}건</span><Help topic="spcEvent" /></>} flush>
          <div className="table-wrap" style={{ maxHeight: 300 }}>
            <table className="t">
              <thead><tr><th>걸린 룰</th><th style={{ width: 76 }}>방향</th><th style={{ width: 150 }}>발생 시각</th><th className="num" style={{ width: 60 }}>점 수</th></tr></thead>
              <tbody>
                {[...d.events].reverse().map((e) => (
                  <tr key={e.signal_index} className="clickable" onClick={() => openLot(e.last_index)}
                    title={e.violations.map((i) => raw?.violations[i]).filter((v) => v && visible.has(v.rule_no)).map((v) => `R${v!.rule_no} ${v!.description}`).join('\n')}>
                    <td style={{ whiteSpace: 'normal' }}>{e.rule_nos.map((r) => <RuleBadge key={r} rule={r} />)}</td>
                    <td>{e.direction > 0 ? '▲ 상승' : e.direction < 0 ? '▼ 하락' : '↕ 양쪽'}</td>
                    <td>{fmtDateTime(e.occurred_at)}</td>
                    <td className="num">{e.last_index - e.first_index + 1}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {d.events.length === 0 && <Empty>위반이 없습니다.</Empty>}
          </div>
        </Panel>
        <div className="stack">
          {can('ADMIN') && d.baseline_scope === 'GLOBAL' && <BaselineForm charId={charId} onDone={() => { chart.reload(); onChanged() }} />}
          <RulePanel key={charId} charId={charId} rules={d.rules} editable={can('ADMIN')} onDone={() => { chart.reload(); onChanged() }} />
        </div>
      </div>
    </div>
  )
}

const RULE_KEY = 'bif_rule_view'

/** 실무자가 끄고 켜는 '볼 넬슨룰' — 이 브라우저에 기억한다. */
function useRuleView(): [Set<number>, (s: Set<number>) => void] {
  const [v, setV] = useState<Set<number>>(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(RULE_KEY) ?? 'null')
      if (Array.isArray(saved)) return new Set(saved.map(Number))
    } catch { /* 저장소 사용 불가 */ }
    return new Set([1, 2, 3, 4, 5, 6, 7, 8])
  })
  const set = (s: Set<number>) => {
    setV(s)
    try { localStorage.setItem(RULE_KEY, JSON.stringify([...s])) } catch { /* 무시 */ }
  }
  return [v, set]
}

function RuleViewBar(props: { visible: Set<number>; setVisible: (s: Set<number>) => void; rules: ChartData['rules']; hidden: number }) {
  const { visible, setVisible } = props
  const enabled = new Map(props.rules.map((r) => [r.rule_no, r]))
  const preset = (n: number[]) => setVisible(new Set(n))
  return (
    <div className="row wrap" style={{ gap: 10, marginBottom: 8, padding: '6px 8px', background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 3 }}>
      <span style={{ fontSize: 12.5, fontWeight: 600 }}>볼 넬슨룰<Help topic="nelson" /></span>
      {[1, 2, 3, 4, 5, 6, 7, 8].map((n) => {
        const rule = enabled.get(n)
        const off = rule && !rule.enabled
        return (
          <label key={n} className="check" style={{ fontSize: 12.5, opacity: off ? 0.45 : 1 }} title={off ? '이 특성에서는 관리자가 이 룰을 꺼 두었습니다' : rule?.description}>
            <input type="checkbox" checked={visible.has(n)} disabled={off} onChange={(e) => {
              const s = new Set(visible)
              if (e.target.checked) s.add(n)
              else s.delete(n)
              setVisible(s)
            }} />R{n}<Help topic={`rule${n}`} />
          </label>
        )
      })}
      <span className="row" style={{ gap: 4 }}>
        <button className="small" onClick={() => preset([1])}>R1만</button>
        <button className="small" onClick={() => preset([1, 2, 3, 4])}>R1~R4</button>
        <button className="small" onClick={() => preset([1, 2, 3, 4, 5, 6, 7, 8])}>전체</button>
      </span>
      {props.hidden > 0 && <span className="faint" style={{ fontSize: 12 }}>숨긴 위반 {props.hidden}건</span>}
    </div>
  )
}

function buildOptions(d: ChartData): { main: EChartsOption; disp: EChartsOption } | null {
  if (!d.points.length) return null
  const x = d.points.map((p) => fmtDateTime(p.time))
  const c = d.characteristic
  const lim = d.limits
  const perRegime = d.baseline_scope === 'PARENT_LOT'
  const specLines: object[] = []
  if (c.usl !== null) specLines.push({ yAxis: c.usl, name: 'USL', lineStyle: { color: '#a8261d', type: 'dashed' }, label: { formatter: 'USL', color: '#a8261d' } })
  if (c.lsl !== null) specLines.push({ yAxis: c.lsl, name: 'LSL', lineStyle: { color: '#a8261d', type: 'dashed' }, label: { formatter: 'LSL', color: '#a8261d' } })

  const values = d.points.map((p) => p.value)
  const allY = [...values, ...d.points.map((p) => p.ucl), ...d.points.map((p) => p.lcl), c.usl ?? values[0], c.lsl ?? values[0]]
  const pad = (Math.max(...allY) - Math.min(...allY)) * 0.08 || 1

  const tooltip = {
    trigger: 'axis',
    textStyle: { fontFamily: CHART_FONT, fontSize: 12 },
    formatter: (items: { dataIndex: number }[]) => {
      const p = d.points[items[0].dataIndex]
      const lots = p.lot_nos.filter(Boolean)
      const lotText = lots.length > 4 ? `${lots.slice(0, 4).join(', ')} 외 ${lots.length - 4}` : lots.join(', ')
      const rules = p.rules.length ? `<br/><b style="color:#a8261d">위반: ${p.rules.map((r) => 'R' + r).join(', ')}</b>` : ''
      return `${fmtDateTime(p.time)}<br/>값 ${fmtNum(p.value, 4)}${p.values.length > 1 ? ` (n=${p.values.length})` : ''}<br/>LOT ${lotText || '-'}${rules}`
    },
  }

  const main: EChartsOption = {
    animation: false,
    grid: { left: 64, right: 56, top: 16, bottom: 28 },
    tooltip,
    xAxis: { type: 'category', data: x, ...AXIS_STYLE, axisLabel: { ...AXIS_STYLE.axisLabel, hideOverlap: true } },
    yAxis: {
      type: 'value', scale: true, min: Math.min(...allY) - pad, max: Math.max(...allY) + pad, ...AXIS_STYLE,
      axisLabel: { ...AXIS_STYLE.axisLabel, formatter: (v: number) => fmtNum(v, 3), showMinLabel: false, showMaxLabel: false },
    },
    dataZoom: [{ type: 'inside' }],
    series: [
      {
        name: '값', type: 'line', data: values, symbol: 'circle', symbolSize: 5,
        lineStyle: { color: '#4b5563', width: 1 }, itemStyle: { color: '#4b5563' },
        markLine: {
          symbol: 'none', silent: true,
          label: { fontFamily: CHART_FONT, fontSize: 11, position: 'end' },
          data: lim && !perRegime ? [
            { yAxis: lim.center, lineStyle: { color: '#2c6e49' }, label: { formatter: 'CL' } },
            ...specLines,
          ] : specLines,
        },
      },
      ...(perRegime ? [{ name: 'CL', type: 'line', step: 'middle', data: d.points.map((p) => p.center), symbol: 'none', lineStyle: { color: '#2c6e49', width: 1 }, silent: true }] : []),
      { name: 'UCL', type: 'line', step: 'middle', data: d.points.map((p) => p.ucl), symbol: 'none', lineStyle: { color: '#24508f', width: 1 }, silent: true },
      { name: 'LCL', type: 'line', step: 'middle', data: d.points.map((p) => p.lcl), symbol: 'none', lineStyle: { color: '#24508f', width: 1 }, silent: true },
      {
        name: '위반', type: 'scatter', symbol: 'diamond', symbolSize: 11, z: 5,
        data: d.points.map((p) => (p.rules.length ? p.value : null)),
        itemStyle: { color: '#a8261d', borderColor: '#fff', borderWidth: 1 },
        label: {
          show: true, position: 'top', fontSize: 10, color: '#a8261d', fontFamily: CHART_FONT,
          formatter: (q: { dataIndex: number }) => d.points[q.dataIndex].rules.join(','),
        },
      },
    ],
  }

  const dispData = d.points.map((p) => p.dispersion)
  const disp: EChartsOption = {
    animation: false,
    grid: { left: 64, right: 56, top: 14, bottom: 24 },
    tooltip: { trigger: 'axis', textStyle: { fontFamily: CHART_FONT, fontSize: 12 } },
    xAxis: { type: 'category', data: x, ...AXIS_STYLE, axisLabel: { show: false } },
    yAxis: { type: 'value', scale: true, name: DISPERSION_LABEL[d.chart_type], nameTextStyle: { color: '#5a6370', fontSize: 11 }, ...AXIS_STYLE },
    dataZoom: [{ type: 'inside' }],
    series: [{
      name: DISPERSION_LABEL[d.chart_type], type: 'line', data: dispData, symbol: 'circle', symbolSize: 4,
      lineStyle: { color: '#6b7280', width: 1 }, itemStyle: { color: '#6b7280' },
      markLine: lim?.dispersion_ucl != null ? {
        symbol: 'none', silent: true, label: { fontSize: 11, position: 'end' },
        data: [
          { yAxis: lim.dispersion_center, lineStyle: { color: '#2c6e49' }, label: { formatter: 'CL' } },
          { yAxis: lim.dispersion_ucl, lineStyle: { color: '#24508f' }, label: { formatter: 'UCL' } },
        ],
      } : undefined,
    }],
  }
  return { main, disp }
}

function BaselineForm({ charId, onDone }: { charId: number; onDone: () => void }) {
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [msg, setMsg] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const submit = async () => {
    if (!from || !to) return setMsg('기준 구간 시작과 끝을 입력하세요.')
    if (!confirm('이 기준 구간으로 관리한계를 다시 계산해 고정합니다. 기록이 남습니다. 계속할까요?')) return
    setBusy(true)
    try {
      const r = await api.post<{ version: number }>(`/spc/${charId}/limits`, { baseline_from: from, baseline_to: to })
      setMsg(`관리한계 v${r.version} 를 고정했습니다.`)
      onDone()
    } catch (e) {
      setMsg(errorText(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Panel title="기준 구간으로 관리한계 고정">
      <div className="form-row">
        <Field label="시작"><input type="datetime-local" value={from} onChange={(e) => setFrom(e.target.value)} /></Field>
        <Field label="끝"><input type="datetime-local" value={to} onChange={(e) => setTo(e.target.value)} /></Field>
        <button className="primary" disabled={busy} onClick={submit}>계산 후 고정</button>
      </div>
      {msg && <div className="notice" style={{ marginTop: 8, whiteSpace: 'pre-wrap' }}>{msg}</div>}
    </Panel>
  )
}

function RulePanel(props: { charId: number; rules: ChartData['rules']; editable: boolean; onDone: () => void }) {
  const [draft, setDraft] = useState(() => props.rules.map((r) => ({ ...r, params: { ...r.params } })))
  const [msg, setMsg] = useState<string | null>(null)
  const save = async () => {
    try {
      const char = await api.get<Characteristic>(`/characteristics/${props.charId}`)
      const rule_config = Object.fromEntries(draft.map((r) => [String(r.rule_no), { enabled: r.enabled, params: r.params }]))
      await api.put(`/characteristics/${props.charId}`, { ...char, rule_config })
      setMsg('룰 설정을 저장했습니다.')
      props.onDone()
    } catch (e) {
      setMsg(errorText(e))
    }
  }
  return (
    <Panel title="넬슨룰 설정 (이 특성)" actions={props.editable ? <button className="small primary" onClick={save}>저장</button> : <span className="faint">관리자만 변경</span>}>
      <table className="t">
        <tbody>
          {draft.map((r, i) => (
            <tr key={r.rule_no}>
              <td style={{ width: 70 }}>
                <label className="check">
                  <input type="checkbox" disabled={!props.editable} checked={r.enabled}
                    onChange={(e) => setDraft(draft.map((x, j) => (j === i ? { ...x, enabled: e.target.checked } : x)))} />
                  R{r.rule_no}
                </label>
              </td>
              <td title={r.description} className={r.enabled ? '' : 'faint'}>{r.description}</td>
              <td style={{ width: 170 }}>
                <div className="row">
                  {Object.entries(r.params).map(([k, v]) => (
                    <input key={k} title={k} type="number" step="any" disabled={!props.editable} value={v} style={{ width: 52, height: 24 }}
                      onChange={(e) => setDraft(draft.map((x, j) => (j === i ? { ...x, params: { ...x.params, [k]: Number(e.target.value) } } : x)))} />
                  ))}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {msg && <div className="notice" style={{ marginTop: 8, whiteSpace: 'pre-wrap' }}>{msg}</div>}
    </Panel>
  )
}

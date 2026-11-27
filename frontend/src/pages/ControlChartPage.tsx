import { useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api, errorText } from '../api/client'
import type { Characteristic, ChartData, SpcCharSummary } from '../api/types'
import { useAuth } from '../auth'
import { AXIS_STYLE, CHART_FONT, EChart, type EChartsOption } from '../components/EChart'
import { Empty, Field, Loading, PageHead, Panel, RuleBadge } from '../components/ui'
import { CHART_LABEL, fmtDateTime, fmtNum } from '../lib/format'
import { useAsync } from '../lib/hooks'

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
                <tr><th>부품/공정 · 특성</th><th style={{ width: 64 }}>관리도</th><th className="num" style={{ width: 56 }}>미확인</th></tr>
              </thead>
              <tbody>
                {rows.map((c) => (
                  <tr key={c.id} className={`clickable ${c.id === selected ? 'selected' : ''}`}
                    onClick={() => setParams({ char: String(c.id) })}>
                    <td title={`${c.owner} · ${c.name}`}><span className="muted">{c.owner}</span> · {c.name}</td>
                    <td>{CHART_LABEL[c.chart_type]}</td>
                    <td className="num">{c.open_violations > 0 ? <b style={{ color: 'var(--danger)' }}>{c.open_violations}</b> : 0}</td>
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
  const d = chart.data

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
          <span>CL <b>{fmtNum(d.limits?.center)}</b></span>
          <span>UCL <b>{fmtNum(d.limits?.ucl)}</b></span>
          <span>LCL <b>{fmtNum(d.limits?.lcl)}</b></span>
          <span className="muted">USL {fmtNum(c.usl)} · LSL {fmtNum(c.lsl)} · Target {fmtNum(c.target)}</span>
          <span className="right">
            {d.provisional
              ? <span className="badge warn">■ 임시 한계</span>
              : <span className="badge ok">● 고정 한계 v{d.limits?.version} ({fmtDateTime(d.limits?.baseline_from)} ~ {fmtDateTime(d.limits?.baseline_to)})</span>}
          </span>
        </div>
        {d.message && <div className="notice warn" style={{ marginBottom: 8 }}>{d.message}</div>}
        {options ? (
          <>
            <EChart option={options.main} height={330} onClick={(p) => openLot(p.dataIndex)} group={`spc-${charId}`} />
            <EChart option={options.disp} height={150} group={`spc-${charId}`} />
          </>
        ) : <Empty>표시할 데이터가 없습니다.</Empty>}
      </Panel>

      <div className="grid cols-2">
        <Panel title={`위반 (${d.violations.length})`} flush>
          <div className="table-wrap" style={{ maxHeight: 300 }}>
            <table className="t">
              <thead><tr><th style={{ width: 56 }}>룰</th><th>내용</th><th style={{ width: 150 }}>발생 시각</th><th className="num" style={{ width: 60 }}>점 수</th></tr></thead>
              <tbody>
                {d.violations.map((v, i) => (
                  <tr key={i} className="clickable" onClick={() => openLot(v.last_index)}>
                    <td><RuleBadge rule={v.rule_no} /></td>
                    <td title={v.description}>{v.description}</td>
                    <td>{fmtDateTime(v.occurred_at)}</td>
                    <td className="num">{v.points.length}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {d.violations.length === 0 && <Empty>위반이 없습니다.</Empty>}
          </div>
        </Panel>
        <div className="stack">
          {can('ADMIN') && <BaselineForm charId={charId} onDone={() => { chart.reload(); onChanged() }} />}
          <RulePanel key={charId} charId={charId} rules={d.rules} editable={can('ADMIN')} onDone={() => { chart.reload(); onChanged() }} />
        </div>
      </div>
    </div>
  )
}

function buildOptions(d: ChartData): { main: EChartsOption; disp: EChartsOption } | null {
  if (!d.points.length) return null
  const x = d.points.map((p) => fmtDateTime(p.time))
  const c = d.characteristic
  const lim = d.limits
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
    yAxis: { type: 'value', scale: true, min: Math.min(...allY) - pad, max: Math.max(...allY) + pad, ...AXIS_STYLE },
    dataZoom: [{ type: 'inside' }],
    series: [
      {
        name: '값', type: 'line', data: values, symbol: 'circle', symbolSize: 5,
        lineStyle: { color: '#4b5563', width: 1 }, itemStyle: { color: '#4b5563' },
        markLine: {
          symbol: 'none', silent: true,
          label: { fontFamily: CHART_FONT, fontSize: 11, position: 'end' },
          data: lim ? [
            { yAxis: lim.center, lineStyle: { color: '#2c6e49' }, label: { formatter: 'CL' } },
            ...specLines,
          ] : specLines,
        },
      },
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

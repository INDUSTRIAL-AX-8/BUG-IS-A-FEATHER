import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { Candidate, CauseAnalysis, Downstream, GraphNode, Judgment, NodeKind } from '../api/causes'
import { api, errorText } from '../api/client'
import { useAuth } from '../auth'
import { CauseGraph, GraphLegend } from '../components/CauseGraph'
import { MiniChart } from '../components/MiniChart'
import { ScoreDetail } from '../components/ScoreDetail'
import { Empty, Field, Loading, PageHead, Panel, ScoreBar, StatusBadge } from '../components/ui'
import { CATEGORY_LABEL, fmtDateTime, NODE_LABEL } from '../lib/format'
import { useAsync } from '../lib/hooks'

const ALL_TYPES: NodeKind[] = ['LOT', 'STEP', 'EQUIPMENT', 'CONDITION']
const TOP_N = 5

export function GenealogyPage() {
  const [params, setParams] = useSearchParams()
  const lotNo = params.get('lot') ?? ''
  const [input, setInput] = useState(lotNo)
  const [depth, setDepth] = useState(Number(params.get('depth')) || 6)
  const [types, setTypes] = useState<Set<NodeKind>>(new Set(ALL_TYPES))
  const [hideNormalConds, setHideNormalConds] = useState(true)
  const [selected, setSelected] = useState<string | null>(null)
  const [downNode, setDownNode] = useState<GraphNode | null>(null)
  const direction = downNode ? 'down' : 'up'

  useEffect(() => setInput(lotNo), [lotNo])
  useEffect(() => { setSelected(null); setDownNode(null) }, [lotNo])

  const analysis = useAsync<CauseAnalysis | null>(
    () => (lotNo ? api.get<CauseAnalysis>(`/causes/${encodeURIComponent(lotNo)}`, { depth }) : Promise.resolve(null)),
    [lotNo, depth],
  )
  const a = analysis.data
  const candByNode = useMemo(() => new Map((a?.candidates ?? []).map((c) => [c.node, c])), [a])

  const shown = useMemo(() => {
    if (!a) return { nodes: [], edges: [] }
    const nodes = a.graph.nodes.filter((n) => n.id === a.graph.root || (types.has(n.type)
      && !(hideNormalConds && n.type === 'CONDITION' && !n.out_of_range)))
    return { nodes, edges: a.graph.edges }
  }, [a, types, hideNormalConds])

  const go = (e?: React.FormEvent) => {
    e?.preventDefault()
    const v = input.trim()
    if (v) setParams({ lot: v })
  }

  return (
    <>
      <PageHead title="계보 그래프" sub="불량 LOT 에서 거슬러 올라가 원인 후보를 찾고, 후보를 공유하는 하위 LOT(잠재 불량 범위)를 확인합니다." />
      <Panel>
        <form className="form-row" onSubmit={go}>
          <Field label="LOT 번호"><input value={input} onChange={(e) => setInput(e.target.value)} placeholder="예: P-260922-02-1" style={{ width: 220 }} /></Field>
          <button className="primary" type="submit">조회</button>
          <Field label={`추적 깊이 ${depth}`}>
            <input type="range" min={1} max={12} value={depth} onChange={(e) => setDepth(Number(e.target.value))} style={{ width: 140, padding: 0 }} />
          </Field>
          <Field label="방향">
            <div className="row">
              <button type="button" className={direction === 'up' ? 'primary' : ''} onClick={() => setDownNode(null)}>역방향 (원인)</button>
              <button type="button" className={direction === 'down' ? 'primary' : ''} disabled={!a}
                onClick={() => a && setDownNode(a.graph.nodes.find((n) => n.id === (selected ?? a.candidates[0]?.node)) ?? null)}>
                정방향 (영향 범위)
              </button>
            </div>
          </Field>
          {direction === 'up' && (
            <Field label="노드 종류">
              <div className="row" style={{ height: 30 }}>
                {ALL_TYPES.map((t) => (
                  <label key={t} className="check">
                    <input type="checkbox" checked={types.has(t)} disabled={t === 'LOT'}
                      onChange={(e) => {
                        const next = new Set(types)
                        if (e.target.checked) next.add(t)
                        else next.delete(t)
                        setTypes(next)
                      }} />
                    {NODE_LABEL[t]}
                  </label>
                ))}
                <label className="check" title="허용 범위 안인 조건 노드는 숨깁니다">
                  <input type="checkbox" checked={hideNormalConds} onChange={(e) => setHideNormalConds(e.target.checked)} />
                  이탈 조건만
                </label>
              </div>
            </Field>
          )}
        </form>
      </Panel>

      <div style={{ height: 12 }} />
      {!lotNo && <Panel><Empty>LOT 번호를 입력하세요. 관리도·대시보드의 LOT 링크로도 들어올 수 있습니다.</Empty></Panel>}
      {lotNo && !a && <Panel><Loading error={analysis.error} loading={analysis.loading} /></Panel>}
      {a && direction === 'up' && (
        <div className="split-right">
          <Panel
            title={<>{a.lot.lot_no} <StatusBadge status={a.lot.status} /></>}
            actions={<span className="faint" style={{ fontSize: 12 }}>분석 기간 {fmtDateTime(a.window.from)} ~ {fmtDateTime(a.window.to)} · 완제품 {a.population.finished_lots} 중 불량 {a.population.defective}{a.graph.truncated ? ' · 노드 수 제한으로 일부 생략' : ''}</span>}
            flush
          >
            <CauseGraph nodes={shown.nodes} edges={shown.edges} topN={TOP_N} selected={selected} onSelect={setSelected} height="calc(100vh - 300px)" />
            <div style={{ padding: '8px 12px', borderTop: '1px solid var(--border)' }}><GraphLegend /></div>
          </Panel>
          <div className="stack" style={{ maxHeight: 'calc(100vh - 210px)', overflowY: 'auto' }}>
            {selected && candByNode.get(selected) ? (
              <NodeDetail
                node={a.graph.nodes.find((n) => n.id === selected)!}
                cand={candByNode.get(selected)!}
                analysis={a}
                onDownstream={(n) => setDownNode(n)}
                onClose={() => setSelected(null)}
              />
            ) : selected === a.graph.root ? (
              <Panel title="불량 LOT"><div className="muted">원인 후보를 보려면 다른 노드를 누르세요.</div></Panel>
            ) : null}
            <CandidateList cands={a.candidates} selected={selected} onSelect={setSelected} />
          </div>
        </div>
      )}
      {a && direction === 'down' && downNode && (
        <DownstreamView node={downNode} rootCreated={a.graph.nodes.find((n) => n.id === a.graph.root)?.created_at ?? null}
          depth={depth} onBack={() => setDownNode(null)} />
      )}
    </>
  )
}

function CandidateList(props: { cands: Candidate[]; selected: string | null; onSelect: (id: string) => void }) {
  const max = props.cands[0]?.score || 1
  return (
    <Panel title="원인 후보 순위" flush>
      <table className="t">
        <thead><tr><th style={{ width: 40 }}>순위</th><th style={{ width: 46 }}>종류</th><th>노드</th><th style={{ width: 110 }}>종합 점수</th></tr></thead>
        <tbody>
          {props.cands.slice(0, 15).map((c) => (
            <tr key={c.node} className={`clickable ${props.selected === c.node ? 'selected' : ''}`} onClick={() => props.onSelect(c.node)}>
              <td>{c.rank}</td>
              <td>{NODE_LABEL[c.type]}</td>
              <td title={`${c.label} ${c.sub}`}>{c.label}</td>
              <td><div className="row"><ScoreBar value={c.score} max={max} /><span style={{ fontSize: 12, width: 38, textAlign: 'right' }}>{c.score.toFixed(3)}</span></div></td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  )
}

function NodeDetail(props: { node: GraphNode; cand: Candidate; analysis: CauseAnalysis; onDownstream: (n: GraphNode) => void; onClose: () => void }) {
  const { node, cand } = props
  const lotId = node.type === 'LOT' ? Number(node.id.split(':')[1]) : null
  return (
    <>
      <Panel title={`${NODE_LABEL[node.type]} · ${node.label}`} actions={<button className="small" onClick={props.onClose}>닫기</button>}>
        <div className="stack" style={{ gap: 8 }}>
          <div className="row wrap" style={{ fontSize: 13 }}>
            {node.type === 'LOT' && <>
              <span className="muted">{node.sub} ({CATEGORY_LABEL[node.category ?? ''] ?? '-'})</span>
              {node.status && <StatusBadge status={node.status} />}
              <span className="muted">생성 {fmtDateTime(node.created_at)}</span>
            </>}
            {node.type === 'EQUIPMENT' && <span className="muted">추적 경로 처리 시각 {fmtDateTime(node.at)}</span>}
            {node.type === 'CONDITION' && (node.out_of_range ? <span className="badge warn">■ 허용 범위 이탈</span> : <span className="badge ok">● 범위 내</span>)}
          </div>
          {node.type !== 'CONDITION' && (
            <MiniChart itemTypeId={node.type === 'LOT' ? node.item_type_id : null}
              stepId={node.type === 'STEP' ? node.process_step_id : node.type === 'EQUIPMENT' ? node.process_step_id : null}
              highlightLot={lotId} around={node.at ?? node.created_at ?? null} />
          )}
          <div className="row">
            <button onClick={() => props.onDownstream(node)}>정방향 영향 범위 보기</button>
          </div>
        </div>
      </Panel>
      <Panel title="점수 상세">
        <ScoreDetail c={cand} labels={props.analysis.factor_labels} />
      </Panel>
      <ConfirmPanel lotNo={props.analysis.lot.lot_no} cand={cand} />
    </>
  )
}

function ConfirmPanel({ lotNo, cand }: { lotNo: string; cand: Candidate }) {
  const { can } = useAuth()
  const judgments = useAsync(() => api.get<Judgment[]>('/defects', { lot_no: lotNo }), [lotNo])
  const j = (judgments.data ?? []).find((x) => x.lot_no === lotNo)
  const [confidence, setConfidence] = useState(1)
  const [memo, setMemo] = useState('')
  const [msg, setMsg] = useState<string | null>(null)
  if (!can('OPERATOR')) return null
  if (!judgments.data) return null
  if (!j) return <Panel title="원인 확정"><div className="muted">이 LOT 에 불량 판정이 없습니다. 불량 판정 후 원인을 확정할 수 있습니다.</div></Panel>
  const confirmed = j.root_causes.find((r) => r.node === cand.node)
  const submit = async () => {
    try {
      await api.post(`/defects/${j.id}/root-cause`, { node: cand.node, confidence, memo: memo || null })
      setMsg('원인으로 확정했습니다. 다음 분석부터 과거 확정 이력에 반영됩니다.')
      judgments.reload()
    } catch (e) {
      setMsg(errorText(e))
    }
  }
  return (
    <Panel title="원인 확정">
      {j.root_causes.length > 0 && (
        <div className="muted" style={{ fontSize: 12, marginBottom: 8 }}>
          확정된 원인: {j.root_causes.map((r) => `${r.label} (${NODE_LABEL[r.type]}, ${r.confidence})`).join(', ')}
        </div>
      )}
      <div className="form-row">
        <Field label="확신도">
          <select value={confidence} onChange={(e) => setConfidence(Number(e.target.value))}>
            <option value={1}>확실 (1.0)</option><option value={0.7}>높음 (0.7)</option><option value={0.4}>추정 (0.4)</option>
          </select>
        </Field>
        <Field label="메모" style={{ flex: 1 }}><input value={memo} onChange={(e) => setMemo(e.target.value)} /></Field>
        <button className="primary" disabled={!!confirmed} onClick={submit}>{confirmed ? '이미 확정됨' : '이 노드를 원인으로 확정'}</button>
      </div>
      {msg && <div className="notice" style={{ marginTop: 8 }}>{msg}</div>}
    </Panel>
  )
}

function DownstreamView(props: { node: GraphNode; rootCreated: string | null; depth: number; onBack: () => void }) {
  const { can } = useAuth()
  const isLot = props.node.type === 'LOT'
  const at = props.node.at ?? props.rootCreated
  const down = useAsync(
    () => api.get<Downstream>(`/trace/downstream/${props.node.id}`, isLot ? { depth: props.depth + 4 } : { depth: props.depth + 4, at }),
    [props.node.id, at, props.depth],
  )
  const d = down.data
  const [checked, setChecked] = useState<Set<string>>(new Set())
  const [msg, setMsg] = useState<string | null>(null)
  useEffect(() => {
    if (d) setChecked(new Set(d.lots.filter((l) => l.category === 'FINISHED' && l.status !== 'DEFECT').map((l) => l.lot_no)))
  }, [d])

  const nodes: GraphNode[] = useMemo(() => (d?.lots ?? []).map((l) => ({
    id: `lot:${l.id}`, type: 'LOT', label: l.lot_no, sub: l.item_type, score: l.is_seed ? 0.9 : 0, rank: null,
    status: l.status, category: l.category, created_at: l.created_at,
  })), [d])
  const edges = useMemo(() => (d?.edges ?? []).map((e) => ({ id: `${e.source}->${e.target}`, kind: 'genealogy' as const, ...e })), [d])

  const apply = async (status: 'HOLD' | 'QUARANTINE') => {
    const reason = prompt(`선택한 ${checked.size}개 LOT 을 ${status === 'HOLD' ? '보류' : '격리'} 처리합니다. 사유를 입력하세요.`)
    if (!reason) return
    try {
      const r = await api.post<{ changed: number; skipped: number }>('/lots/bulk-status', { lot_nos: [...checked], status, reason })
      setMsg(`${r.changed}개 변경, ${r.skipped}개 건너뜀 (이미 같은 상태 또는 불량).`)
      down.reload()
    } catch (e) {
      setMsg(errorText(e))
    }
  }

  return (
    <div className="split-right">
      <Panel
        title={<>정방향 영향 범위 · {NODE_LABEL[props.node.type]} {props.node.label}</>}
        actions={<button className="small" onClick={props.onBack}>역방향으로 돌아가기</button>}
        flush
      >
        <Loading error={down.error} loading={down.loading && !d} />
        {d && (
          <>
            <div className="notice" style={{ margin: 8 }}>
              {isLot ? '이 LOT 의 하위 계보 전체' : `기간 ${fmtDateTime(d.window?.from)} ~ ${fmtDateTime(d.window?.to)} 동안 이 노드를 거친 LOT ${d.seed_count}개의 하위 계보`}
              {' '}· 완제품 {d.finished_count}개 (진한 색 = 시작 LOT){d.truncated ? ' · 노드 수 제한으로 일부 생략' : ''}
            </div>
            <CauseGraph nodes={nodes} edges={edges} topN={0} selected={null} onSelect={() => undefined} height="calc(100vh - 350px)" />
          </>
        )}
      </Panel>
      <Panel title={`잠재 불량 범위 (${checked.size} 선택)`} flush
        actions={can('OPERATOR') ? (
          <div className="row">
            <button className="small" disabled={!checked.size} onClick={() => apply('HOLD')}>보류</button>
            <button className="small danger" disabled={!checked.size} onClick={() => apply('QUARANTINE')}>격리</button>
          </div>
        ) : <span className="faint">실무자 이상 처리 가능</span>}>
        {msg && <div className="notice" style={{ margin: 8 }}>{msg}</div>}
        <div className="table-wrap" style={{ maxHeight: 'calc(100vh - 290px)' }}>
          <table className="t">
            <thead><tr><th style={{ width: 30 }} /><th>LOT</th><th style={{ width: 70 }}>분류</th><th style={{ width: 70 }}>상태</th></tr></thead>
            <tbody>
              {(d?.lots ?? []).filter((l) => !l.is_seed || isLot).map((l) => (
                <tr key={l.id}>
                  <td><input type="checkbox" checked={checked.has(l.lot_no)} disabled={l.status === 'DEFECT'}
                    onChange={(e) => {
                      const next = new Set(checked)
                      if (e.target.checked) next.add(l.lot_no)
                      else next.delete(l.lot_no)
                      setChecked(next)
                    }} /></td>
                  <td title={l.item_type}>{l.lot_no}</td>
                  <td>{CATEGORY_LABEL[l.category]}</td>
                  <td><StatusBadge status={l.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  )
}

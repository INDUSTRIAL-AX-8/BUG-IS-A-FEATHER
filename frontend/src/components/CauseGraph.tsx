import { useEffect, useRef } from 'react'
import cytoscape, { type Core, type ElementDefinition } from 'cytoscape'
import dagre from 'cytoscape-dagre'
import type { GraphEdge, GraphNode, NodeKind } from '../api/causes'
import { SCORE_COLORS, scoreLevel } from '../lib/format'

cytoscape.use(dagre)

// 노드 종류별 모양: LOT=사각, 공정=육각, 설비=다이아몬드, 조건=타원
const SHAPE: Record<NodeKind, string> = {
  LOT: 'round-rectangle',
  STEP: 'hexagon',
  EQUIPMENT: 'diamond',
  CONDITION: 'ellipse',
}

const KIND_TEXT: Record<NodeKind, string> = { LOT: '', STEP: '[공정] ', EQUIPMENT: '[설비] ', CONDITION: '[조건] ' }

export function CauseGraph(props: {
  nodes: GraphNode[]
  edges: GraphEdge[]
  topN: number
  selected: string | null
  onSelect: (id: string | null) => void
  height: string
}) {
  const ref = useRef<HTMLDivElement>(null)
  const cy = useRef<Core | null>(null)
  const selectRef = useRef(props.onSelect)
  selectRef.current = props.onSelect

  useEffect(() => {
    if (!ref.current) return
    const ids = new Set(props.nodes.map((n) => n.id))
    const elements: ElementDefinition[] = [
      ...props.nodes.map((n) => {
        const level = scoreLevel(n.score)
        const rank = n.rank && n.rank <= props.topN ? `${n.rank}위 · ` : ''
        const sub = n.type === 'LOT' ? n.sub : n.out_of_range ? '허용 범위 이탈' : ''
        return {
          data: {
            ...n,
            display: `${rank}${KIND_TEXT[n.type]}${n.label}${sub ? '\n' + sub : ''}${n.is_root ? '\n[불량]' : ''}`,
            fill: SCORE_COLORS[level],
            text: level >= 4 ? '#ffffff' : '#1c222b',
            shape: SHAPE[n.type],
          },
          classes: [n.is_root ? 'root' : '', n.rank && n.rank <= props.topN ? 'top' : '', n.out_of_range ? 'oor' : '',
            n.status === 'DEFECT' && !n.is_root ? 'defect' : ''].join(' '),
        }
      }),
      ...props.edges.filter((e) => ids.has(e.source) && ids.has(e.target)).map((e) => ({
        data: { ...e, width: e.kind === 'genealogy' ? 1 + Math.min(1, e.ratio ?? 0.25) * 7 : 1 },
        classes: e.kind,
      })),
    ]

    const c = cytoscape({
      container: ref.current,
      elements,
      wheelSensitivity: 0.25,
      minZoom: 0.15,
      maxZoom: 2.5,
      style: [
        {
          selector: 'node',
          style: {
            shape: 'data(shape)' as never,
            'background-color': 'data(fill)',
            'border-width': 1,
            'border-color': '#7b8592',
            label: 'data(display)',
            'text-wrap': 'wrap',
            'text-valign': 'center',
            'text-halign': 'center',
            color: 'data(text)',
            'font-size': 11,
            'font-family': "'Pretendard', 'Malgun Gothic', sans-serif",
            width: 'label',
            height: 'label',
            padding: '9px',
          },
        },
        { selector: 'node[type = "STEP"], node[type = "EQUIPMENT"]', style: { padding: '14px' } },
        { selector: 'node.top', style: { 'border-width': 2, 'border-color': '#1d4580', 'font-weight': 700 } },
        { selector: 'node.root', style: { 'border-width': 4, 'border-color': '#a8261d', 'border-style': 'double' } },
        { selector: 'node.defect', style: { 'border-color': '#a8261d', 'border-width': 2 } },
        { selector: 'node.oor', style: { 'border-style': 'dashed', 'border-color': '#9a6200', 'border-width': 2 } },
        { selector: 'node:selected', style: { 'overlay-color': '#24508f', 'overlay-opacity': 0.18, 'overlay-padding': 6 } },
        {
          selector: 'edge',
          style: {
            width: 'data(width)',
            'line-color': '#9aa3af',
            'target-arrow-color': '#9aa3af',
            'target-arrow-shape': 'triangle',
            'arrow-scale': 0.8,
            'curve-style': 'bezier',
          },
        },
        { selector: 'edge.process, edge.belongs', style: { 'line-style': 'dashed', 'line-color': '#c3c9d1', 'target-arrow-color': '#c3c9d1' } },
        { selector: 'edge.condition', style: { 'line-style': 'dotted', 'line-color': '#c3c9d1', 'target-arrow-shape': 'none' } },
      ],
      layout: {
        name: 'dagre',
        rankDir: 'LR',
        nodeSep: 14,
        rankSep: 70,
        edgeSep: 6,
        animate: false,
      } as never,
    })
    c.on('tap', 'node', (e) => selectRef.current(e.target.id()))
    c.on('tap', (e) => {
      if (e.target === c) selectRef.current(null)
    })
    cy.current = c
    return () => {
      c.destroy()
      cy.current = null
    }
  }, [props.nodes, props.edges, props.topN])

  useEffect(() => {
    const c = cy.current
    if (!c) return
    c.elements().unselect()
    if (props.selected) c.getElementById(props.selected).select()
  }, [props.selected, props.nodes])

  return (
    <div style={{ position: 'relative' }}>
      <div ref={ref} style={{ width: '100%', height: props.height, background: '#fbfbfc' }} />
      <div className="row" style={{ position: 'absolute', right: 8, bottom: 8, gap: 4 }}>
        <button className="small" onClick={() => cy.current?.fit(undefined, 30)}>전체 보기</button>
      </div>
    </div>
  )
}

export function GraphLegend() {
  return (
    <div className="row wrap" style={{ gap: 14, fontSize: 12, color: 'var(--muted)' }}>
      <span>□ LOT</span><span>⬡ 공정</span><span>◇ 설비</span><span>○ 조건</span>
      <span>이중 붉은 테두리 = 판정 불량 LOT</span>
      <span>점선 테두리 = 조건 이탈</span>
      <span className="score-legend">원인 점수 낮음 {SCORE_COLORS.map((c) => <i key={c} style={{ background: c }} />)} 높음</span>
      <span>엣지 두께 = 투입 비율</span>
    </div>
  )
}

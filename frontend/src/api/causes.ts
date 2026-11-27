export type NodeKind = 'LOT' | 'STEP' | 'EQUIPMENT' | 'CONDITION'

export interface GraphNode {
  id: string
  type: NodeKind
  label: string
  sub: string
  score: number
  rank: number | null
  status?: string
  is_root?: boolean
  category?: string
  depth?: number
  created_at?: string
  item_type_id?: number
  process_step_id?: number | null
  out_of_range?: boolean
  at?: string
}

export interface GraphEdge {
  id: string
  source: string
  target: string
  kind: 'genealogy' | 'process' | 'belongs' | 'condition'
  ratio?: number | null
}

export interface Graph {
  root: string
  direction: string
  depth: number
  truncated: boolean
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export interface Factor {
  label: string
  score: number
  raw: Record<string, unknown>
  note: string
}

export interface Candidate {
  node: string
  type: NodeKind
  label: string
  sub: string
  score: number
  rank: number
  contributions: Record<string, number>
  specificity?: { exposure_share: number | null; factor: number; note: string }
  factors: Record<string, Factor>
}

export interface CauseAnalysis {
  lot: { id: number; lot_no: string; status: string }
  defect_type_id: number | null
  window: { from: string; to: string }
  population: { finished_lots: number; defective: number }
  weights: Record<string, number>
  factor_labels: Record<string, string>
  candidates: Candidate[]
  graph: Graph
}

export interface DownstreamLot {
  id: number
  lot_no: string
  item_type: string
  category: string
  status: string
  created_at: string
  depth: number
  is_seed: boolean
}

export interface Downstream {
  node: string
  node_type: NodeKind
  window: { from: string; to: string } | null
  depth: number
  truncated: boolean
  seed_count: number
  lots: DownstreamLot[]
  finished_count: number
  edges: { source: string; target: string; ratio: number | null }[]
}

export interface Judgment {
  id: number
  lot_id: number
  lot_no: string
  item_type: string
  defect_type_id: number
  defect_type: string
  judged_by: string | null
  judged_at: string
  memo: string | null
  attachments: string[]
  is_cancelled: boolean
  root_causes: {
    id: number
    node: string
    type: NodeKind
    label: string | null
    confidence: number
    memo: string | null
    confirmed_by: string | null
    confirmed_at: string
  }[]
}

export const FACTOR_ORDER = ['spc', 'lift', 'arima', 'condition', 'design', 'history'] as const

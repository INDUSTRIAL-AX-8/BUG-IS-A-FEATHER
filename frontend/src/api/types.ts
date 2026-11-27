export type Role = 'VIEWER' | 'OPERATOR' | 'ADMIN'
export type LotStatus = 'NORMAL' | 'HOLD' | 'QUARANTINE' | 'DEFECT'

export interface User {
  id: number
  username: string
  display_name: string | null
  role: Role
  is_active: boolean
  created_at: string
  /** 공개된 초기 비밀번호로 로그인함 — 비밀번호를 바꾸기 전에는 다른 화면을 쓸 수 없다 */
  must_change_password?: boolean
}

export interface ItemType {
  id: number
  name: string
  category: 'RAW' | 'PART' | 'SEMI' | 'FINISHED'
  description: string | null
  is_active: boolean
}

export interface ProcessStep {
  id: number
  name: string
  seq: number
  description: string | null
  is_active: boolean
}

export interface Equipment {
  id: number
  name: string
  process_step_id: number | null
  description: string | null
  pos_x: number | null
  pos_y: number | null
  pos_z: number | null
  model_path: string | null
  is_active: boolean
}

export interface ConditionDefinition {
  id: number
  name: string
  unit: string | null
  min_value: number | null
  max_value: number | null
  process_step_id: number | null
  is_active: boolean
}

export interface RuleSetting {
  enabled?: boolean
  params?: Record<string, number>
}

export interface Characteristic {
  id: number
  name: string
  unit: string | null
  target: number | null
  usl: number | null
  lsl: number | null
  subgroup_size: number
  chart_type: 'AUTO' | 'IMR' | 'XBAR_R' | 'XBAR_S'
  baseline_scope: 'GLOBAL' | 'PARENT_LOT'
  rule_config: Record<string, RuleSetting> | null
  item_type_id: number | null
  process_step_id: number | null
  is_active: boolean
}

export interface DefectType {
  id: number
  code: string
  name: string
  description: string | null
  is_active: boolean
}

export interface Lot {
  id: number
  lot_no: string
  item_type_id: number
  item_type_name: string | null
  created_at: string
  qty: number | null
  status: LotStatus
}

export interface LotDetail extends Lot {
  parent_count: number
  child_count: number
  judgment_count: number
}

export interface SpcCharSummary {
  id: number
  name: string
  unit: string | null
  is_active: boolean
  owner: string | null
  owner_kind: string
  chart_type: string
  measurements: number
  open_violations: number
  limit_version: number | null
}

export interface ChartPoint {
  index: number
  subgroup: number
  time: string
  value: number
  values: number[]
  dispersion: number | null
  ucl: number
  lcl: number
  center: number
  regime: number | null
  lot_ids: number[]
  lot_nos: (string | null)[]
  rules: number[]
}

export interface ChartData {
  characteristic: {
    id: number
    name: string
    unit: string | null
    target: number | null
    usl: number | null
    lsl: number | null
    subgroup_size: number
  }
  chart_type: string
  baseline_scope: 'GLOBAL' | 'PARENT_LOT'
  provisional: boolean
  message: string | null
  limits: {
    center: number
    ucl: number
    lcl: number
    sigma: number
    dispersion_center: number | null
    dispersion_ucl: number | null
    dispersion_lcl: number | null
    version: number | null
    baseline_from: string | null
    baseline_to: string | null
  } | null
  rules: { rule_no: number; enabled: boolean; params: Record<string, number>; description: string }[]
  violations: {
    id: number | null
    rule_no: number
    description: string
    first_index: number
    last_index: number
    points: number[]
    occurred_at: string
  }[]
  points: ChartPoint[]
}

export interface ViolationRow {
  id: number
  characteristic_id: number
  characteristic: string
  owner: string | null
  rule_no: number
  description: string
  occurred_at: string
  detected_at: string
  point_count: number
  lot_ids: number[]
  lot_nos: (string | null)[]
  acknowledged_at: string | null
  acknowledged_by: number | null
}

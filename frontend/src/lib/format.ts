// 날짜 표시: YYYY-MM-DD HH:mm (서버 시각은 공장 로컬 시각, 타임존 없음)
export function fmtDateTime(v: string | null | undefined): string {
  if (!v) return '-'
  const m = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/.exec(v)
  return m ? `${m[1]} ${m[2]}` : v
}

export function fmtDate(v: string | null | undefined): string {
  if (!v) return '-'
  return v.slice(0, 10)
}

export function fmtNum(v: number | null | undefined, digits = 3): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '-'
  const abs = Math.abs(v)
  if (abs !== 0 && (abs >= 1e6 || abs < 1e-3)) return v.toExponential(2)
  const n = Number(v.toFixed(digits))
  return abs >= 1000 ? n.toLocaleString('ko-KR', { maximumFractionDigits: digits }) : n.toString()
}

export function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return '-'
  return `${(v * 100).toFixed(digits)}%`
}

// <input type="datetime-local"> 값 ↔ 서버 ISO
export function toLocalInput(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}

export const STATUS_LABEL: Record<string, string> = {
  NORMAL: '정상',
  HOLD: '보류',
  QUARANTINE: '격리',
  DEFECT: '불량',
}

export const CATEGORY_LABEL: Record<string, string> = {
  RAW: '원자재',
  PART: '부품',
  SEMI: '반제품',
  FINISHED: '완제품',
}

export const ROLE_LABEL: Record<string, string> = {
  VIEWER: '조회자',
  OPERATOR: '실무자',
  ADMIN: '관리자',
}

export const CHART_LABEL: Record<string, string> = {
  AUTO: '자동',
  IMR: 'I-MR',
  XBAR_R: 'Xbar-R',
  XBAR_S: 'Xbar-S',
}

export const NODE_LABEL: Record<string, string> = {
  LOT: 'LOT',
  EQUIPMENT: '설비',
  STEP: '공정',
  CONDITION: '조건',
}

// 원인 점수(0~1) → 농도 단계 0~5
export function scoreLevel(score: number): number {
  if (score <= 0) return 0
  return Math.min(5, Math.max(1, Math.ceil(score * 5)))
}

export const SCORE_COLORS = ['#f1f4f9', '#d4e0f0', '#a9c1e2', '#7499cc', '#3f6fb0', '#1d4580']

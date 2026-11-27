import type { ReactNode } from 'react'
import { STATUS_LABEL } from '../lib/format'

export function Panel(props: { title?: ReactNode; actions?: ReactNode; children: ReactNode; flush?: boolean; className?: string; style?: React.CSSProperties }) {
  return (
    <section className={`panel ${props.className ?? ''}`} style={props.style}>
      {props.title !== undefined && (
        <div className="panel-head">
          <h2>{props.title}</h2>
          <span className="spacer" />
          {props.actions}
        </div>
      )}
      <div className={`panel-body ${props.flush ? 'flush' : ''}`}>{props.children}</div>
    </section>
  )
}

export function PageHead(props: { title: string; sub?: ReactNode; children?: ReactNode }) {
  return (
    <div className="page-head">
      <div className="titles">
        <h1>{props.title}</h1>
        {props.sub && <span className="sub">{props.sub}</span>}
      </div>
      <span className="spacer" />
      {props.children}
    </div>
  )
}

/**
 * 지표 카드. delta: 직전 기간 대비 변화(비율 또는 %p), goodWhen: 오르는 게 좋은지(up) 내리는 게 좋은지(down).
 * meter: 0~1 진행 막대, target: 막대 위 목표 위치.
 */
export function Metric(props: {
  label: ReactNode; value: ReactNode; unit?: string; hint?: ReactNode
  delta?: { value: number; text: string; goodWhen: 'up' | 'down' } | null
  meter?: number | null; target?: number | null; tone?: 'danger' | 'ok'
}) {
  const d = props.delta
  const cls = !d || Math.abs(d.value) < 1e-9 ? 'flat' : (d.value > 0) === (d.goodWhen === 'up') ? 'good' : 'bad'
  return (
    <div className="panel metric">
      <span className="label">{props.label}</span>
      <span className="value" style={props.tone ? { color: `var(--${props.tone})` } : undefined}>{props.value}{props.unit && <span className="unit">{props.unit}</span>}</span>
      {props.meter != null && (
        <div className="meter"><span style={{ width: `${Math.min(100, Math.max(0, props.meter * 100))}%` }} />{props.target != null && <i style={{ left: `${Math.min(100, props.target * 100)}%` }} />}</div>
      )}
      <span className="foot">
        {d && <span className={`delta ${cls}`}>{d.value > 0 ? '▲' : d.value < 0 ? '▼' : '–'} {d.text}</span>}
        <span className="hint">{props.hint ?? ' '}</span>
      </span>
    </div>
  )
}

// 상태는 기호 + 텍스트로 표시 (색만으로 의미 전달하지 않음)
const STATUS_CLASS: Record<string, [string, string]> = {
  NORMAL: ['ok', '●'],
  HOLD: ['warn', '■'],
  QUARANTINE: ['danger', '▲'],
  DEFECT: ['danger', '✕'],
}

export function StatusBadge({ status }: { status: string }) {
  const [cls, mark] = STATUS_CLASS[status] ?? ['neutral', '·']
  return (
    <span className={`badge ${cls}`}>
      <span aria-hidden>{mark}</span>
      {STATUS_LABEL[status] ?? status}
    </span>
  )
}

export function Level({ level, children }: { level: 'ok' | 'warn' | 'danger' | 'neutral'; children: ReactNode }) {
  const mark = { ok: '●', warn: '■', danger: '▲', neutral: '·' }[level]
  return (
    <span className={`badge ${level}`}>
      <span aria-hidden>{mark}</span>
      {children}
    </span>
  )
}

export function RuleBadge({ rule }: { rule: number }) {
  return <span className="badge rule" title={`넬슨룰 ${rule}`}>R{rule}</span>
}

export function Loading({ error, loading }: { error?: string | null; loading?: boolean }) {
  if (error) return <div className="notice error" style={{ whiteSpace: 'pre-wrap' }}>{error}</div>
  if (loading) return <div className="empty">불러오는 중…</div>
  return null
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>
}

export function Field(props: { label: string; children: ReactNode; style?: React.CSSProperties }) {
  return (
    <div className="field" style={props.style}>
      <label>{props.label}</label>
      {props.children}
    </div>
  )
}

export function ScoreBar({ value, max = 1 }: { value: number; max?: number }) {
  const pct = Math.max(0, Math.min(100, (value / (max || 1)) * 100))
  return (
    <div className="bar" title={value.toFixed(3)}>
      <span style={{ width: `${pct}%` }} />
    </div>
  )
}

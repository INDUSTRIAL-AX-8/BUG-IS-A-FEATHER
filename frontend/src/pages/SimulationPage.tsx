import { useEffect, useMemo, useRef, useState } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import { Html, OrbitControls } from '@react-three/drei'
import * as THREE from 'three'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { Equipment, ProcessStep } from '../api/types'
import { Help } from '../components/Help'
import { RtdbPanel } from '../components/RtdbPanel'
import { Empty, Level, PageHead, Panel } from '../components/ui'
import { fmtDateTime, fmtPct } from '../lib/format'
import { useAsync } from '../lib/hooks'
import { useLive, type LiveMessage, type LiveRecord, type LiveReview } from '../lib/live'
import { useTerms } from '../lib/terms'

// ------------------------------------------------------------------ 모델
// 데이터 기반 3D 디지털 트윈: 공정 단계 순서대로 설비를 배치하고, 이벤트(공정 시작·종료, 불량, 정지, SPC 위반)를
// 시간 순서대로 재생한다. 실시간 수집이 켜져 있으면 들어오는 기록을 즉시 반영한다.
// 설비 좌표(마스터 관리 > 설비 X/Y/Z)가 있으면 그 자리에, 없으면 공정 순서로 자동 배치한다.

interface CondVal { value: number; unit: string | null; min: number | null; max: number | null; learned?: boolean; out_of_range: boolean }
interface Ev {
  type: 'run_start' | 'run_end' | 'judgment' | 'equipment_event' | 'violation'
  at: string; until?: string | null; run_id?: number; lot_no?: string; lot_id?: number; step?: string | null; operator?: string | null
  equipment?: { id: number; name: string } | null; conditions?: Record<string, CondVal>; produced?: number | null; defects?: number
  kind?: string; note?: string | null; rule_no?: number; characteristic?: string; lot_ids?: number[]
}
interface Station { key: string; name: string; step: string; pos: [number, number, number]; equipmentId: number | null }
type Status = 'idle' | 'run' | 'alarm' | 'stop'
interface MachineState { status: Status; values: Record<string, CondVal>; lot: string | null; operator: string | null; alarm: string | null; stopUntil: number | null; lastAt: number }
interface Token { id: string; label: string; color: string; from: THREE.Vector3; to: THREE.Vector3; start: number; dur: number; size: number }
interface Totals { produced: number; defects: number }

const SPACING = 9
const SHIP: [number, number, number] = [0, 0, -6]
const HOLD: [number, number, number] = [0, 0, 0]
const REJECT: [number, number, number] = [0, 0, 6]
const MAX_TOKENS = 80

function rateColor(r: number): string {
  if (r >= 0.08) return '#c8423a'
  if (r >= 0.03) return '#d59a2b'
  return '#3d9a63'
}

function layout(steps: ProcessStep[], equipment: Equipment[], usedStations: Set<string>): { stations: Station[]; ship: [number, number, number]; hold: [number, number, number]; reject: [number, number, number] } {
  const order = [...steps].sort((a, b) => a.seq - b.seq)
  const stations: Station[] = []
  let col = 0
  for (const s of order) {
    const eqs = equipment.filter((e) => e.process_step_id === s.id && usedStations.has(`eq:${e.id}`))
    const stepUsed = usedStations.has(`step:${s.id}`)
    if (!eqs.length && !stepUsed) continue
    const items: Station[] = eqs.map((e) => ({ key: `eq:${e.id}`, name: e.name, step: s.name, equipmentId: e.id, pos: [0, 0, 0] }))
    if (stepUsed) items.push({ key: `step:${s.id}`, name: s.name, step: s.name, equipmentId: null, pos: [0, 0, 0] })
    items.forEach((it, i) => {
      const e = equipment.find((x) => x.id === it.equipmentId)
      it.pos = e && e.pos_x != null && e.pos_z != null ? [e.pos_x, e.pos_y ?? 0, e.pos_z] : [col * SPACING, 0, (i - (items.length - 1) / 2) * 6]
    })
    stations.push(...items)
    col++
  }
  const endX = Math.max(0, ...stations.map((s) => s.pos[0])) + SPACING * 0.9
  return { stations, ship: [endX, 0, SHIP[2]], hold: [endX, 0, HOLD[2]], reject: [endX, 0, REJECT[2]] }
}

function stationOf(ev: { equipment?: { id: number } | null; step?: string | null }, steps: ProcessStep[]): string | null {
  if (ev.equipment) return `eq:${ev.equipment.id}`
  const s = steps.find((x) => x.name === ev.step)
  return s ? `step:${s.id}` : null
}

// ------------------------------------------------------------------ 엔진 (재생·실시간 공통)
class TwinEngine {
  machines = new Map<string, MachineState>()
  tokens: Token[] = []
  lotAt = new Map<number | string, string>()
  totals: Totals = { produced: 0, defects: 0 }
  log: { at: number; text: string; level: 'ok' | 'warn' | 'danger' }[] = []
  shipCount = 0
  rejectCount = 0
  holdCount = 0
  held = new Map<number, number>()  // 검토 대기 중인 LOT → 수량
  constructor(public stations: Station[], public ship: THREE.Vector3, public reject: THREE.Vector3, public hold: THREE.Vector3 = new THREE.Vector3()) {}

  reset() {
    this.machines.clear(); this.tokens = []; this.lotAt.clear(); this.totals = { produced: 0, defects: 0 }
    this.log = []; this.shipCount = 0; this.rejectCount = 0; this.holdCount = 0; this.held.clear()
  }
  m(key: string): MachineState {
    let s = this.machines.get(key)
    if (!s) { s = { status: 'idle', values: {}, lot: null, operator: null, alarm: null, stopUntil: null, lastAt: 0 }; this.machines.set(key, s) }
    return s
  }
  posOf(key: string): THREE.Vector3 | null {
    const st = this.stations.find((s) => s.key === key)
    return st ? new THREE.Vector3(st.pos[0], 1.6, st.pos[2]) : null
  }
  addLog(at: number, text: string, level: 'ok' | 'warn' | 'danger') {
    this.log = [{ at, text, level }, ...this.log].slice(0, 40)
  }
  spawn(t: Token) {
    this.tokens = [...this.tokens.filter((x) => x.id !== t.id), t].slice(-MAX_TOKENS)
  }
  /** animate=false 이면 위치만 맞춤 (재생 위치를 건너뛸 때) */
  apply(ev: Ev, key: string | null, now: number, simMs: number, animate: boolean, stepDur: number) {
    if (ev.type === 'equipment_event' && key) {
      const s = this.m(key)
      s.status = 'stop'; s.stopUntil = ev.until ? Date.parse(ev.until) : simMs + 3600_000; s.alarm = null
      this.addLog(simMs, `${this.name(key)} ${ev.kind === 'STOP' ? '정지' : ev.kind}${ev.note ? ` — ${ev.note}` : ''}`, 'warn')
      return
    }
    if (ev.type === 'violation') {
      for (const lid of ev.lot_ids ?? []) {
        const k = this.lotAt.get(lid)
        if (k) { const s = this.m(k); s.status = 'alarm'; s.alarm = `${ev.characteristic} R${ev.rule_no} 위반` }
      }
      this.addLog(simMs, `${ev.characteristic} 넬슨룰 R${ev.rule_no} 위반`, 'danger')
      return
    }
    if (!key) return
    const s = this.m(key)
    if (ev.type === 'run_start') {
      if (s.stopUntil && simMs < s.stopUntil) return
      s.stopUntil = null
      s.values = ev.conditions ?? s.values
      s.lot = ev.lot_no ?? null; s.operator = ev.operator ?? null; s.lastAt = simMs
      const out = Object.entries(s.values).filter(([, v]) => v.out_of_range).map(([k]) => k)
      s.status = out.length ? 'alarm' : 'run'
      s.alarm = out.length ? `${out.join(', ')} 범위 이탈` : null
      if (out.length) this.addLog(simMs, `${this.name(key)} ${s.alarm} (${ev.lot_no})`, 'danger')
      const prev = ev.lot_id != null ? this.lotAt.get(ev.lot_id) : undefined
      if (ev.lot_id != null) this.lotAt.set(ev.lot_id, key)
      const to = this.posOf(key)
      if (to && animate) {
        const from = prev ? this.posOf(prev) ?? to.clone().add(new THREE.Vector3(-4, 0, 0)) : to.clone().add(new THREE.Vector3(-4, 3, 0))
        this.spawn({ id: `lot-${ev.lot_id}`, label: ev.lot_no ?? '', color: '#8fa7c6', from, to, start: now, dur: stepDur * 0.5, size: 0.7 })
      }
    } else if (ev.type === 'run_end') {
      // 마지막 공정이면 출하/불량 구역으로 (생산·불량 수는 시작 이벤트에 실려 옴)
      s.status = s.status === 'alarm' ? 'alarm' : 'run'
      s.lastAt = simMs
    }
  }
  finishLot(lotId: number | string, lotNo: string, key: string, produced: number, defects: number, now: number, simMs: number, animate: boolean, stepDur: number) {
    this.totals.produced += produced
    this.totals.defects += defects
    const rate = produced ? defects / produced : 0
    const from = this.posOf(key)
    if (!from) return
    this.shipCount += produced - defects
    this.rejectCount += defects
    if (animate) {
      this.spawn({ id: `lot-${lotId}`, label: `${lotNo} ${defects}/${produced}`, color: rateColor(rate), from, to: this.ship.clone().add(new THREE.Vector3(0, 0.6, (Math.random() - 0.5) * 3)), start: now, dur: stepDur, size: 0.9 })
      if (defects > 0) this.spawn({ id: `rej-${lotId}`, label: `불량 ${defects}`, color: '#c8423a', from, to: this.reject.clone().add(new THREE.Vector3((Math.random() - 0.5) * 2, 0.5, (Math.random() - 0.5) * 2)), start: now + 150, dur: stepDur, size: 0.45 + Math.min(0.6, rate * 4) })
    }
    if (rate >= 0.08) this.addLog(simMs, `${lotNo} 불량률 ${fmtPct(rate)} (${defects}/${produced})`, 'danger')
  }
  /** 실시간: 한 기록을 전체 공정 순서대로 흘려 보낸다. 의심이 있으면 검토 대기 구역으로. */
  flowRecord(m: LiveRecord, stageMs: number, onStep: () => void) {
    const route = m.route?.length ? m.route : [{ step: '', equipment: m.equipment, equipment_id: m.equipment_id, main: true, inspection: false, start: m.at, end: m.at, conditions: {}, out_of_range: m.out_of_range }]
    const id = `lot-${m.lot_id}`
    const ms = Date.parse(m.at)
    let prev: THREE.Vector3 | null = null
    route.forEach((st, i) => {
      window.setTimeout(() => {
        const key = `eq:${st.equipment_id}`
        const s = this.m(key)
        if (!(s.stopUntil && ms < s.stopUntil)) {
          s.values = st.conditions as Record<string, CondVal>
          s.lot = m.lot_no; s.lastAt = ms
          s.operator = st.main ? m.operator : null
          s.status = st.out_of_range.length ? 'alarm' : 'run'
          s.alarm = st.out_of_range.length ? `${st.out_of_range.join(', ')} 범위 이탈` : null
          if (st.out_of_range.length) this.addLog(ms, `[${st.step}] ${s.alarm} (${m.lot_no})`, 'danger')
        }
        const to = this.posOf(key)
        if (to) {
          const from: THREE.Vector3 = prev ?? to.clone().add(new THREE.Vector3(-4, 3, 0))
          const tinted = m.suspects.some((x) => x.source !== 'INSPECTION' && route.findIndex((r2) => r2.step === x.stage) <= i)
          this.spawn({ id, label: m.lot_no, color: tinted ? '#d59a2b' : '#8fa7c6', from, to, start: performance.now(), dur: stageMs * 0.8, size: 0.8 })
          prev = to
        }
        if (st.inspection) {
          const insp = m.suspects.filter((x) => x.source === 'INSPECTION')
          for (const x of insp) {
            if (to) this.spawn({ id: `sus-${x.id}`, label: `의심 ${x.qty}`, color: '#d59a2b', from: to, to: this.hold.clone().add(new THREE.Vector3((Math.random() - 0.5) * 2, 0.5, (Math.random() - 0.5) * 2)), start: performance.now(), dur: stageMs, size: 0.5 })
            this.held.set(x.id, x.qty ?? 0)
            this.holdCount += x.qty ?? 0
            this.addLog(ms, `[${st.step}] 검사 불합격 ${x.qty}개 → 검토 대기 (${m.lot_no})`, 'warn')
          }
        }
        onStep()
      }, i * stageMs)
    })
    // 마지막 단계 뒤: 조건 이탈·관리도 의심이 있으면 LOT 전체 보류(검토 대기), 아니면 출하
    window.setTimeout(() => {
      const whole = m.suspects.filter((x) => x.source !== 'INSPECTION')
      const flagged = m.suspects.filter((x) => x.source === 'INSPECTION').reduce((a, x) => a + (x.qty ?? 0), 0)
      this.totals.produced += m.produced
      if (prev) {
        if (whole.length) {
          this.spawn({ id, label: `${m.lot_no} 보류`, color: '#d59a2b', from: prev, to: this.hold.clone().add(new THREE.Vector3(0, 0.6, (Math.random() - 0.5) * 2)), start: performance.now(), dur: stageMs, size: 0.9 })
          for (const x of whole) { this.held.set(x.id, 0); this.addLog(ms, `[${x.stage}] ${x.detail} → ${m.lot_no} 보류·검토 대기`, 'warn') }
        } else {
          this.spawn({ id, label: `${m.lot_no} ${m.produced - flagged}`, color: '#3d9a63', from: prev, to: this.ship.clone().add(new THREE.Vector3(0, 0.6, (Math.random() - 0.5) * 3)), start: performance.now(), dur: stageMs, size: 0.9 })
          this.shipCount += m.produced - flagged
        }
      }
      onStep()
    }, route.length * stageMs)
  }
  /** 실무자 판단: 검토 대기 → 불량(확정) 또는 출하(불량 아님) */
  review(m: LiveReview, stageMs: number) {
    const qty = this.held.get(m.suspect_id) ?? 0
    this.held.delete(m.suspect_id)
    this.holdCount = Math.max(0, this.holdCount - qty)
    const confirmed = m.status === 'CONFIRMED'
    if (confirmed) { this.rejectCount += m.qty; this.totals.defects += m.qty } else this.shipCount += qty
    this.spawn({ id: `rev-${m.suspect_id}`, label: confirmed ? `불량 ${m.qty}` : '양품', color: confirmed ? '#c8423a' : '#3d9a63',
      from: this.hold.clone().add(new THREE.Vector3(0, 0.6, 0)), to: (confirmed ? this.reject : this.ship).clone().add(new THREE.Vector3((Math.random() - 0.5) * 2, 0.5, (Math.random() - 0.5) * 2)),
      start: performance.now(), dur: stageMs, size: 0.6 })
    this.addLog(Date.parse(m.at), `${m.lot_no} ${confirmed ? `불량 확정 ${m.qty}개 → 불량 적재` : '불량 아님 → 출하'}`, confirmed ? 'danger' : 'ok')
  }
  decay(simMs: number) {
    for (const s of this.machines.values()) {
      if (s.status === 'stop' && s.stopUntil && simMs >= s.stopUntil) { s.status = 'idle'; s.stopUntil = null }
    }
  }
  name(key: string) { return this.stations.find((s) => s.key === key)?.name ?? key }
}

// ------------------------------------------------------------------ 화면
export function SimulationPage() {
  const { t } = useTerms()
  const live = useLive()
  const steps = useAsync(() => api.get<ProcessStep[]>('/process-steps'), [])
  const equipment = useAsync(() => api.get<Equipment[]>('/equipment'), [])
  const range = useAsync(() => api.get<{ from: string | null; to: string | null }>('/events/range'), [])
  const lines = useAsync(() => api.get<Record<string, { equipment_id?: number }[]>>('/settings/process_lines'), [])
  const lineEq = useMemo(() => Object.values(lines.data ?? {}).flat().map((x) => x.equipment_id).filter((x): x is number => !!x), [lines.data])
  const [days, setDays] = useState(3)
  const [mode, setMode] = useState<'replay' | 'live'>(live.status?.running ? 'live' : 'replay')
  useEffect(() => { if (live.status?.running) setMode('live') }, [live.status?.running])

  const window_ = useMemo(() => {
    if (!range.data?.to) return null
    const to = Date.parse(range.data.to) + 3600_000
    const from = Math.max(Date.parse(range.data.from ?? range.data.to), to - days * 86400_000)
    return { from, to }
  }, [range.data, days])
  const events = useAsync<Ev[] | null>(() => window_
    ? api.get<{ events: Ev[] }>('/events', { from: new Date(window_.from - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 19), to: new Date(window_.to - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 19), types: 'run_start,run_end,equipment_event,violation', values: true, limit: 20000 }).then((r) => r.events)
    : Promise.resolve(null), [window_?.from, window_?.to])

  if (!steps.data || !equipment.data || range.loading) return <Panel><div className="empty">불러오는 중…</div></Panel>
  if (!range.data?.to) return <><PageHead title="3D 디지털 트윈" /><Panel><Empty>재생할 {t('run')}이 없습니다. 데모 데이터를 넣거나 실시간 수집을 시작하세요.</Empty></Panel></>
  return (
    <>
      <PageHead title={<>3D 디지털 트윈<Help topic="twin" /></> as unknown as string} sub={`${t('equipment')} 기록을 3D 공장 모형에 재생합니다. 실시간 수집이 켜져 있으면 들어오는 기록이 그대로 반영됩니다.`}>
        <div className="row">
          <button className={mode === 'replay' ? 'primary' : ''} onClick={() => setMode('replay')}>기록 재생</button>
          <button className={mode === 'live' ? 'primary' : ''} onClick={() => setMode('live')}>실시간 (디지털 트윈)</button>
          {mode === 'replay' && (
            <select value={days} onChange={(e) => setDays(Number(e.target.value))} title="재생할 기간">
              {[1, 3, 7, 30].map((d) => <option key={d} value={d}>최근 {d}일</option>)}
            </select>
          )}
        </div>
      </PageHead>
      {events.data
        ? <Twin key={`${mode}-${window_?.from}-${lineEq.join(',')}`} mode={mode} steps={steps.data} equipment={equipment.data} events={events.data} window={window_!} lineEq={lineEq} />
        : <Panel><div className="empty">이벤트 불러오는 중…</div></Panel>}
    </>
  )
}

function Twin(props: { mode: 'replay' | 'live'; steps: ProcessStep[]; equipment: Equipment[]; events: Ev[]; window: { from: number; to: number }; lineEq: number[] }) {
  const { t } = useTerms()
  const live = useLive()
  const sorted = useMemo(() => props.events.map((e) => ({ ...e, ms: Date.parse(e.at) })).sort((a, b) => a.ms - b.ms), [props.events])
  const used = useMemo(() => {
    const s = new Set<string>()
    for (const e of sorted) { const k = stationOf(e, props.steps); if (k) s.add(k) }
    if (live.status?.equipment_id) s.add(`eq:${live.status.equipment_id}`)
    for (const id of props.lineEq) s.add(`eq:${id}`)
    for (const x of live.status?.route ?? []) if (x.equipment_id) s.add(`eq:${x.equipment_id}`)
    return s
  }, [sorted, props.steps, live.status?.equipment_id, live.status?.route, props.lineEq])
  const lay = useMemo(() => layout(props.steps, props.equipment, used), [props.steps, props.equipment, used])
  const engine = useMemo(() => new TwinEngine(lay.stations, new THREE.Vector3(...lay.ship), new THREE.Vector3(...lay.reject), new THREE.Vector3(...lay.hold)), [lay])
  // run_start 에 실린 생산·불량 수를 run_end 에서 쓰기 위한 표
  const runInfo = useMemo(() => {
    const m = new Map<number, Ev>()
    for (const e of sorted) if (e.type === 'run_start' && e.run_id != null) m.set(e.run_id, e)
    return m
  }, [sorted])
  const lastRunOfLot = useMemo(() => {
    const m = new Map<number, number>()
    for (const e of sorted) if (e.type === 'run_end' && e.lot_id != null) m.set(e.lot_id, e.run_id!)
    return m
  }, [sorted])

  const [playing, setPlaying] = useState(true)
  const [secPerHour, setSecPerHour] = useState(1)  // 실제 1초 = 기록 몇 시간
  const sim = useRef({ ms: props.window.from, idx: 0 })
  const [, force] = useState(0)
  const stepDur = 900

  const process = (upTo: number, animate: boolean) => {
    const now = performance.now()
    while (sim.current.idx < sorted.length && sorted[sim.current.idx].ms <= upTo) {
      const e = sorted[sim.current.idx++]
      const key = stationOf(e, props.steps)
      engine.apply(e, key, now, e.ms, animate, stepDur)
      if (e.type === 'run_end' && e.lot_id != null && key && lastRunOfLot.get(e.lot_id) === e.run_id) {
        const info = runInfo.get(e.run_id!)
        const produced = info?.produced ?? 1
        engine.finishLot(e.lot_id, e.lot_no ?? '', key, produced, Math.min(info?.defects ?? 0, produced), now, e.ms, animate, stepDur)
      }
    }
    engine.decay(upTo)
  }
  const seek = (ms: number) => {
    engine.reset()
    sim.current = { ms, idx: 0 }
    process(ms, false)
    force((x) => x + 1)
  }

  // 실시간 모드: 시작 시 마지막 시점까지 맞춰 놓고, 들어오는 기록을 바로 반영
  useEffect(() => {
    if (props.mode !== 'live') return
    seek(props.window.to)
    const off = live.subscribe((m: LiveMessage) => {
      const now = performance.now()
      if (m.type === 'record') {
        const ms = Date.parse(m.at)
        const stages = Math.max(1, m.route?.length ?? 1)
        const stageMs = Math.max(250, Math.min(900, ((live.status?.interval_sec ?? 2) * 1000 * 0.85) / (stages + 1)))
        engine.flowRecord(m, stageMs, () => force((x) => x + 1))
        for (const v of m.violations) engine.addLog(ms, `${v.characteristic} 넬슨룰 R${v.rule_no} 위반 (${m.lot_no})`, 'danger')
        sim.current.ms = ms
      } else if (m.type === 'progress') {
        // 센서 스트림: 주 설비 조건값·진행 중 생산 수 갱신, 부품 몇 개가 다음 설비로 흘러감
        const key = `eq:${m.equipment_id}`
        const s = engine.m(key)
        const ms = Date.parse(m.at)
        if (!(s.stopUntil && ms < s.stopUntil)) {
          const conds: Record<string, CondVal> = {}
          for (const [name, v] of Object.entries(m.values)) {
            const [lo, hi, learned] = m.limits[name] ?? [null, null, false]
            conds[name] = { value: v, unit: m.units[name] ?? null, min: lo, max: hi, learned, out_of_range: m.out_of_range.includes(name) }
          }
          s.values = conds
          s.status = m.out_of_range.length ? 'alarm' : 'run'
          s.alarm = m.out_of_range.length ? `${m.out_of_range.join(', ')} 범위 이탈` : null
          s.lot = `진행 중 ${m.produced}/${m.planned}개`
          const route = live.status?.route ?? []
          const idx = route.findIndex((x) => x.equipment_id === m.equipment_id)
          const next = idx >= 0 && idx + 1 < route.length ? route[idx + 1] : null
          const from = engine.posOf(key)
          const to = next ? engine.posOf(`eq:${next.equipment_id}`) : null
          if (from && to) engine.spawn({ id: `shot-${ms}`, label: '', color: m.out_of_range.length ? '#d59a2b' : '#c9d4e3', from, to, start: performance.now(), dur: 2200, size: 0.3 })
        }
        sim.current.ms = ms
      } else if (m.type === 'review') {
        engine.review(m, 700)
      } else if (m.type === 'equipment_event') {
        engine.apply({ type: 'equipment_event', at: m.at, kind: m.kind, note: m.note }, `eq:${m.equipment_id}`, now, Date.parse(m.at), true, stepDur)
      }
      force((x) => x + 1)
    })
    return off
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.mode])

  // 재생 모드: 화면 갱신(오버레이)은 0.2초마다
  useEffect(() => {
    const id = window.setInterval(() => force((x) => x + 1), 200)
    return () => window.clearInterval(id)
  }, [])

  const machines = lay.stations.map((s) => ({ s, st: engine.m(s.key) }))
  const rate = engine.totals.produced ? engine.totals.defects / engine.totals.produced : 0
  return (
    <div className="split-right" style={{ gridTemplateColumns: 'minmax(0,1fr) 330px' }}>
      <Panel flush>
        <div style={{ position: 'relative', height: 'calc(100vh - 230px)', minHeight: 480, background: '#11161d' }}>
          <Canvas camera={{ position: [lay.ship[0] / 2, 5 + lay.ship[0] * 0.34, 8 + lay.ship[0] * 0.5], fov: 45 }} dpr={[1, 2]}>
            <color attach="background" args={['#11161d']} />
            <ambientLight intensity={0.55} />
            <directionalLight position={[10, 20, 10]} intensity={1.1} />
            <gridHelper args={[120, 60, '#2b3542', '#1c232c']} position={[lay.ship[0] / 2, 0, 0]} />
            <Clock playing={playing && props.mode === 'replay'} sim={sim} secPerHour={secPerHour} end={props.window.to} process={process} onEnd={() => setPlaying(false)} />
            {lay.stations.slice(0, -1).map((s, i) => {
              const n = lay.stations[i + 1]
              if (n.pos[0] === s.pos[0]) return null
              return <Belt key={s.key} from={s.pos} to={n.pos} />
            })}
            {lay.stations.length > 0 && <Belt from={lay.stations[lay.stations.length - 1].pos} to={lay.ship} />}
            {machines.map(({ s, st }) => <Machine key={s.key} station={s} state={st} />)}
            <Bin pos={lay.ship} label={`출하 ${engine.shipCount.toLocaleString()}`} color="#2f6d4a" />
            <Bin pos={lay.reject} label={`불량 적재 ${engine.rejectCount.toLocaleString()}`} color="#7a2a25" />
            {props.mode === 'live' && <Bin pos={lay.hold} label={`검토 대기 ${engine.holdCount.toLocaleString()}${engine.held.size ? ` (${engine.held.size}건)` : ''}`} color="#8a6416" />}
            <Tokens engine={engine} />
            <OrbitControls makeDefault target={[lay.ship[0] / 2, 0, 0]} maxPolarAngle={Math.PI / 2.1} />
          </Canvas>
          <div style={{ position: 'absolute', left: 10, top: 10, color: '#e9edf2', fontSize: 13, background: 'rgba(17,22,29,0.8)', padding: '8px 10px', borderRadius: 3, lineHeight: 1.6 }}>
            <div>{props.mode === 'live' ? (live.status?.running ? '● 실시간 수집 중' : '○ 실시간 수집 꺼짐 — 대시보드에서 시작') : '▶ 기록 재생'}</div>
            <div>기록 시각 <b>{fmtDateTime(new Date(sim.current.ms - new Date().getTimezoneOffset() * 60000).toISOString())}</b></div>
            <div>생산 {engine.totals.produced.toLocaleString()} · 불량 {engine.totals.defects.toLocaleString()} ({fmtPct(rate)})</div>
          </div>
          <div style={{ position: 'absolute', right: 10, bottom: 10, color: '#9aa6b5', fontSize: 12, background: 'rgba(17,22,29,0.8)', padding: '4px 8px', borderRadius: 3 }}>
            드래그 회전 · 휠 확대 · 우클릭 이동 | 설비 ● 정상 ■ 정지 ▲ 이상 | 트레이 초록=출하 주황=검토 대기 빨강=불량
          </div>
        </div>
        {props.mode === 'replay' && (
          <div className="row" style={{ padding: 10, borderTop: '1px solid var(--border)' }}>
            <button className="primary" onClick={() => { if (sim.current.ms >= props.window.to) seek(props.window.from); setPlaying((p) => !p) }} style={{ width: 70 }}>{playing ? '일시정지' : '재생'}</button>
            <select value={secPerHour} onChange={(e) => setSecPerHour(Number(e.target.value))} title="실제 1초에 재생할 기록 시간">
              {[0.25, 0.5, 1, 2, 4, 12].map((v) => <option key={v} value={v}>1초 = {v}시간</option>)}
            </select>
            <input type="range" min={props.window.from} max={props.window.to} step={60_000} value={sim.current.ms} style={{ flex: 1, padding: 0 }}
              onChange={(e) => seek(Number(e.target.value))} aria-label="재생 위치" />
          </div>
        )}
      </Panel>
      <div className="stack">
        {props.mode === 'live' && <RtdbPanel height={250} />}
        <Panel title={`${t('equipment')} 상태`} flush>
          <table className="t">
            <tbody>
              {machines.map(({ s, st }) => (
                <tr key={s.key} style={{ height: 'auto' }}>
                  <td style={{ padding: '6px 10px', whiteSpace: 'normal' }}>
                    <div className="row"><b>{s.name}</b><span className="right">
                      <Level level={st.status === 'alarm' ? 'danger' : st.status === 'stop' ? 'warn' : st.status === 'run' ? 'ok' : 'neutral'}>
                        {st.status === 'alarm' ? '이상' : st.status === 'stop' ? '정지' : st.status === 'run' ? '가동' : '대기'}
                      </Level></span></div>
                    {st.alarm && <div style={{ color: 'var(--danger)', fontSize: 12 }}>▲ {st.alarm}</div>}
                    <div className="muted" style={{ fontSize: 12 }}>
                      {Object.entries(st.values).slice(0, 6).map(([k, v]) => (
                        <span key={k} style={{ marginRight: 8, color: v.out_of_range ? 'var(--danger)' : undefined }}>{k} {v.value}{v.unit ?? ''}</span>
                      ))}
                    </div>
                    {st.lot && <div className="faint" style={{ fontSize: 11.5 }}>{st.lot}{st.operator ? ` · ${st.operator}` : ''}</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
        <Panel title="이벤트" flush>
          <div className="table-wrap" style={{ maxHeight: 330 }}>
            <table className="t"><tbody>
              {engine.log.map((l, i) => (
                <tr key={i}><td style={{ width: 92, fontSize: 12 }}>{fmtDateTime(new Date(l.at - new Date().getTimezoneOffset() * 60000).toISOString()).slice(5)}</td>
                  <td style={{ fontSize: 12.5, color: l.level === 'danger' ? 'var(--danger)' : l.level === 'warn' ? 'var(--warn)' : undefined }} title={l.text}>{l.text}</td></tr>
              ))}
            </tbody></table>
            {!engine.log.length && <div className="empty">이상·정지가 생기면 여기에 쌓입니다.</div>}
          </div>
        </Panel>
        <div className="faint" style={{ fontSize: 12 }}>설비 배치는 <Link to="/master">마스터 관리</Link> &gt; {t('equipment')}의 X/Y/Z 좌표로 바꿀 수 있습니다.</div>
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ 3D 부품
function Clock(props: { playing: boolean; sim: React.MutableRefObject<{ ms: number; idx: number }>; secPerHour: number; end: number; process: (ms: number, animate: boolean) => void; onEnd: () => void }) {
  useFrame((_, delta) => {
    if (!props.playing) return
    const next = Math.min(props.end, props.sim.current.ms + delta * props.secPerHour * 3600_000)
    props.sim.current.ms = next
    props.process(next, true)
    if (next >= props.end) props.onEnd()
  })
  return null
}

const STATUS_COLOR: Record<Status, string> = { idle: '#56606d', run: '#2f6fb0', alarm: '#c8423a', stop: '#c58a1c' }

function Machine({ station, state }: { station: Station; state: MachineState }) {
  const light = useRef<THREE.Mesh>(null)
  const body = useRef<THREE.MeshStandardMaterial>(null)
  useFrame(({ clock }) => {
    if (light.current) {
      const pulse = state.status === 'alarm' ? 0.6 + 0.4 * Math.sin(clock.elapsedTime * 8) : 1
      light.current.scale.setScalar(pulse)
    }
    if (body.current) body.current.emissiveIntensity = state.status === 'alarm' ? 0.35 + 0.25 * Math.sin(clock.elapsedTime * 6) : 0.05
  })
  const color = STATUS_COLOR[state.status]
  const isEq = station.equipmentId != null
  const values = Object.entries(state.values).slice(0, 4)
  return (
    <group position={station.pos}>
      <mesh position={[0, 0.25, 0]}><boxGeometry args={[isEq ? 4 : 3, 0.5, isEq ? 2.6 : 2]} /><meshStandardMaterial color="#3a434f" /></mesh>
      <mesh position={[0, 1.3, 0]}>
        <boxGeometry args={[isEq ? 3.4 : 2.4, isEq ? 1.6 : 0.9, isEq ? 2 : 1.4]} />
        <meshStandardMaterial ref={body} color={isEq ? '#7d8a99' : '#5d6773'} emissive={color} emissiveIntensity={0.05} />
      </mesh>
      {isEq && <mesh position={[-1.1, 2.6, 0]}><cylinderGeometry args={[0.35, 0.15, 1, 16]} /><meshStandardMaterial color="#9aa6b5" /></mesh>}
      <mesh ref={light} position={[1.2, 2.4, 0]}><sphereGeometry args={[0.28, 16, 16]} /><meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.9} /></mesh>
      <Html position={[0, 3.6, 0]} center distanceFactor={24} style={{ pointerEvents: 'none' }}>
        <div style={{ background: 'rgba(17,22,29,0.88)', color: '#e9edf2', padding: '4px 8px', borderRadius: 3, fontSize: 12, whiteSpace: 'nowrap', borderLeft: `3px solid ${color}`, fontFamily: 'Pretendard, sans-serif' }}>
          <b>{station.name}</b>{state.alarm ? <span style={{ color: '#ff8a80' }}> ▲ {state.alarm}</span> : state.status === 'stop' ? <span style={{ color: '#ffd27a' }}> ■ 정지</span> : null}
          {values.length > 0 && (
            <div style={{ fontSize: 11, color: '#b9c2cf' }}>
              {values.map(([k, v]) => <span key={k} style={{ marginRight: 6, color: v.out_of_range ? '#ff8a80' : undefined }}>{k} {v.value}{v.unit ?? ''}</span>)}
            </div>
          )}
        </div>
      </Html>
    </group>
  )
}

function Belt({ from, to }: { from: [number, number, number]; to: [number, number, number] }) {
  const a = new THREE.Vector3(from[0], 0.15, from[2])
  const b = new THREE.Vector3(to[0], 0.15, to[2])
  const mid = a.clone().add(b).multiplyScalar(0.5)
  const len = a.distanceTo(b)
  const angle = Math.atan2(b.z - a.z, b.x - a.x)
  return (
    <mesh position={mid} rotation={[0, -angle, 0]}>
      <boxGeometry args={[len, 0.12, 0.9]} />
      <meshStandardMaterial color="#2b333e" />
    </mesh>
  )
}

function Bin({ pos, label, color }: { pos: [number, number, number]; label: string; color: string }) {
  return (
    <group position={pos}>
      <mesh position={[0, 0.4, 0]}><boxGeometry args={[4, 0.8, 3.4]} /><meshStandardMaterial color={color} transparent opacity={0.65} /></mesh>
      <Html position={[0, 1.6, 0]} center distanceFactor={24} style={{ pointerEvents: 'none' }}>
        <div style={{ background: 'rgba(17,22,29,0.88)', color: '#e9edf2', padding: '3px 8px', borderRadius: 3, fontSize: 12, whiteSpace: 'nowrap', fontFamily: 'Pretendard, sans-serif' }}>{label}</div>
      </Html>
    </group>
  )
}

function Tokens({ engine }: { engine: TwinEngine }) {
  const [, setN] = useState(0)
  const count = useRef(0)
  useFrame(() => {
    if (engine.tokens.length !== count.current) { count.current = engine.tokens.length; setN(count.current) }
  })
  return <>{engine.tokens.map((t) => <TokenMesh key={t.id + t.start} token={t} />)}</>
}

function TokenMesh({ token }: { token: Token }) {
  const ref = useRef<THREE.Mesh>(null)
  useFrame(() => {
    if (!ref.current) return
    const k = Math.min(1, Math.max(0, (performance.now() - token.start) / token.dur))
    const e = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2
    ref.current.position.lerpVectors(token.from, token.to, e)
    ref.current.position.y += Math.sin(Math.PI * e) * 1.2
  })
  return (
    <mesh ref={ref} position={token.from}>
      <boxGeometry args={[token.size, token.size * 0.5, token.size]} />
      <meshStandardMaterial color={token.color} />
    </mesh>
  )
}

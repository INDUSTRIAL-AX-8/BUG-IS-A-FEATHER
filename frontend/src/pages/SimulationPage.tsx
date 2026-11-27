import { useEffect, useMemo, useRef, useState } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import { Html, OrbitControls } from '@react-three/drei'
import * as THREE from 'three'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { Equipment, ProcessStep } from '../api/types'
import { Help } from '../components/Help'
import { Empty, Level, PageHead, Panel } from '../components/ui'
import { fmtDateTime, fmtPct } from '../lib/format'
import { useAsync } from '../lib/hooks'
import { useLive, type LiveMessage } from '../lib/live'
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
const SHIP: [number, number, number] = [0, 0, -5]
const REJECT: [number, number, number] = [0, 0, 5]
const MAX_TOKENS = 80

function rateColor(r: number): string {
  if (r >= 0.08) return '#c8423a'
  if (r >= 0.03) return '#d59a2b'
  return '#3d9a63'
}

function layout(steps: ProcessStep[], equipment: Equipment[], usedStations: Set<string>): { stations: Station[]; ship: [number, number, number]; reject: [number, number, number] } {
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
  return { stations, ship: [endX, 0, SHIP[2]], reject: [endX, 0, REJECT[2]] }
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
  constructor(public stations: Station[], public ship: THREE.Vector3, public reject: THREE.Vector3) {}

  reset() {
    this.machines.clear(); this.tokens = []; this.lotAt.clear(); this.totals = { produced: 0, defects: 0 }
    this.log = []; this.shipCount = 0; this.rejectCount = 0
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
        ? <Twin key={`${mode}-${window_?.from}`} mode={mode} steps={steps.data} equipment={equipment.data} events={events.data} window={window_!} />
        : <Panel><div className="empty">이벤트 불러오는 중…</div></Panel>}
    </>
  )
}

function Twin(props: { mode: 'replay' | 'live'; steps: ProcessStep[]; equipment: Equipment[]; events: Ev[]; window: { from: number; to: number } }) {
  const { t } = useTerms()
  const live = useLive()
  const sorted = useMemo(() => props.events.map((e) => ({ ...e, ms: Date.parse(e.at) })).sort((a, b) => a.ms - b.ms), [props.events])
  const used = useMemo(() => {
    const s = new Set<string>()
    for (const e of sorted) { const k = stationOf(e, props.steps); if (k) s.add(k) }
    if (live.status?.equipment_id) s.add(`eq:${live.status.equipment_id}`)
    return s
  }, [sorted, props.steps, live.status?.equipment_id])
  const lay = useMemo(() => layout(props.steps, props.equipment, used), [props.steps, props.equipment, used])
  const engine = useMemo(() => new TwinEngine(lay.stations, new THREE.Vector3(...lay.ship), new THREE.Vector3(...lay.reject)), [lay])
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
        const key = `eq:${m.equipment_id}`
        const conds: Record<string, CondVal> = {}
        for (const [name, v] of Object.entries(m.values)) {
          const [lo, hi, learned] = m.limits[name] ?? [null, null, false]
          conds[name] = { value: v, unit: m.units[name] ?? null, min: lo, max: hi, learned, out_of_range: m.out_of_range.includes(name) }
        }
        const ms = Date.parse(m.at)
        engine.apply({ type: 'run_start', at: m.at, lot_no: m.lot_no, lot_id: m.lot_id, equipment: { id: m.equipment_id, name: m.equipment }, operator: m.operator, conditions: conds }, key, now, ms, true, stepDur)
        window.setTimeout(() => {
          engine.finishLot(m.lot_id, m.lot_no, key, m.produced, m.defects, performance.now(), ms, true, stepDur)
          for (const v of m.violations) engine.apply({ type: 'violation', at: m.at, characteristic: v.characteristic, rule_no: v.rule_no, lot_ids: [m.lot_id] }, null, performance.now(), ms, true, stepDur)
          force((x) => x + 1)
        }, stepDur * 0.6)
        sim.current.ms = ms
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
        <div style={{ position: 'relative', height: 'calc(100vh - 250px)', minHeight: 420, background: '#11161d' }}>
          <Canvas camera={{ position: [lay.ship[0] / 2 - 2, 9 + lay.stations.length * 1.5, 12 + lay.stations.length * 2], fov: 45 }} dpr={[1, 2]}>
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
            <Bin pos={lay.reject} label={`불량 ${engine.rejectCount.toLocaleString()}`} color="#7a2a25" />
            <Tokens engine={engine} />
            <OrbitControls makeDefault target={[lay.ship[0] / 2, 0, 0]} maxPolarAngle={Math.PI / 2.1} />
          </Canvas>
          <div style={{ position: 'absolute', left: 10, top: 10, color: '#e9edf2', fontSize: 13, background: 'rgba(17,22,29,0.8)', padding: '8px 10px', borderRadius: 3, lineHeight: 1.6 }}>
            <div>{props.mode === 'live' ? (live.status?.running ? '● 실시간 수집 중' : '○ 실시간 수집 꺼짐 — 대시보드에서 시작') : '▶ 기록 재생'}</div>
            <div>기록 시각 <b>{fmtDateTime(new Date(sim.current.ms - new Date().getTimezoneOffset() * 60000).toISOString())}</b></div>
            <div>생산 {engine.totals.produced.toLocaleString()} · 불량 {engine.totals.defects.toLocaleString()} ({fmtPct(rate)})</div>
          </div>
          <div style={{ position: 'absolute', right: 10, bottom: 10, color: '#9aa6b5', fontSize: 12, background: 'rgba(17,22,29,0.8)', padding: '4px 8px', borderRadius: 3 }}>
            드래그 회전 · 휠 확대 · 우클릭 이동 | ● 정상 ■ 정지 ▲ 이상
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

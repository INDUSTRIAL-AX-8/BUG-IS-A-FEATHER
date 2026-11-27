import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'

// 실시간 수집 스트림(SSE) — 앱 전체에서 연결 하나를 공유한다. 서버 주소 외 연결 없음.
export interface LiveRecord {
  type: 'record'
  at: string
  lot_no: string
  lot_id: number
  equipment: string
  equipment_id: number
  operator: string | null
  produced: number
  defects: number
  rate: number
  top_type: string | null
  values: Record<string, number>
  limits: Record<string, [number | null, number | null, boolean]>
  units: Record<string, string | null>
  out_of_range: string[]
  violations: { id: number; characteristic: string; characteristic_id: number; rule_no: number }[]
  drift_condition: string | null
  suspected: number
  route: { step: string; equipment: string; equipment_id: number; main: boolean; inspection: boolean; start: string; end: string;
    conditions: Record<string, { value: number; unit: string | null; min: number | null; max: number | null; learned: boolean; out_of_range: boolean }>; out_of_range: string[] }[]
  suspects: { id: number; stage: string; source: string; qty: number | null; detail: string }[]
  inserts: { table: string; key: string; summary: string }[]
}
export interface LiveAlarm { type: 'alarm'; at: string; lot_no: string; equipment: string; text: string }
export interface LiveEquipmentEvent { type: 'equipment_event'; at: string; kind: string; equipment: string; equipment_id: number; note: string | null }
export interface LiveStatus {
  running: boolean; equipment_id: number | null; equipment: string | null; scenario: string; interval_sec: number
  ticks: number; sim_time: string | null; phase: string; message: string | null; drift_condition: string | null
  scenarios: Record<string, string>
  speeds: Record<string, string>
  clock: 'real' | 'continue'
  speed: number
  sim_now: string | null
  step_hours: number | null
  route: { step: string; equipment: string | null; equipment_id: number | null; main: boolean; inspection: boolean }[]
  /** 이 서버에서 시연을 켤 수 있는지 (운영 서버는 설정으로 끔) */
  enabled?: boolean
  /** DB 에 남아 있는 시연 기록 수 */
  simulated?: { lots: number; equipment_events: number }
}
export interface LiveProgress {
  type: 'progress'; at: string; window_start: string; window_end: string; fraction: number; produced: number; planned: number
  equipment: string; equipment_id: number; values: Record<string, number>; limits: Record<string, [number | null, number | null, boolean]>
  units: Record<string, string | null>; out_of_range: string[]; drift_condition: string | null
}
export interface LiveReview { type: 'review'; suspect_id: number; lot_id: number; lot_no: string; status: 'CONFIRMED' | 'REJECTED'; qty: number; at: string }
export type LiveMessage = LiveRecord | LiveAlarm | LiveEquipmentEvent | LiveReview | LiveProgress | ({ type: 'status' } & LiveStatus)

interface LiveState {
  connected: boolean
  status: LiveStatus | null
  records: LiveRecord[]
  alarms: (LiveAlarm | LiveEquipmentEvent)[]
  progress: LiveProgress | null
  sensors: LiveProgress[]
  subscribe: (fn: (m: LiveMessage) => void) => () => void
}

const LiveContext = createContext<LiveState>({ connected: false, status: null, records: [], alarms: [], progress: null, sensors: [], subscribe: () => () => undefined })
const KEEP = 60

export function LiveProvider({ children }: { children: ReactNode }) {
  const [connected, setConnected] = useState(false)
  const [status, setStatus] = useState<LiveStatus | null>(null)
  const [records, setRecords] = useState<LiveRecord[]>([])
  const [alarms, setAlarms] = useState<(LiveAlarm | LiveEquipmentEvent)[]>([])
  const [progress, setProgress] = useState<LiveProgress | null>(null)
  const [sensors, setSensors] = useState<LiveProgress[]>([])
  const listeners = useRef(new Set<(m: LiveMessage) => void>())

  useEffect(() => {
    const es = new EventSource('/api/live/stream', { withCredentials: true })
    const handle = (e: MessageEvent) => {
      let m: LiveMessage
      try { m = JSON.parse(e.data) } catch { return }
      if (m.type === 'status') { setStatus(m); if (!m.running) setProgress(null) }
      else if (m.type === 'progress') { setProgress(m); setSensors((x) => [...x.slice(-59), m]) }
      else if (m.type === 'record') {
        setRecords((r) => [...r.slice(-(KEEP - 1)), m as LiveRecord])
        setProgress(null)
        setStatus((s) => s && { ...s, ticks: s.ticks + 1, sim_time: m.at, simulated: s.simulated && { ...s.simulated, lots: s.simulated.lots + 1 }, phase: (m as LiveRecord).out_of_range.length || (m as LiveRecord).violations.length ? '이상' : '정상' })
      } else if (m.type === 'alarm' || m.type === 'equipment_event') {
        setAlarms((a) => [m as LiveAlarm, ...a].slice(0, 30))
        if (m.type === 'equipment_event') setStatus((s) => s && { ...s, phase: '정비' })
      }
      listeners.current.forEach((fn) => fn(m))
    }
    for (const t of ['status', 'record', 'alarm', 'equipment_event', 'review', 'progress']) es.addEventListener(t, handle as EventListener)
    es.onopen = () => setConnected(true)
    es.onerror = () => setConnected(false)
    return () => es.close()
  }, [])

  const subscribe = useCallback((fn: (m: LiveMessage) => void) => {
    listeners.current.add(fn)
    return () => { listeners.current.delete(fn) }
  }, [])
  return <LiveContext.Provider value={{ connected, status, records, alarms, progress, sensors, subscribe }}>{children}</LiveContext.Provider>
}

export function useLive() {
  return useContext(LiveContext)
}

/** 실시간 기록이 들어오면 콜백 (화면 갱신용, 너무 잦지 않게 묶음). */
export function useLiveRefresh(fn: () => void, delayMs = 800) {
  const { subscribe } = useLive()
  const ref = useRef(fn)
  ref.current = fn
  useEffect(() => {
    let t: number | undefined
    const off = subscribe((m) => {
      if (m.type !== 'record' && m.type !== 'equipment_event' && m.type !== 'review') return
      window.clearTimeout(t)
      t = window.setTimeout(() => ref.current(), delayMs)
    })
    return () => { off(); window.clearTimeout(t) }
  }, [subscribe, delayMs])
}

import { useEffect, useState } from 'react'
import { api, errorText } from '../api/client'
import type { User } from '../api/types'
import { useAuth } from '../auth'
import { Empty, Field, Loading, PageHead, Panel } from '../components/ui'
import { fmtDateTime, ROLE_LABEL } from '../lib/format'
import { useAsync } from '../lib/hooks'

type Settings = Record<string, Record<string, unknown>>

// 설정 키별 한국어 라벨. 여기에 없는 키도 그대로 편집할 수 있다.
const LABELS: Record<string, [string, Record<string, string>]> = {
  cause_weights: ['원인 점수 가중치', { spc: '(a) SPC 신호', lift: '(b) 공유 불량률', arima: '(c) ARIMA 위험', condition: '(d) 작업 조건 이탈', design: '(e) 설계 불일치', history: '(f) 과거 확정 이력' }],
  cause_scoring: ['원인 분석 기준', {
    analysis_window_days: '분석 기간 (±일)', downstream_window_days: '설비/공정 영향 범위 (±일)', beta_prior_alpha: 'Lift 평활 α', beta_prior_beta: 'Lift 평활 β',
    top_n_badges: '그래프 순위 배지 수', max_trace_depth: '최대 추적 깊이', lift_saturation: 'Lift 만점 기준', spc_rule_saturation: 'SPC 만점 룰 수',
    history_saturation: '확정 이력 만점 기준', history_type_weight: '같은 부품 종류 확정 가중치', design_ratio_tolerance: '설계 비율 기본 허용오차',
    common_share_threshold: '공통 경로 기준 비율' }],
  forecast: ['ARIMA 예측', {
    min_points: '최소 데이터 수', horizon: '예측 스텝 수', max_p: '최대 p', max_d: '최대 d', max_q: '최대 q', adf_alpha: 'ADF 유의수준',
    schedule_cron_hour: '배치 실행 시각 (시)', interval_alpha: '예측 구간 α', max_points: '적합 최대 포인트', breach_probability: '예상 이탈 확률 기준',
    trend_window: '추세 판단 포인트', flat_threshold_sigma: '평탄 판단 (σ 배수)' }],
  control_chart: ['관리도', { imr_max_n: 'I-MR 최대 n', xbar_r_max_n: 'Xbar-R 최대 n', min_baseline_subgroups: '기준 구간 최소 서브그룹', recompute_interval_minutes: 'SPC 재계산 주기 (분)', min_regime_subgroups: '체제별 기준선 최소 데이터' }],
  trace: ['계보 추적', { default_depth: '기본 깊이', max_depth: '최대 깊이', max_nodes: '최대 노드 수' }],
}

export function SettingsPage() {
  const { can } = useAuth()
  const s = useAsync(() => api.get<Settings>('/settings'), [])
  const [tab, setTab] = useState<'settings' | 'rules' | 'users' | 'audit'>('settings')
  return (
    <>
      <PageHead title="설정" sub="설정값은 DB 에 저장되며 변경 이력이 감사 로그에 남습니다." />
      <div className="row" style={{ marginBottom: 12, gap: 4 }}>
        <button className={tab === 'settings' ? 'primary' : ''} onClick={() => setTab('settings')}>분석 설정</button>
        <button className={tab === 'rules' ? 'primary' : ''} onClick={() => setTab('rules')}>넬슨룰 전역 기본값</button>
        {can('ADMIN') && <button className={tab === 'users' ? 'primary' : ''} onClick={() => setTab('users')}>사용자</button>}
        {can('ADMIN') && <button className={tab === 'audit' ? 'primary' : ''} onClick={() => setTab('audit')}>감사 로그</button>}
      </div>
      {!can('ADMIN') && tab !== 'users' && <div className="notice" style={{ marginBottom: 12 }}>조회만 가능합니다. 변경은 관리자 권한이 필요합니다.</div>}
      <Loading error={s.error} loading={s.loading && !s.data} />
      {s.data && tab === 'settings' && (
        <div className="grid cols-3" style={{ alignItems: 'start' }}>
          {Object.keys(LABELS).map((k) => s.data![k] && <SettingForm key={k} name={k} value={s.data![k]} editable={can('ADMIN')} onSaved={s.reload} />)}
        </div>
      )}
      {s.data && tab === 'rules' && <GlobalRules value={s.data.nelson_rules as Record<string, { enabled: boolean; params: Record<string, number> }>} editable={can('ADMIN')} onSaved={s.reload} />}
      {tab === 'users' && can('ADMIN') && <Users />}
      {tab === 'audit' && can('ADMIN') && <Audit />}
    </>
  )
}

function SettingForm({ name, value, editable, onSaved }: { name: string; value: Record<string, unknown>; editable: boolean; onSaved: () => void }) {
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [msg, setMsg] = useState<string | null>(null)
  useEffect(() => setDraft(Object.fromEntries(Object.entries(value).map(([k, v]) => [k, String(v)]))), [value])
  const [title, labels] = LABELS[name] ?? [name, {}]
  const save = async () => {
    const body: Record<string, number> = {}
    for (const [k, v] of Object.entries(draft)) {
      const n = Number(v)
      if (v === '' || Number.isNaN(n)) return setMsg(`'${labels[k] ?? k}' 는 숫자여야 합니다.`)
      body[k] = n
    }
    try {
      await api.put(`/settings/${name}`, body)
      setMsg('저장했습니다.'); onSaved()
    } catch (e) { setMsg(errorText(e)) }
  }
  return (
    <Panel title={title} actions={editable && <button className="small primary" onClick={save}>저장</button>}>
      <table className="t">
        <tbody>
          {Object.keys(draft).map((k) => (
            <tr key={k}>
              <td title={k}>{labels[k] ?? k}</td>
              <td style={{ width: 110 }}><input type="number" step="any" disabled={!editable} value={draft[k]} style={{ width: '100%', height: 26 }} onChange={(e) => setDraft({ ...draft, [k]: e.target.value })} /></td>
            </tr>
          ))}
        </tbody>
      </table>
      {msg && <div className="notice" style={{ marginTop: 8 }}>{msg}</div>}
    </Panel>
  )
}

const RULE_TEXT: Record<string, string> = {
  '1': '1점이 kσ 밖', '2': '연속 n점이 중심선 한쪽', '3': '연속 n점 증가/감소', '4': '연속 n점 번갈아 증감',
  '5': 'window 중 min_points 점이 같은 쪽 kσ 밖', '6': 'window 중 min_points 점이 같은 쪽 kσ 밖', '7': '연속 n점이 ±kσ 안', '8': '연속 n점이 ±kσ 밖 (양쪽)',
}

function GlobalRules({ value, editable, onSaved }: { value: Record<string, { enabled: boolean; params: Record<string, number> }>; editable: boolean; onSaved: () => void }) {
  const [draft, setDraft] = useState(value)
  const [msg, setMsg] = useState<string | null>(null)
  useEffect(() => setDraft(value), [value])
  const save = async () => {
    try { await api.put('/settings/nelson_rules', draft); setMsg('저장했습니다. 특성별 설정이 없는 항목에 적용됩니다.'); onSaved() } catch (e) { setMsg(errorText(e)) }
  }
  return (
    <Panel title="넬슨룰 전역 기본값" actions={editable && <button className="small primary" onClick={save}>저장</button>}>
      <table className="t">
        <thead><tr><th style={{ width: 80 }}>룰</th><th>내용</th><th style={{ width: 320 }}>파라미터</th></tr></thead>
        <tbody>
          {Object.entries(draft).map(([k, r]) => (
            <tr key={k}>
              <td><label className="check"><input type="checkbox" disabled={!editable} checked={r.enabled} onChange={(e) => setDraft({ ...draft, [k]: { ...r, enabled: e.target.checked } })} />R{k}</label></td>
              <td>{RULE_TEXT[k]}</td>
              <td><div className="row">{Object.entries(r.params).map(([p, v]) => (
                <label key={p} className="check" style={{ fontSize: 12 }}>{p}<input type="number" step="any" disabled={!editable} value={v} style={{ width: 56, height: 24 }}
                  onChange={(e) => setDraft({ ...draft, [k]: { ...r, params: { ...r.params, [p]: Number(e.target.value) } } })} /></label>
              ))}</div></td>
            </tr>
          ))}
        </tbody>
      </table>
      {msg && <div className="notice" style={{ marginTop: 8, whiteSpace: 'pre-wrap' }}>{msg}</div>}
    </Panel>
  )
}

function Users() {
  const users = useAsync(() => api.get<User[]>('/users'), [])
  const [form, setForm] = useState({ username: '', password: '', display_name: '', role: 'VIEWER' })
  const [msg, setMsg] = useState<string | null>(null)
  const add = async () => {
    try { await api.post('/users', form); setForm({ username: '', password: '', display_name: '', role: 'VIEWER' }); setMsg('추가했습니다.'); users.reload() } catch (e) { setMsg(errorText(e)) }
  }
  const update = async (u: User, patch: Record<string, unknown>) => {
    try { await api.put(`/users/${u.id}`, patch); users.reload() } catch (e) { setMsg(errorText(e)) }
  }
  return (
    <div className="split-right">
      <Panel title="사용자" flush>
        {msg && <div className="notice" style={{ margin: 8, whiteSpace: 'pre-wrap' }}>{msg}</div>}
        <table className="t">
          <thead><tr><th>아이디</th><th>이름</th><th style={{ width: 120 }}>역할</th><th style={{ width: 80 }}>상태</th><th style={{ width: 200 }} /></tr></thead>
          <tbody>
            {(users.data ?? []).map((u) => (
              <tr key={u.id}>
                <td>{u.username}</td><td>{u.display_name ?? '-'}</td>
                <td><select value={u.role} onChange={(e) => update(u, { role: e.target.value })} style={{ height: 26 }}>
                  {Object.entries(ROLE_LABEL).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select></td>
                <td>{u.is_active ? '사용' : <span className="faint">중지</span>}</td>
                <td><div className="row">
                  <button className="small" onClick={() => update(u, { is_active: !u.is_active })}>{u.is_active ? '중지' : '다시 사용'}</button>
                  <button className="small" onClick={() => { const p = prompt(`${u.username} 의 새 비밀번호 (6자 이상)`); if (p) update(u, { password: p }) }}>비밀번호 재설정</button>
                </div></td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
      <Panel title="사용자 추가">
        <div className="stack" style={{ gap: 8 }}>
          <Field label="아이디"><input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} /></Field>
          <Field label="이름"><input value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} /></Field>
          <Field label="초기 비밀번호 (6자 이상)"><input type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></Field>
          <Field label="역할"><select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
            {Object.entries(ROLE_LABEL).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select></Field>
          <div className="faint" style={{ fontSize: 12 }}>조회자: 보기만 · 실무자: 불량 판정, 원인 확정, 보류/격리, 업로드 · 관리자: 마스터, 룰, 관리한계, 가중치, 사용자</div>
          <button className="primary" onClick={add} disabled={!form.username || form.password.length < 6}>추가</button>
        </div>
      </Panel>
    </div>
  )
}

interface AuditRow { id: number; at: string; user: string | null; action: string; entity: string; entity_id: string | null; before: unknown; after: unknown }
const ACTION_LABEL: Record<string, string> = {
  create: '생성', update: '수정', delete: '삭제', judge: '불량 판정', confirm_root_cause: '원인 확정', cancel_judgment: '판정 취소',
  recalc_limits: '관리한계 재설정', bulk_status: '상태 일괄 변경', acknowledge: '위반 확인', ingest: '데이터 적재', attach: '첨부',
}

function Audit() {
  const [entity, setEntity] = useState('')
  const rows = useAsync(() => api.get<AuditRow[]>('/audit', { entity, limit: 300 }), [entity])
  const [open, setOpen] = useState<number | null>(null)
  return (
    <Panel title="감사 로그" flush actions={
      <select value={entity} onChange={(e) => setEntity(e.target.value)} style={{ height: 26 }}>
        <option value="">전체</option>
        {['defect_judgment', 'root_cause_confirmation', 'control_limit', 'setting', 'characteristic', 'lot', 'user', 'spc_violation'].map((x) => <option key={x} value={x}>{x}</option>)}
      </select>}>
      <Loading error={rows.error} loading={rows.loading && !rows.data} />
      <div className="table-wrap" style={{ maxHeight: 'calc(100vh - 260px)' }}>
        <table className="t">
          <thead><tr><th style={{ width: 130 }}>시각</th><th style={{ width: 100 }}>사용자</th><th style={{ width: 120 }}>작업</th><th style={{ width: 180 }}>대상</th><th>내용</th></tr></thead>
          <tbody>
            {(rows.data ?? []).map((r) => (
              <tr key={r.id} className="clickable" onClick={() => setOpen(open === r.id ? null : r.id)} style={{ height: open === r.id ? 'auto' : undefined }}>
                <td>{fmtDateTime(r.at)}</td><td>{r.user ?? '-'}</td><td>{ACTION_LABEL[r.action] ?? r.action}</td>
                <td>{r.entity}{r.entity_id ? ` #${r.entity_id}` : ''}</td>
                <td style={{ whiteSpace: open === r.id ? 'pre-wrap' : 'nowrap', fontSize: 12 }} className="mono">
                  {open === r.id ? `이전: ${JSON.stringify(r.before, null, 1)}\n이후: ${JSON.stringify(r.after, null, 1)}` : JSON.stringify(r.after)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.data && !rows.data.length && <Empty>기록이 없습니다.</Empty>}
      </div>
    </Panel>
  )
}

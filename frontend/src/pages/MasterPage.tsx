import { useMemo, useState } from 'react'
import { api, errorText } from '../api/client'
import type { ItemType, ProcessStep } from '../api/types'
import { useAuth } from '../auth'
import { Empty, Field, Loading, PageHead, Panel } from '../components/ui'
import { CATEGORY_LABEL, CHART_LABEL } from '../lib/format'
import { useAsync } from '../lib/hooks'
import { useTerms } from '../lib/terms'

type Kind = 'text' | 'number' | 'bool' | 'select' | 'textarea'
interface Col { key: string; label: string; kind: Kind; options?: () => { value: string | number; label: string }[]; required?: boolean; width?: number; nullable?: boolean }
interface Tab { key: string; label: string; path: string; cols: Col[]; defaults: Record<string, unknown> }

type Row = Record<string, unknown> & { id: number }

export function MasterPage() {
  const { can } = useAuth()
  const { t } = useTerms()
  const items = useAsync(() => api.get<ItemType[]>('/item-types'), [])
  const steps = useAsync(() => api.get<ProcessStep[]>('/process-steps'), [])
  const itemOpts = () => (items.data ?? []).map((i) => ({ value: i.id, label: i.name }))
  const stepOpts = () => (steps.data ?? []).map((s) => ({ value: s.id, label: s.name }))

  const tabs: Tab[] = useMemo(() => [
    { key: 'item', label: '부품 종류', path: '/item-types', defaults: { category: 'RAW', is_active: true }, cols: [
      { key: 'name', label: '이름', kind: 'text', required: true },
      { key: 'category', label: '분류', kind: 'select', options: () => Object.entries(CATEGORY_LABEL).map(([v, l]) => ({ value: v, label: l })), width: 90 },
      { key: 'description', label: '설명', kind: 'text', nullable: true },
      { key: 'is_active', label: '사용', kind: 'bool', width: 60 },
    ] },
    { key: 'step', label: '공정 단계', path: '/process-steps', defaults: { seq: 10, is_active: true }, cols: [
      { key: 'name', label: '이름', kind: 'text', required: true },
      { key: 'seq', label: '순서', kind: 'number', width: 70 },
      { key: 'description', label: '설명', kind: 'text', nullable: true },
      { key: 'is_active', label: '사용', kind: 'bool', width: 60 },
    ] },
    { key: 'eq', label: t('equipment'), path: '/equipment', defaults: { is_active: true }, cols: [
      { key: 'name', label: '이름', kind: 'text', required: true },
      { key: 'process_step_id', label: `소속 ${t('step')}`, kind: 'select', options: stepOpts, nullable: true },
      { key: 'pos_x', label: 'X', kind: 'number', nullable: true, width: 60 },
      { key: 'pos_y', label: 'Y', kind: 'number', nullable: true, width: 60 },
      { key: 'pos_z', label: 'Z', kind: 'number', nullable: true, width: 60 },
      { key: 'model_path', label: '3D 모델 경로', kind: 'text', nullable: true },
      { key: 'is_active', label: '사용', kind: 'bool', width: 60 },
    ] },
    { key: 'cond', label: `${t('condition')} 항목`, path: '/condition-definitions', defaults: { is_active: true }, cols: [
      { key: 'name', label: '이름', kind: 'text', required: true },
      { key: 'unit', label: '단위', kind: 'text', nullable: true, width: 70 },
      { key: 'min_value', label: '허용 하한', kind: 'number', nullable: true, width: 90 },
      { key: 'max_value', label: '허용 상한', kind: 'number', nullable: true, width: 90 },
      { key: 'value_kind', label: '값 종류', kind: 'select', options: () => [{ value: 'measured', label: '측정값' }, { value: 'setpoint', label: '설정값' }], nullable: true, width: 80 },
      { key: 'process_step_id', label: '공정', kind: 'select', options: stepOpts, nullable: true },
      { key: 'is_active', label: '사용', kind: 'bool', width: 60 },
    ] },
    { key: 'char', label: t('characteristic'), path: '/characteristics', defaults: { subgroup_size: 1, chart_type: 'AUTO', baseline_scope: 'GLOBAL', is_active: true }, cols: [
      { key: 'name', label: '이름', kind: 'text', required: true },
      { key: 'unit', label: '단위', kind: 'text', nullable: true, width: 60 },
      { key: 'item_type_id', label: '부품 종류', kind: 'select', options: itemOpts, nullable: true },
      { key: 'process_step_id', label: '또는 공정', kind: 'select', options: stepOpts, nullable: true },
      { key: 'target', label: 'Target', kind: 'number', nullable: true, width: 70 },
      { key: 'lsl', label: 'LSL', kind: 'number', nullable: true, width: 70 },
      { key: 'usl', label: 'USL', kind: 'number', nullable: true, width: 70 },
      { key: 'subgroup_size', label: 'n', kind: 'number', width: 50 },
      { key: 'chart_type', label: '관리도', kind: 'select', options: () => Object.entries(CHART_LABEL).map(([v, l]) => ({ value: v, label: l })), width: 80 },
      { key: 'baseline_scope', label: '기준선', kind: 'select', options: () => [{ value: 'GLOBAL', label: '전체' }, { value: 'PARENT_LOT', label: '상위 LOT별' }], width: 90 },
      { key: 'is_active', label: '사용', kind: 'bool', width: 50 },
    ] },
    { key: 'dtype', label: '불량 유형', path: '/defect-types', defaults: { is_active: true }, cols: [
      { key: 'code', label: '코드', kind: 'text', required: true, width: 140 },
      { key: 'name', label: '이름', kind: 'text', required: true },
      { key: 'description', label: '설명', kind: 'text', nullable: true },
      { key: 'is_active', label: '사용', kind: 'bool', width: 60 },
    ] },
  // eslint-disable-next-line react-hooks/exhaustive-deps
  ], [items.data, steps.data, t])

  const [tabKey, setTabKey] = useState('item')
  const tab = tabs.find((t) => t.key === tabKey)!
  return (
    <>
      <PageHead title="마스터 관리" sub="부품 종류·공정·설비·조건·특성·불량 유형을 현장에 맞게 추가·수정합니다. 변경은 모두 감사 로그에 남습니다." />
      <div className="row" style={{ marginBottom: 12, gap: 4 }}>
        {tabs.map((t) => <button key={t.key} className={t.key === tabKey ? 'primary' : ''} onClick={() => setTabKey(t.key)}>{t.label}</button>)}
        <button className={tabKey === 'design' ? 'primary' : ''} onClick={() => setTabKey('design')}>설계 정보</button>
      </div>
      {!can('ADMIN') && <div className="notice" style={{ marginBottom: 12 }}>조회만 가능합니다. 변경은 관리자 권한이 필요합니다.</div>}
      {tabKey === 'design'
        ? <DesignEditor items={items.data ?? []} steps={steps.data ?? []} editable={can('ADMIN')} />
        : <MasterTable key={tab.key} tab={tab} editable={can('ADMIN')} onChanged={() => { items.reload(); steps.reload() }} />}
    </>
  )
}

function display(col: Col, v: unknown): string {
  if (v === null || v === undefined || v === '') return '-'
  if (col.kind === 'bool') return v ? '사용' : '중지'
  if (col.kind === 'select') return col.options?.().find((o) => o.value === v)?.label ?? String(v)
  return String(v)
}

function MasterTable({ tab, editable, onChanged }: { tab: Tab; editable: boolean; onChanged: () => void }) {
  const rows = useAsync(() => api.get<Row[]>(tab.path), [tab.path])
  const [edit, setEdit] = useState<Record<string, unknown> | null>(null)
  const [msg, setMsg] = useState<string | null>(null)

  const save = async () => {
    if (!edit) return
    const body: Record<string, unknown> = { ...tab.defaults }
    for (const c of tab.cols) {
      let v = edit[c.key]
      if (c.kind === 'number') v = v === '' || v === null || v === undefined ? null : Number(v)
      if (c.kind === 'select' && v === '') v = null
      if (c.nullable && v === '') v = null
      body[c.key] = v ?? (c.kind === 'bool' ? false : null)
    }
    for (const k of ['rule_config']) if (k in edit) body[k] = edit[k]
    try {
      if (edit.id) await api.put(`${tab.path}/${edit.id}`, body)
      else await api.post(tab.path, body)
      setEdit(null); setMsg('저장했습니다.')
      rows.reload(); onChanged()
    } catch (e) {
      setMsg(errorText(e))
    }
  }
  const remove = async (r: Row) => {
    if (!confirm('삭제합니다. 다른 데이터가 쓰고 있으면 삭제되지 않으니 대신 "중지"로 바꾸세요. 계속할까요?')) return
    try {
      await api.del(`${tab.path}/${r.id}`)
      rows.reload(); onChanged()
    } catch (e) {
      setMsg(errorText(e))
    }
  }

  return (
    <div className="split-right" style={{ gridTemplateColumns: edit ? 'minmax(0,1fr) 380px' : 'minmax(0,1fr)' }}>
      <Panel title={`${tab.label} (${rows.data?.length ?? 0})`} flush
        actions={editable && <button className="small primary" onClick={() => setEdit({ ...tab.defaults })}>추가</button>}>
        <Loading error={rows.error} loading={rows.loading && !rows.data} />
        {msg && <div className="notice" style={{ margin: 8, whiteSpace: 'pre-wrap' }}>{msg}</div>}
        <div className="table-wrap" style={{ maxHeight: 'calc(100vh - 250px)' }}>
          <table className="t">
            <thead><tr>{tab.cols.map((c) => <th key={c.key} style={{ width: c.width }}>{c.label}</th>)}{editable && <th style={{ width: 110 }} />}</tr></thead>
            <tbody>
              {(rows.data ?? []).map((r) => (
                <tr key={r.id} className={edit?.id === r.id ? 'selected' : ''}>
                  {tab.cols.map((c) => <td key={c.key} title={display(c, r[c.key])} className={c.kind === 'bool' && !r[c.key] ? 'faint' : ''}>{display(c, r[c.key])}</td>)}
                  {editable && <td><div className="row"><button className="small" onClick={() => setEdit({ ...r })}>수정</button><button className="small danger" onClick={() => remove(r)}>삭제</button></div></td>}
                </tr>
              ))}
            </tbody>
          </table>
          {rows.data && !rows.data.length && <Empty>등록된 항목이 없습니다.</Empty>}
        </div>
      </Panel>
      {edit && (
        <Panel title={edit.id ? `${tab.label} 수정` : `${tab.label} 추가`} actions={<button className="small" onClick={() => setEdit(null)}>닫기</button>}>
          <div className="stack" style={{ gap: 8 }}>
            {tab.cols.map((c) => (
              <Field key={c.key} label={`${c.label}${c.required ? ' *' : ''}`}>
                {c.kind === 'bool' ? (
                  <label className="check"><input type="checkbox" checked={!!edit[c.key]} onChange={(e) => setEdit({ ...edit, [c.key]: e.target.checked })} />사용</label>
                ) : c.kind === 'select' ? (
                  <select value={(edit[c.key] as string | number | null) ?? ''} onChange={(e) => {
                    const raw = e.target.value
                    const opt = c.options?.().find((o) => String(o.value) === raw)
                    setEdit({ ...edit, [c.key]: raw === '' ? null : opt ? opt.value : raw })
                  }}>
                    {c.nullable && <option value="">(없음)</option>}
                    {c.options?.().map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                ) : (
                  <input type={c.kind === 'number' ? 'number' : 'text'} step="any" value={(edit[c.key] as string | number | null) ?? ''}
                    onChange={(e) => setEdit({ ...edit, [c.key]: e.target.value })} />
                )}
              </Field>
            ))}
            {tab.key === 'char' && <div className="faint" style={{ fontSize: 12 }}>부품 종류와 공정 중 하나만 지정합니다. 넬슨룰 On/Off·파라미터는 관리도 화면에서 설정합니다.</div>}
            <button className="primary" onClick={save}>저장</button>
          </div>
        </Panel>
      )}
    </div>
  )
}

interface Design {
  id?: number; name: string; output_item_type_id: number | ''; version: number; description: string | null; is_active: boolean
  components: { item_type_id: number | ''; ratio: number | null; qty: number | null; tolerance: number | null }[]
  steps: { process_step_id: number | ''; seq: number }[]
}

function DesignEditor({ items, steps, editable }: { items: ItemType[]; steps: ProcessStep[]; editable: boolean }) {
  const designs = useAsync(() => api.get<Design[]>('/product-designs'), [])
  const [edit, setEdit] = useState<Design | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const name = (id: number | '', list: { id: number; name: string }[]) => list.find((x) => x.id === id)?.name ?? '-'
  const save = async () => {
    if (!edit) return
    const body = { ...edit, components: edit.components.filter((c) => c.item_type_id !== ''), steps: edit.steps.filter((s) => s.process_step_id !== '') }
    try {
      if (edit.id) await api.put(`/product-designs/${edit.id}`, body)
      else await api.post('/product-designs', body)
      setEdit(null); setMsg('저장했습니다.'); designs.reload()
    } catch (e) { setMsg(errorText(e)) }
  }
  return (
    <div className="split-right" style={{ gridTemplateColumns: edit ? 'minmax(0,1fr) 460px' : 'minmax(0,1fr)' }}>
      <Panel title="설계 정보 (기대 투입 구조)" flush actions={editable && <button className="small primary" onClick={() => setEdit({ name: '', output_item_type_id: '', version: 1, description: null, is_active: true, components: [], steps: [] })}>추가</button>}>
        {msg && <div className="notice" style={{ margin: 8 }}>{msg}</div>}
        <table className="t">
          <thead><tr><th>이름</th><th>출력 부품</th><th style={{ width: 50 }}>버전</th><th>투입 구성 (비율 ± 허용)</th><th>공정 순서</th>{editable && <th style={{ width: 70 }} />}</tr></thead>
          <tbody>
            {(designs.data ?? []).map((d) => (
              <tr key={d.id}>
                <td>{d.name}</td><td>{name(d.output_item_type_id, items)}</td><td>{d.version}</td>
                <td title="">{d.components.map((c) => `${name(c.item_type_id, items)} ${c.ratio ?? '-'}${c.tolerance != null ? `±${c.tolerance}` : ''}`).join(', ')}</td>
                <td>{d.steps.map((s) => name(s.process_step_id, steps)).join(' → ')}</td>
                {editable && <td><button className="small" onClick={() => setEdit(JSON.parse(JSON.stringify(d)))}>수정</button></td>}
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
      {edit && (
        <Panel title={edit.id ? '설계 수정' : '설계 추가'} actions={<button className="small" onClick={() => setEdit(null)}>닫기</button>}>
          <div className="stack" style={{ gap: 8 }}>
            <div className="form-grid">
              <Field label="이름"><input value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
              <Field label="출력 부품 종류">
                <select value={edit.output_item_type_id} onChange={(e) => setEdit({ ...edit, output_item_type_id: Number(e.target.value) })}>
                  <option value="">선택</option>{items.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
                </select>
              </Field>
            </div>
            <b>투입 구성</b>
            {edit.components.map((c, i) => (
              <div key={i} className="row">
                <select value={c.item_type_id} style={{ flex: 1 }} onChange={(e) => { const cs = [...edit.components]; cs[i] = { ...c, item_type_id: Number(e.target.value) }; setEdit({ ...edit, components: cs }) }}>
                  <option value="">부품 선택</option>{items.map((it) => <option key={it.id} value={it.id}>{it.name}</option>)}
                </select>
                <input type="number" step="any" placeholder="비율" value={c.ratio ?? ''} style={{ width: 70 }} onChange={(e) => { const cs = [...edit.components]; cs[i] = { ...c, ratio: e.target.value === '' ? null : Number(e.target.value) }; setEdit({ ...edit, components: cs }) }} />
                <input type="number" step="any" placeholder="허용" value={c.tolerance ?? ''} style={{ width: 64 }} onChange={(e) => { const cs = [...edit.components]; cs[i] = { ...c, tolerance: e.target.value === '' ? null : Number(e.target.value) }; setEdit({ ...edit, components: cs }) }} />
                <button className="small" onClick={() => setEdit({ ...edit, components: edit.components.filter((_, j) => j !== i) })}>빼기</button>
              </div>
            ))}
            <button className="small" onClick={() => setEdit({ ...edit, components: [...edit.components, { item_type_id: '', ratio: null, qty: null, tolerance: null }] })}>구성 추가</button>
            <b>공정 순서</b>
            {edit.steps.map((s, i) => (
              <div key={i} className="row">
                <span style={{ width: 20 }}>{i + 1}</span>
                <select value={s.process_step_id} style={{ flex: 1 }} onChange={(e) => { const ss = [...edit.steps]; ss[i] = { process_step_id: Number(e.target.value), seq: i + 1 }; setEdit({ ...edit, steps: ss }) }}>
                  <option value="">공정 선택</option>{steps.map((st) => <option key={st.id} value={st.id}>{st.name}</option>)}
                </select>
                <button className="small" onClick={() => setEdit({ ...edit, steps: edit.steps.filter((_, j) => j !== i).map((x, k) => ({ ...x, seq: k + 1 })) })}>빼기</button>
              </div>
            ))}
            <button className="small" onClick={() => setEdit({ ...edit, steps: [...edit.steps, { process_step_id: '', seq: edit.steps.length + 1 }] })}>공정 추가</button>
            <button className="primary" onClick={save}>저장</button>
          </div>
        </Panel>
      )}
    </div>
  )
}

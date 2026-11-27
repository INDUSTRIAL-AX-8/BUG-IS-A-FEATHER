import { useMemo, useState } from 'react'
import { api, errorText } from '../api/client'
import { useAuth } from '../auth'
import { Empty, Field, Loading, PageHead, Panel } from '../components/ui'
import { useAsync } from '../lib/hooks'

interface TargetField { name: string; label: string; kind: string; required: boolean }
interface Target { target: string; label: string; fields: TargetField[]; dynamic: { name: string; label: string }[] }
interface UploadInfo { upload_id: string; filename: string; columns: string[]; row_count: number; sample: Record<string, string>[] }
interface Validation { target: string; total: number; valid: number; invalid: number; errors: { row: number; messages: string[] }[]; preview: Record<string, unknown>[]; errors_url: string | null }
interface Template { id: number; name: string; target: string; column_map: Record<string, string> }

export function UploadPage() {
  const { can } = useAuth()
  const targets = useAsync(() => api.get<Target[]>('/ingest/targets'), [])
  const [target, setTarget] = useState('lots')
  const templates = useAsync(() => api.get<Template[]>('/mapping-templates', { target }), [target])
  const [info, setInfo] = useState<UploadInfo | null>(null)
  const [mapping, setMapping] = useState<Record<string, string>>({})
  const [result, setResult] = useState<Validation | null>(null)
  const [committed, setCommitted] = useState<{ inserted: number; invalid: number; errors_url: string | null } | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [tplName, setTplName] = useState('')
  const [dragCol, setDragCol] = useState<string | null>(null)

  const spec = targets.data?.find((t) => t.target === target)
  const fields: TargetField[] = useMemo(() => spec ? [...spec.fields, ...spec.dynamic.map((d) => ({ name: d.name, label: d.label, kind: 'float', required: false }))] : [], [spec])
  const used = new Set(Object.values(mapping))

  const onFile = async (f: File | undefined) => {
    if (!f) return
    setBusy(true); setMsg(null); setResult(null); setCommitted(null)
    const fd = new FormData()
    fd.append('file', f)
    try {
      const u = await api.post<UploadInfo>('/upload', fd)
      setInfo(u)
      // 같은 이름의 열은 자동 매핑
      const auto: Record<string, string> = {}
      for (const fl of fields) {
        const hit = u.columns.find((c) => c === fl.name || c === fl.label || c.toLowerCase() === fl.name.toLowerCase())
        if (hit) auto[fl.name] = hit
      }
      setMapping(auto)
    } catch (e) {
      setMsg(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  const run = async (kind: 'preview' | 'commit') => {
    if (!info) return
    setBusy(true); setMsg(null)
    try {
      const clean = Object.fromEntries(Object.entries(mapping).filter(([, v]) => v))
      if (kind === 'preview') {
        setResult(await api.post<Validation>(`/upload/${info.upload_id}/preview`, { target, mapping: clean }))
        setCommitted(null)
      } else {
        if (!confirm('검증을 통과한 행만 적재합니다. 계속할까요?')) return
        setCommitted(await api.post(`/upload/${info.upload_id}/commit`, { target, mapping: clean }))
      }
    } catch (e) {
      setMsg(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  const saveTemplate = async () => {
    if (!tplName.trim()) return setMsg('템플릿 이름을 입력하세요.')
    try {
      await api.post('/mapping-templates', { name: tplName.trim(), target, column_map: mapping })
      setMsg(`매핑 템플릿 '${tplName.trim()}' 을(를) 저장했습니다.`)
      templates.reload()
    } catch (e) {
      setMsg(errorText(e))
    }
  }

  return (
    <>
      <PageHead title="데이터 업로드" sub="CSV/Excel 을 올리고 파일의 열을 시스템 필드로 끌어다 놓아 매핑합니다. 검증을 통과한 행만 적재되고, 오류 행은 따로 내려받을 수 있습니다." />
      {!can('OPERATOR') && <div className="notice warn" style={{ marginBottom: 12 }}>조회자는 업로드할 수 없습니다.</div>}
      <Panel>
        <div className="form-row">
          <Field label="적재 대상">
            <select value={target} onChange={(e) => { setTarget(e.target.value); setMapping({}); setResult(null); setCommitted(null) }}>
              {(targets.data ?? []).map((t) => <option key={t.target} value={t.target}>{t.label}</option>)}
            </select>
          </Field>
          <Field label="파일 (.csv, .xlsx)"><input type="file" accept=".csv,.txt,.xlsx,.xlsm" disabled={!can('OPERATOR') || busy}
            onChange={(e) => onFile(e.target.files?.[0])} style={{ paddingTop: 4 }} /></Field>
          <Field label="매핑 템플릿 불러오기">
            <select value="" onChange={(e) => {
              const t = templates.data?.find((x) => String(x.id) === e.target.value)
              if (t) { setMapping(t.column_map); setTplName(t.name) }
            }}>
              <option value="">선택</option>
              {(templates.data ?? []).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </Field>
          {info && <span className="muted" style={{ alignSelf: 'center' }}>{info.filename} · {info.row_count.toLocaleString()}행 · 열 {info.columns.length}개</span>}
        </div>
        {msg && <div className="notice" style={{ marginTop: 10, whiteSpace: 'pre-wrap' }}>{msg}</div>}
      </Panel>

      <div style={{ height: 12 }} />
      {!info ? <Panel><Empty>파일을 선택하세요. 첫 행은 열 이름이어야 합니다.</Empty></Panel> : (
        <div className="split" style={{ gridTemplateColumns: '300px minmax(0, 1fr)' }}>
          <Panel title="파일의 열" flush>
            <div className="muted" style={{ fontSize: 12, padding: '8px 12px' }}>오른쪽 시스템 필드 칸으로 끌어다 놓으세요.</div>
            <div className="stack" style={{ gap: 4, padding: '0 12px 12px' }}>
              {info.columns.map((c) => (
                <div key={c} draggable onDragStart={() => setDragCol(c)} onDragEnd={() => setDragCol(null)}
                  className="badge neutral" style={{ height: 28, cursor: 'grab', justifyContent: 'space-between', fontWeight: 500, opacity: used.has(c) ? 0.55 : 1 }}
                  title={`예: ${info.sample.slice(0, 3).map((r) => r[c]).join(', ')}`}>
                  <span>{c}</span>{used.has(c) && <span className="faint">매핑됨</span>}
                </div>
              ))}
            </div>
          </Panel>
          <div className="stack">
            <Panel title={`시스템 필드 · ${spec?.label ?? ''}`}
              actions={<div className="row">
                <input placeholder="템플릿 이름" value={tplName} onChange={(e) => setTplName(e.target.value)} style={{ width: 160, height: 24 }} />
                <button className="small" onClick={saveTemplate} disabled={!can('OPERATOR')}>템플릿 저장</button>
              </div>}>
              <table className="t">
                <thead><tr><th style={{ width: 220 }}>시스템 필드</th><th>파일 열 (끌어다 놓기 또는 선택)</th><th style={{ width: 240 }}>예시 값</th></tr></thead>
                <tbody>
                  {fields.map((f) => (
                    <tr key={f.name} onDragOver={(e) => e.preventDefault()}
                      onDrop={() => { if (dragCol) setMapping({ ...mapping, [f.name]: dragCol }) }}
                      style={dragCol ? { outline: '1px dashed var(--border-strong)' } : undefined}>
                      <td>{f.label}{f.required && <b style={{ color: 'var(--danger)' }}> *</b>}</td>
                      <td>
                        <select value={mapping[f.name] ?? ''} onChange={(e) => setMapping({ ...mapping, [f.name]: e.target.value })} style={{ width: '100%', height: 26 }}>
                          <option value="">(매핑 안 함)</option>
                          {info.columns.map((c) => <option key={c} value={c}>{c}</option>)}
                        </select>
                      </td>
                      <td className="muted">{mapping[f.name] ? info.sample.slice(0, 3).map((r) => r[mapping[f.name]]).join(', ') : ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="row" style={{ marginTop: 10 }}>
                <button onClick={() => run('preview')} disabled={busy || !can('OPERATOR')}>미리보기·검증</button>
                <button className="primary" onClick={() => run('commit')} disabled={busy || !result || result.valid === 0 || !can('OPERATOR')}>검증 통과 행 적재</button>
                {busy && <span className="muted">처리 중…</span>}
              </div>
            </Panel>
            {committed && (
              <div className="notice">
                {committed.inserted.toLocaleString()}행을 적재했습니다. 오류 {committed.invalid.toLocaleString()}행은 적재하지 않았습니다.
                {committed.errors_url && <> <a href={committed.errors_url}>오류 행 내려받기</a></>}
              </div>
            )}
            {result && <ValidationView r={result} />}
          </div>
        </div>
      )}
      <Loading error={targets.error} />
    </>
  )
}

function ValidationView({ r }: { r: Validation }) {
  const cols = r.preview[0] ? Object.keys(r.preview[0]) : []
  return (
    <>
      <div className="grid cols-3">
        <div className="panel metric"><span className="label">전체 행</span><span className="value">{r.total.toLocaleString()}</span><span className="hint">&nbsp;</span></div>
        <div className="panel metric"><span className="label">검증 통과</span><span className="value" style={{ color: 'var(--ok)' }}>{r.valid.toLocaleString()}</span><span className="hint">적재 대상</span></div>
        <div className="panel metric"><span className="label">오류 행</span><span className="value" style={{ color: r.invalid ? 'var(--danger)' : undefined }}>{r.invalid.toLocaleString()}</span>
          <span className="hint">{r.errors_url ? <a href={r.errors_url}>오류 행만 내려받기 (CSV)</a> : '오류 없음'}</span></div>
      </div>
      {r.errors.length > 0 && (
        <Panel title="오류 행 (최대 200개 표시)" flush>
          <div className="table-wrap" style={{ maxHeight: 260 }}>
            <table className="t">
              <thead><tr><th style={{ width: 70 }}>행</th><th>오류 내용</th></tr></thead>
              <tbody>{r.errors.map((e) => <tr key={e.row}><td>{e.row}</td><td title={e.messages.join(' / ')} style={{ color: 'var(--danger)' }}>{e.messages.join(' / ')}</td></tr>)}</tbody>
            </table>
          </div>
        </Panel>
      )}
      {r.preview.length > 0 && (
        <Panel title="검증 통과 미리보기 (최대 20행)" flush>
          <div className="table-wrap" style={{ maxHeight: 300 }}>
            <table className="t">
              <thead><tr>{cols.map((c) => <th key={c}>{c === 'row' ? '행' : c}</th>)}</tr></thead>
              <tbody>{r.preview.map((p, i) => <tr key={i}>{cols.map((c) => <td key={c}>{String(p[c] ?? '')}</td>)}</tr>)}</tbody>
            </table>
          </div>
        </Panel>
      )}
    </>
  )
}

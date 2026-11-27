import { Fragment, useEffect, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { api, errorText } from '../api/client'
import { useAsync } from '../lib/hooks'

interface Ref { kind: string; id: string | number; label: string; link: string }
interface ToolCall { name: string; args: Record<string, unknown>; ok: boolean; result?: unknown; error?: string; ms?: number }
interface Reply { answer: string; tool_calls: ToolCall[]; refs: Ref[]; model: string; mode: 'tools' | 'json' }
interface Msg { role: 'user' | 'assistant'; content: string; reply?: Reply; error?: boolean }
interface Health { available: boolean; model: string; model_present: boolean; message: string | null; models?: string[] }

const EXAMPLES = ['LOT 20260302A0004-1 불량 원인 후보 알려줘', '이번 주 주의해야 할 부품 뭐야', '최근 2주 불량 유형별 건수 알려줘']

// 답변 속 근거 ID(LOT 번호, 위반 #, 판정 #)를 화면 링크로 바꾼다
function linkify(text: string, refs: Ref[]): ReactNode {
  const labels = refs.map((r) => r.label).filter(Boolean).sort((a, b) => b.length - a.length)
  if (!labels.length) return text
  const esc = labels.map((l) => l.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  const parts = text.split(new RegExp(`(${esc.join('|')})`, 'g'))
  return parts.map((p, i) => {
    const ref = refs.find((r) => r.label === p)
    return ref ? <Link key={i} to={ref.link}>{p}</Link> : <Fragment key={i}>{p}</Fragment>
  })
}

export function ChatPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const health = useAsync(() => api.get<Health>('/assistant/health'), [open])
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const end = useRef<HTMLDivElement>(null)

  useEffect(() => end.current?.scrollIntoView({ block: 'end' }), [msgs, busy])
  useEffect(() => {
    if (!busy) return
    const t0 = Date.now()
    const id = setInterval(() => setElapsed(Math.round((Date.now() - t0) / 1000)), 1000)
    return () => clearInterval(id)
  }, [busy])

  const send = async (text?: string) => {
    const q = (text ?? input).trim()
    if (!q || busy) return
    const next: Msg[] = [...msgs, { role: 'user', content: q }]
    setMsgs(next)
    setInput('')
    setBusy(true)
    setElapsed(0)
    try {
      const history = next.filter((m) => !m.error).map((m) => ({ role: m.role, content: m.content }))
      const reply = await api.post<Reply>('/assistant/chat', { messages: history })
      setMsgs([...next, { role: 'assistant', content: reply.answer, reply }])
    } catch (e) {
      setMsgs([...next, { role: 'assistant', content: errorText(e), error: true }])
      health.reload()
    } finally {
      setBusy(false)
    }
  }

  const h = health.data
  return (
    <aside className="chat" style={{ display: open ? 'flex' : 'none' }}>
      <div className="panel-head">
        <h2>어시스턴트</h2>
        <span className="faint" style={{ fontSize: 12 }}>{h ? `로컬 모델 ${h.model}` : ''}</span>
        <span style={{ flex: 1 }} />
        {msgs.length > 0 && <button className="small" onClick={() => setMsgs([])}>새 대화</button>}
        <button className="small" onClick={onClose}>닫기</button>
      </div>
      <div className="msgs">
        {h && !h.available && (
          <div className="notice warn">
            로컬 LLM(Ollama)에 연결할 수 없습니다. 다른 기능은 정상 동작합니다.
            <br />{h.message}
            <br />서버에서 Ollama 를 실행하고 설정 파일의 [llm] ollama_url, model 을 확인하세요.
            <div style={{ marginTop: 6 }}><button className="small" onClick={health.reload}>다시 확인</button></div>
          </div>
        )}
        {h && h.available && !h.model_present && <div className="notice warn">{h.message}</div>}
        {msgs.length === 0 && (
          <div className="stack" style={{ gap: 6 }}>
            <div className="muted" style={{ fontSize: 13 }}>계보·SPC·불량 이력을 조회하는 고정 도구만 사용해 답합니다. 데이터에 없는 내용은 "데이터에 없음"으로 답합니다.</div>
            {EXAMPLES.map((e) => <button key={e} className="small" style={{ justifyContent: 'flex-start' }} onClick={() => send(e)}>{e}</button>)}
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={`msg ${m.role}`} style={m.error ? { borderColor: '#e3b1ac', color: 'var(--danger)' } : undefined}>
            {m.reply ? linkify(m.content, m.reply.refs) : m.content}
            {m.reply && m.reply.refs.length > 0 && (
              <div className="refs">{m.reply.refs.slice(0, 12).map((r) => <Link key={`${r.kind}${r.id}`} to={r.link}>{r.label}</Link>)}</div>
            )}
            {m.reply && m.reply.tool_calls.length > 0 && (
              <details>
                <summary>도구 호출 {m.reply.tool_calls.length}건 {m.reply.mode === 'json' ? '(JSON 대체 방식)' : ''}</summary>
                {m.reply.tool_calls.map((c, k) => (
                  <div key={k} style={{ marginTop: 6 }}>
                    <div><b>{c.name}</b> {c.ok ? '' : <span style={{ color: 'var(--danger)' }}>실패</span>} {c.ms != null && <span className="faint">{c.ms}ms</span>}</div>
                    <pre>인자: {JSON.stringify(c.args, null, 1)}</pre>
                    <pre>{c.ok ? JSON.stringify(c.result, null, 1) : c.error}</pre>
                  </div>
                ))}
              </details>
            )}
          </div>
        ))}
        {busy && <div className="msg assistant muted">답변 생성 중… {elapsed}초 (로컬 모델은 수십 초~수 분 걸릴 수 있습니다)</div>}
        <div ref={end} />
      </div>
      <div className="composer">
        <textarea value={input} placeholder="질문을 입력하세요 (Enter 전송, Shift+Enter 줄바꿈)" onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }} />
        <button className="primary" disabled={busy || !input.trim()} onClick={() => send()}>전송</button>
      </div>
    </aside>
  )
}

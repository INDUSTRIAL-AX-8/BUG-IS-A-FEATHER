import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, errorText } from '../api/client'
import type { Lot } from '../api/types'
import { useAuth } from '../auth'
import { fmtDateTime } from '../lib/format'
import { useAsync } from '../lib/hooks'
import { useTerms } from '../lib/terms'
import { Help } from './Help'
import { StatusBadge } from './ui'

interface Note { id: number; text: string; author: string | null; author_id: number | null; created_at: string }

/** LOT 메모 목록 + 작성. 실무자 이상 작성, 본인·관리자 삭제. */
export function LotMemo({ lotNo, onChanged, compact }: { lotNo: string; onChanged?: () => void; compact?: boolean }) {
  const { can, user } = useAuth()
  const notes = useAsync(() => api.get<Note[]>(`/lots/${encodeURIComponent(lotNo)}/notes`), [lotNo])
  const [text, setText] = useState('')
  const [msg, setMsg] = useState<string | null>(null)
  const add = async () => {
    if (!text.trim()) return
    try {
      await api.post(`/lots/${encodeURIComponent(lotNo)}/notes`, { text })
      setText(''); setMsg(null); notes.reload(); onChanged?.()
    } catch (e) { setMsg(errorText(e)) }
  }
  const del = async (id: number) => {
    if (!confirm('이 메모를 지웁니다.')) return
    try { await api.del(`/lots/notes/${id}`); notes.reload(); onChanged?.() } catch (e) { setMsg(errorText(e)) }
  }
  return (
    <div className="stack" style={{ gap: 6 }}>
      <div className="row"><b style={{ fontSize: 13 }}>메모</b><Help topic="memo" /></div>
      {can('OPERATOR') && (
        <div className="row" style={{ alignItems: 'stretch' }}>
          <textarea value={text} onChange={(e) => setText(e.target.value)} placeholder="현장 관찰, 조치, 인수인계…" rows={compact ? 2 : 3} style={{ flex: 1 }}
            onKeyDown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) add() }} />
          <button className="primary" onClick={add} disabled={!text.trim()} title="Ctrl+Enter">남기기</button>
        </div>
      )}
      {msg && <div className="notice error">{msg}</div>}
      {(notes.data ?? []).map((n) => (
        <div key={n.id} style={{ borderLeft: '3px solid var(--border-strong)', padding: '2px 8px', fontSize: 13 }}>
          <div style={{ whiteSpace: 'pre-wrap' }}>{n.text}</div>
          <div className="faint" style={{ fontSize: 11.5 }}>
            {n.author ?? '-'} · {fmtDateTime(n.created_at)}
            {(n.author_id === user?.id || can('ADMIN')) && <button className="small" style={{ marginLeft: 6, height: 18 }} onClick={() => del(n.id)}>지우기</button>}
          </div>
        </div>
      ))}
      {notes.data && !notes.data.length && <div className="faint" style={{ fontSize: 12.5 }}>아직 메모가 없습니다.</div>}
    </div>
  )
}

interface SearchHit extends Lot { note_count: number; last_note: string | null; matched_note: boolean }

/** 상단 검색: LOT 번호 일부 또는 메모 내용. ↑↓ 선택, Enter 이동, Esc 닫기. */
export function GlobalSearch() {
  const nav = useNavigate()
  const { t } = useTerms()
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<SearchHit[]>([])
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const text = q.trim()
    if (!text) { setHits([]); return }
    const id = window.setTimeout(() => {
      api.get<SearchHit[]>('/lots/search', { q: text, limit: 10 }).then((h) => { setHits(h); setActive(0); setOpen(true) }).catch(() => setHits([]))
    }, 200)
    return () => window.clearTimeout(id)
  }, [q])
  useEffect(() => {
    const close = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [])

  const go = (h: SearchHit | undefined) => {
    if (!h) return
    setOpen(false); setQ('')
    nav(`/lots?lot=${encodeURIComponent(h.lot_no)}`)
  }
  return (
    <div className="search" ref={box}>
      <input value={q} placeholder={`${t('lotNo')} 또는 메모 내용으로 검색`} onChange={(e) => setQ(e.target.value)} onFocus={() => hits.length && setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => Math.min(a + 1, hits.length - 1)) }
          else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)) }
          else if (e.key === 'Enter') go(hits[active])
          else if (e.key === 'Escape') setOpen(false)
        }} aria-label={`${t('lot')} 검색`} />
      {open && q.trim() && (
        <div className="results" role="listbox">
          {hits.map((h, i) => (
            <button key={h.id} className={i === active ? 'active' : ''} onMouseEnter={() => setActive(i)} onClick={() => go(h)} role="option" aria-selected={i === active}>
              <span className="row" style={{ width: '100%' }}>
                <b>{h.lot_no}</b><span className="muted" style={{ fontSize: 12 }}>{h.item_type_name}</span>
                <span className="right"><StatusBadge status={h.status} /></span>
              </span>
              {h.note_count > 0 && <span className="faint" style={{ fontSize: 12 }}>메모 {h.note_count} · {h.last_note}{h.matched_note ? ' (메모에서 찾음)' : ''}</span>}
            </button>
          ))}
          {!hits.length && <div className="empty" style={{ padding: 12 }}>찾는 {t('lot')}이 없습니다.</div>}
        </div>
      )}
    </div>
  )
}

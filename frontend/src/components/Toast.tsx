import { useEffect, useState } from 'react'
import { Icon } from './Icon'

// 화면 어디서나 toast('저장했습니다') 로 우측 하단에 잠깐 띄우는 알림
type T = { id: number; text: string; kind: 'ok' | 'error' | 'info' }
let push: ((t: Omit<T, 'id'>) => void) | null = null
let seq = 0

export function toast(text: string, kind: T['kind'] = 'ok') {
  push?.({ text, kind })
}

export function Toaster() {
  const [items, setItems] = useState<T[]>([])
  useEffect(() => {
    push = (t) => {
      const id = ++seq
      setItems((xs) => [...xs.slice(-3), { ...t, id }])
      setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), t.kind === 'error' ? 6000 : 3500)
    }
    return () => { push = null }
  }, [])
  return (
    <div className="toasts" role="status" aria-live="polite">
      {items.map((t) => (
        <div key={t.id} className={`toast ${t.kind}`}>
          <Icon name={t.kind === 'error' ? 'alert' : t.kind === 'ok' ? 'check' : 'bulb'} />
          <span>{t.text}</span>
        </div>
      ))}
    </div>
  )
}

// 같은 서버(FastAPI)의 /api 만 호출한다. 외부 주소 호출 금지.
const TOKEN_KEY = 'bif_token'

export class ApiError extends Error {
  status: number
  detail: unknown
  constructor(status: number, message: string, detail?: unknown) {
    super(message)
    this.status = status
    this.detail = detail
  }
}

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* 저장소 사용 불가 시 쿠키로만 인증 */
  }
}

let onUnauthorized: (() => void) | null = null
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn
}

type Query = Record<string, string | number | boolean | null | undefined>

function qs(query?: Query): string {
  if (!query) return ''
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined && v !== null && v !== '') p.set(k, String(v))
  }
  const s = p.toString()
  return s ? `?${s}` : ''
}

async function request<T>(method: string, path: string, body?: unknown, query?: Query, raw?: boolean): Promise<T> {
  const headers: Record<string, string> = {}
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  let payload: BodyInit | undefined
  if (body instanceof FormData) payload = body
  else if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }
  let res: Response
  try {
    res = await fetch(`/api${path}${qs(query)}`, { method, headers, body: payload, credentials: 'same-origin' })
  } catch {
    throw new ApiError(0, '서버에 연결할 수 없습니다. 서버가 실행 중인지 확인하세요.')
  }
  if (res.status === 401 && path !== '/auth/login') onUnauthorized?.()
  if (!res.ok) {
    let message = `요청이 실패했습니다 (${res.status}).`
    let detail: unknown
    try {
      const j = await res.json()
      message = j.message ?? message
      detail = j.detail
    } catch {
      /* 본문 없음 */
    }
    throw new ApiError(res.status, message, detail)
  }
  if (raw) return res as unknown as T
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

export const api = {
  get: <T>(path: string, query?: Query) => request<T>('GET', path, undefined, query),
  post: <T>(path: string, body?: unknown, query?: Query) => request<T>('POST', path, body, query),
  put: <T>(path: string, body?: unknown) => request<T>('PUT', path, body),
  del: <T>(path: string) => request<T>('DELETE', path),
  raw: (path: string, body?: unknown, query?: Query) => request<Response>('POST', path, body, query, true),
}

export function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    if (Array.isArray(e.detail) && e.detail.length) {
      const lines = e.detail.map((d) =>
        typeof d === 'string' ? d : d && typeof d === 'object' && 'message' in d
          ? `${(d as { field?: string }).field ? (d as { field: string }).field + ': ' : ''}${(d as { message: string }).message}`
          : JSON.stringify(d),
      )
      return `${e.message}\n${lines.join('\n')}`
    }
    return e.message
  }
  return e instanceof Error ? e.message : String(e)
}

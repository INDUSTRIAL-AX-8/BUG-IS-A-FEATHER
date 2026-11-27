import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { api } from '../api/client'

// 화면 용어 프리셋. 데이터 모델은 범용(LOT·설비·작업 조건…)이고, 화면에 보이는 말만 현장 용어로 바꾼다.
// 설정 > 분석 설정 의 '화면 용어' 에서 바꾸며, 가져오기(사출 기록, KAMP)가 알맞은 프리셋을 자동 지정한다.
export const TERM_KEYS = {
  lot: '처리 단위', lotNo: '번호', genealogy: '추적', genealogyGraph: '추적 화면', equipment: '설비', step: '공정',
  condition: '조건', characteristic: '특성', operator: '작업자', rawLot: '원료 단위', product: '제품',
  defectJudgment: '불량 판정', scanHint: '입력 안내', drivers: '요인 분석', run: '처리 기록', upstream: '역방향',
  downstream: '정방향', impact: '영향 범위', judge: '판정 행위',
} as const
export type TermKey = keyof typeof TERM_KEYS

export const PRESETS: Record<string, { label: string; terms: Record<TermKey, string> }> = {
  general: {
    label: '범용 (LOT 기준)',
    terms: {
      lot: 'LOT', lotNo: 'LOT 번호', genealogy: '계보', genealogyGraph: '계보 그래프', equipment: '설비', step: '공정 단계',
      condition: '작업 조건', characteristic: '측정 특성', operator: '작업자', rawLot: '원자재 LOT', product: '제품',
      defectJudgment: '불량 판정', scanHint: 'LOT 번호 입력(바코드 스캔 가능)', drivers: '요인 분석', run: '공정 처리',
      upstream: '역방향 (원인)', downstream: '정방향 (영향 범위)', impact: '잠재 불량 범위', judge: '판정',
    },
  },
  injection: {
    label: '사출 성형',
    terms: {
      lot: '생산 실적', lotNo: '생산 실적 번호(LOT)', genealogy: '이력 추적', genealogyGraph: '이력 추적', equipment: '사출기',
      step: '공정', condition: '성형 조건', characteristic: '품질 특성', operator: '작업자', rawLot: '수지(원료) LOT',
      product: '성형품', defectJudgment: '불량 등록', scanHint: '생산 실적 번호 또는 박스 라벨(LOT) 스캔',
      drivers: '불량 요인 분석', run: '성형 실적', upstream: '원인 쪽 (역추적)', downstream: '영향 쪽 (출하·재고 추적)',
      impact: '출하·재고 점검 대상', judge: '등록',
    },
  },
  diecasting: {
    label: '다이캐스팅',
    terms: {
      lot: 'LOT', lotNo: '제품 ID / LOT 번호', genealogy: '이력 추적', genealogyGraph: '이력 추적', equipment: '설비 (주조기·금형)',
      step: '공정', condition: '주조 조건', characteristic: '품질 특성', operator: '작업자', rawLot: '용탕 LOT', product: '주조품',
      defectJudgment: '불량 판정', scanHint: '제품 ID 또는 LOT 번호 입력(바코드 스캔 가능)', drivers: '불량 요인 분석', run: 'shot 기록',
      upstream: '원인 쪽 (역추적)', downstream: '영향 쪽 (같은 원인 제품)', impact: '격리·재검사 대상', judge: '판정',
    },
  },
}

interface TermState { preset: string; t: (k: TermKey) => string; reload: () => void }
const TermsContext = createContext<TermState>({ preset: 'general', t: (k) => PRESETS.general.terms[k], reload: () => undefined })

export function TermsProvider({ children }: { children: ReactNode }) {
  const [cfg, setCfg] = useState<{ preset: string; overrides: Record<string, string> }>({ preset: 'general', overrides: {} })
  const load = () => {
    api.get<{ preset: string; overrides: Record<string, string> }>('/settings/terminology').then(setCfg).catch(() => undefined)
  }
  useEffect(load, [])
  const base = PRESETS[cfg.preset] ?? PRESETS.general
  const t = (k: TermKey) => cfg.overrides?.[k] || base.terms[k]
  return <TermsContext.Provider value={{ preset: cfg.preset, t, reload: load }}>{children}</TermsContext.Provider>
}

export function useTerms() {
  return useContext(TermsContext)
}

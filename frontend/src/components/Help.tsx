import { useId, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

// 체계에 익숙하지 않은 실무자를 위한 용어 설명. 같은 말은 화면 어디서나 같은 설명을 쓴다.
export const GLOSSARY: Record<string, { title: string; body: string }> = {
  nelson: { title: '넬슨룰', body: '관리도에서 "우연으로 보기 어려운 패턴"을 잡는 8가지 규칙입니다. R1은 한 점이 크게 튄 것, R2~R4는 한쪽으로 치우침·연속 상승/하락·지그재그, R5~R8은 경계 근처에 몰리는 패턴입니다. 위반 = 공정에 무언가 변화가 생겼다는 신호이지 곧바로 불량은 아닙니다.' },
  rule1: { title: 'R1 한 점 이탈', body: '한 점이 관리한계(보통 ±3σ) 밖. 갑작스러운 이상(설비 고장, 재료 불량, 측정 실수)을 잡습니다.' },
  rule2: { title: 'R2 한쪽 치우침', body: '연속 9점이 중심선 한쪽. 값이 조금씩 옮겨 간 것(공구 마모, 온도 변화 등)을 잡습니다.' },
  rule3: { title: 'R3 연속 상승/하락', body: '연속 6점이 계속 오르거나 내림. 서서히 진행되는 변화(가열, 마모)를 한계 이탈 전에 잡습니다.' },
  rule4: { title: 'R4 지그재그', body: '연속 14점이 번갈아 오르내림. 두 설비·두 재료를 번갈아 쓰는 등 체계적인 교대를 의심합니다.' },
  rule5: { title: 'R5 2σ 근접', body: '연속 3점 중 2점이 같은 쪽 2σ 밖. 이탈 직전의 신호입니다.' },
  rule6: { title: 'R6 1σ 근접', body: '연속 5점 중 4점이 같은 쪽 1σ 밖. 작은 평균 이동을 잡습니다.' },
  rule7: { title: 'R7 너무 고름', body: '연속 15점이 ±1σ 안. 너무 안정적이면 측정·기록이 잘못됐거나 관리한계가 넓게 잡힌 것일 수 있습니다.' },
  rule8: { title: 'R8 양쪽 흩어짐', body: '연속 8점이 ±1σ 밖에 양쪽으로. 서로 다른 두 집단(설비·재료)이 섞였을 때 나타납니다.' },
  controlLimit: { title: '관리한계 (UCL/LCL)', body: '공정이 평소처럼 돌 때 값이 머무는 범위(중심 ± 3σ)입니다. 고객 규격(USL/LSL)과는 다릅니다. 규격 안이어도 관리한계를 벗어나면 "평소와 다르다"는 뜻입니다.' },
  spec: { title: '규격 (USL/LSL)', body: '제품이 합격하려면 지켜야 하는 한계입니다. 이걸 벗어나면 불량입니다.' },
  provisional: { title: '임시 한계', body: '관리한계를 아직 고정하지 않아 처음 일부 데이터로 계산한 값입니다. 관리자가 안정적인 기간을 골라 "고정"하면 그 뒤로는 고정 한계로 판정합니다.' },
  regime: { title: '상위 LOT별 기준선', body: '런·배치·용탕마다 공정 수준이 조금씩 다르면, 각 묶음 안에서 따로 관리한계를 잡습니다. 묶음 간 수준 차이가 전부 이상으로 잡히는 것을 막습니다.' },
  causeScore: { title: '원인 후보 점수', body: '6가지 근거(관리도 위반, 이 노드를 거친 제품의 불량률, 예측 위험, 조건 이탈, 설계와 다른 투입, 과거 확정 이력)를 0~1로 매겨 가중 평균한 값입니다. 점수 상세에서 무엇 때문에 높은지 막대로 볼 수 있습니다. 확정 원인이 아니라 "먼저 확인할 순서"입니다.' },
  lift: { title: '공유 불량률 (Lift)', body: '이 노드를 거친 제품의 불량률 ÷ 거치지 않은 제품의 불량률. 2면 두 배로 불량이 많다는 뜻입니다. 제품 수가 적으면 우연일 수 있어 보정하고 p-value(작을수록 우연이 아닐 가능성↑)를 함께 보여 줍니다.' },
  commonPath: { title: '공통 경로 보정', body: '거의 모든 제품이 거치는 공정·설비는 "거친 것 vs 안 거친 것"을 비교할 수 없어 구분력이 없습니다. 그래서 점수를 낮춥니다.' },
  learned: { title: '학습 범위', body: '현장이 허용 범위를 정하지 않은 조건에 대해, 요인 분석이 데이터에서 "이 값을 넘으면 불량이 늘었다"고 찾은 경계입니다. 현장 검증 후 마스터 관리에서 정식 허용 범위로 바꾸세요.' },
  confounder: { title: '다른 요인으로 설명됨 (교란)', body: '겉보기엔 불량과 관련 있어 보이지만, 진짜 원인과 같은 시기에 함께 바뀌었을 뿐인 경우입니다. 예: 신입이 근무한 날 금형 온도가 높았다면 신입 탓처럼 보입니다. 같은 금형 온도끼리 비교하면 차이가 없어 원인이 아니라고 판단합니다.' },
  confidence: { title: '확신도', body: '높음: 근거가 강하고 데이터가 충분 / 보통: 근거는 있으나 확인 필요 / 낮음: 참고만. 관측 데이터의 상관이므로 현장 확인으로 원인을 확정하세요.' },
  oe: { title: '예상 대비 (관측/예상 비)', body: '주요인 값만으로 예상한 불량 수에 비해 실제 불량이 몇 배였는지. 1에 가까우면 그 요인 때문에 더 나빠지지 않았다는 뜻입니다.' },
  arima: { title: 'ARIMA 예측 위험', body: '지금까지의 추세로 다음 몇 구간을 통계적으로 예측해, 관리한계나 규격을 넘을 확률을 점수로 낸 것입니다. 확정 판단이 아닙니다.' },
  quarantine: { title: '보류 / 격리', body: '보류: 출하를 잠시 멈추고 확인. 격리: 따로 분리해 재검사·폐기 판단. 격리 해제는 관리자만 할 수 있고 모두 기록됩니다.' },
  live: { title: '실시간 수집', body: '설비 기록이 실시간 DB에서 계속 들어오는 상황을 시연합니다. 학습된 정상 분포에서 값을 만들고, 주요인이 서서히 범위를 벗어났다가 정비로 회복되는 흐름을 재현합니다.' },
  twin: { title: '디지털 트윈', body: '실제 설비 기록을 3D 공장 모형에 그대로 재생합니다. 실시간 수집이 켜져 있으면 새 기록이 들어오는 즉시 모형이 움직이고 이상 시 설비가 붉게 표시됩니다.' },
  memo: { title: '메모', body: '현장에서 본 것, 조치, 인수인계를 LOT(생산 실적)마다 남깁니다. 상단 검색창은 LOT 번호뿐 아니라 메모 내용으로도 찾습니다.' },
}

/** 마우스를 올리거나 키보드로 포커스하면 설명이 뜨는 '?' 표시. topic 은 GLOSSARY 키, 또는 title/body 직접 지정. */
export function Help(props: { topic?: string; title?: string; children?: ReactNode; inline?: boolean }) {
  const g = props.topic ? GLOSSARY[props.topic] : undefined
  const title = props.title ?? g?.title
  const body = props.children ?? g?.body
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null)
  const ref = useRef<HTMLSpanElement>(null)
  const id = useId()
  if (!body) return null
  const show = () => {
    const r = ref.current?.getBoundingClientRect()
    if (r) setPos({ x: Math.min(r.left, window.innerWidth - 340), y: r.bottom + 6 })
  }
  return (
    <>
      <span ref={ref} className="help" tabIndex={0} role="button" aria-label={`${title ?? '도움말'} 설명`} aria-describedby={pos ? id : undefined}
        onMouseEnter={show} onMouseLeave={() => setPos(null)} onFocus={show} onBlur={() => setPos(null)}>?</span>
      {pos && createPortal(
        <div id={id} role="tooltip" className="popover" style={{ left: pos.x, top: pos.y }}>
          {title && <b>{title}</b>}
          <div>{body}</div>
        </div>, document.body)}
    </>
  )
}

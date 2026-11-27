import { PageHead, Panel } from '../components/ui'

// 데이터 기반 3D 시뮬레이션은 향후 확장 (명세 6장). 지금은 자리만 둔다.
// 구현 위치: 이 파일에서 react-three-fiber 캔버스를 그리고, GET /api/events?from=&to= 를 시간순으로 재생한다.
// 설비 배치는 마스터 관리 > 설비의 X/Y/Z 좌표와 3D 모델 경로를 쓴다. 자세한 내용은 README '3D 확장' 절 참고.
export function SimulationPage() {
  return (
    <>
      <PageHead title="3D 시뮬레이션" sub="준비 중" />
      <Panel>
        <div className="stack" style={{ gap: 8, maxWidth: 760 }}>
          <div>설비 배치와 LOT 흐름을 3D 로 재생하는 기능은 준비 중입니다.</div>
          <div className="muted" style={{ fontSize: 13 }}>
            미리 마련된 자리: 설비 좌표(X/Y/Z)·3D 모델 경로 (마스터 관리 &gt; 설비), 시간순 통합 이벤트 API
            <code> GET /api/events?from=&amp;to=</code> (공정 시작/종료·측정·SPC 위반·불량 판정). 2D 타임라인과 3D 재생이 같은 이벤트를 씁니다.
          </div>
        </div>
      </Panel>
    </>
  )
}

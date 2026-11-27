// Phase 5 에서 구현
export function ChatPanel({ onClose }: { onClose: () => void }) {
  return (
    <aside className="chat">
      <div className="panel-head">
        <h2>어시스턴트</h2>
        <span style={{ flex: 1 }} />
        <button className="small" onClick={onClose}>닫기</button>
      </div>
      <div className="msgs">
        <div className="notice">로컬 LLM 어시스턴트는 준비 중입니다.</div>
      </div>
    </aside>
  )
}

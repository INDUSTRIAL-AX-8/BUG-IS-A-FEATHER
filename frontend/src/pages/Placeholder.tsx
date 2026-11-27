import { PageHead, Panel } from '../components/ui'

export function Placeholder({ title }: { title: string }) {
  return (
    <>
      <PageHead title={title} />
      <Panel><div className="empty">준비 중입니다.</div></Panel>
    </>
  )
}

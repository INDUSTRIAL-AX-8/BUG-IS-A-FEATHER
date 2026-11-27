import { FACTOR_ORDER, type Candidate } from '../api/causes'
import { fmtNum, fmtPct, NODE_LABEL } from '../lib/format'
import { ScoreBar } from './ui'
import { Help } from './Help'

// 요소별 기여도를 같은 축 막대로 보여준다 — "왜 이게 1순위인지" 설명용
export function ScoreDetail({ c, labels }: { c: Candidate; labels: Record<string, string> }) {
  const maxContribution = Math.max(...Object.values(c.contributions), 0.0001)
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="row">
        <b>{c.rank}위</b>
        <span className="badge neutral">{NODE_LABEL[c.type]}</span>
        <span>{c.label}</span>
        <span className="right">종합 <b>{c.score.toFixed(3)}</b><Help topic="causeScore" /></span>
      </div>
      <table className="t">
        <thead>
          <tr><th style={{ width: 104 }}>요소</th><th>기여도</th><th className="num" style={{ width: 54 }}>점수</th></tr>
        </thead>
        <tbody>
          {FACTOR_ORDER.map((k) => {
            const f = c.factors[k]
            return (
              <tr key={k} title={f.note} style={{ height: 'auto' }}>
                <td style={{ verticalAlign: 'top', paddingTop: 8 }}>{labels[k] ?? k}{k === 'lift' && <Help topic="lift" />}{k === 'arima' && <Help topic="arima" />}{k === 'condition' && <Help topic="learned" />}</td>
                <td style={{ whiteSpace: 'normal', padding: '6px 10px' }}>
                  <ScoreBar value={c.contributions[k]} max={maxContribution} />
                  <div className="faint" style={{ fontSize: 12, marginTop: 3 }}>{f.note}</div>
                  {k === 'lift' && f.raw.exposed_n ? <LiftLine raw={f.raw} /> : null}
                </td>
                <td className="num" style={{ verticalAlign: 'top', paddingTop: 8 }}>{f.score.toFixed(2)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
      {c.specificity?.note && <div className="notice warn" style={{ fontSize: 12 }}>공통 경로 보정<Help topic="commonPath" />: {c.specificity.note}</div>}
    </div>
  )
}

function LiftLine({ raw }: { raw: Record<string, unknown> }) {
  const r = raw as { rate_exposed: number; rate_other: number | null; smoothed_lift: number | null; p_value: number | null }
  return (
    <div className="muted" style={{ fontSize: 12 }}>
      불량률 {fmtPct(r.rate_exposed)} vs {fmtPct(r.rate_other)} · 평활 Lift {fmtNum(r.smoothed_lift, 2)} · p {r.p_value === null ? '-' : r.p_value < 0.001 ? '<0.001' : fmtNum(r.p_value, 3)}
    </div>
  )
}

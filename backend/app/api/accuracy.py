"""정확도 지표: 확정 원인이 판정 시점 후보 Top-1/3/5 안에 있었던 비율 (기간별)."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import admin, viewer
from app.db import get_db
from app.models import AuditLog, CandidateSnapshot, DefectJudgment, RootCauseConfirmation, User, node_key

router = APIRouter(tags=["정확도/감사"])

TOP_K = (1, 3, 5)


def _bucket(t: datetime, unit: str) -> str:
    if unit == "month":
        return t.strftime("%Y-%m")
    if unit == "day":
        return t.strftime("%Y-%m-%d")
    monday = (t - timedelta(days=t.weekday())).date()
    return monday.isoformat()


def accuracy_rows(db: Session, start: datetime | None = None, end: datetime | None = None) -> list[dict]:
    """확정 원인이 있는 판정별: 판정 시점 스냅샷에서 확정 노드의 최고 순위."""
    stmt = (select(DefectJudgment, CandidateSnapshot)
            .join(CandidateSnapshot, CandidateSnapshot.judgment_id == DefectJudgment.id)
            .where(DefectJudgment.is_cancelled.is_(False)))
    if start:
        stmt = stmt.where(DefectJudgment.judged_at >= start)
    if end:
        stmt = stmt.where(DefectJudgment.judged_at <= end)
    snaps: dict[int, tuple[DefectJudgment, CandidateSnapshot]] = {}
    for j, s in db.execute(stmt.order_by(CandidateSnapshot.computed_at)):
        snaps.setdefault(j.id, (j, s))  # 판정 시점(가장 이른) 스냅샷
    if not snaps:
        return []
    confirmed: dict[int, set[str]] = defaultdict(set)
    for c in db.scalars(select(RootCauseConfirmation).where(RootCauseConfirmation.judgment_id.in_(list(snaps)))):
        confirmed[c.judgment_id].add(node_key(c.node_type, c.node_id))
    rows = []
    for jid, nodes in confirmed.items():
        j, s = snaps[jid]
        ranks = [r["rank"] for r in s.ranked if r["node"] in nodes]
        rows.append({"judgment_id": jid, "judged_at": j.judged_at, "rank": min(ranks) if ranks else None,
                     "candidates": len(s.ranked)})
    return sorted(rows, key=lambda r: r["judged_at"])


def _rates(rows: list[dict]) -> dict:
    n = len(rows)
    out: dict = {"total": n}
    for k in TOP_K:
        out[f"top{k}"] = (sum(1 for r in rows if r["rank"] is not None and r["rank"] <= k) / n) if n else None
    return out


@router.get("/accuracy")
def accuracy(
    start: datetime | None = Query(default=None, alias="from"),
    end: datetime | None = Query(default=None, alias="to"),
    bucket: str = Query(default="week", pattern="^(day|week|month)$"),
    db: Session = Depends(get_db),
    _=Depends(viewer),
):
    rows = accuracy_rows(db, start, end)
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[_bucket(r["judged_at"], bucket)].append(r)
    cumulative: list[dict] = []
    series = []
    for key in sorted(groups):
        cumulative.extend(groups[key])
        series.append({"period": key, **_rates(groups[key]), "cumulative": _rates(cumulative)})
    return {
        "overall": _rates(rows),
        "bucket": bucket,
        "series": series,
        "recent": [{**r, "judged_at": r["judged_at"]} for r in rows[-20:]][::-1],
        "note": "판정 저장 시점의 후보 순위(스냅샷) 기준. 확정 원인이 있는 판정만 집계합니다.",
    }


@router.get("/audit")
def audit_log(entity: str | None = None, action: str | None = None, limit: int = Query(default=200, ge=1, le=2000),
              db: Session = Depends(get_db), _=Depends(admin)):
    stmt = select(AuditLog, User.username).outerjoin(User, User.id == AuditLog.user_id)
    if entity:
        stmt = stmt.where(AuditLog.entity == entity)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    return [
        {"id": a.id, "at": a.at, "user": u, "action": a.action, "entity": a.entity, "entity_id": a.entity_id,
         "before": a.before, "after": a.after}
        for a, u in db.execute(stmt.order_by(AuditLog.at.desc(), AuditLog.id.desc()).limit(limit))
    ]

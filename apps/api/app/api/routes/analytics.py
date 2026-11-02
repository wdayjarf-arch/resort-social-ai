from datetime import datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, case
from sqlalchemy.orm import Session
from app.api.deps import require_roles
from app.db.dependencies import get_db
from app.db.models.analytics import AnalyticsSnapshot
from app.db.models.engagement import Conversation, Message, PostEngagement, ScheduledPost
from app.db.models.identity import Membership, MembershipRole, SocialAccount
from app.services.analytics import activity_matrix, best_slots, ingest_account_insights

router = APIRouter(tags=["Audience analytics"])
READ_ROLES = tuple(MembershipRole)
WRITE_ROLES = (MembershipRole.owner, MembershipRole.admin, MembershipRole.manager, MembershipRole.analyst)

@router.post("/organizations/{organization_id}/analytics/ingest")
def ingest(organization_id: UUID, account_id: UUID | None = None, days: int = Query(30, ge=1, le=90),
           _: Membership = Depends(require_roles(*WRITE_ROLES)), db: Session = Depends(get_db)):
    stmt = select(SocialAccount).where(SocialAccount.organization_id == organization_id,
        SocialAccount.provider == "meta", SocialAccount.is_active.is_(True))
    if account_id: stmt = stmt.where(SocialAccount.id == account_id)
    accounts = db.scalars(stmt).all()
    result = []
    for account in accounts:
        try: result.append({"account_id": str(account.id), "account": account.display_name,
                            "snapshots_upserted": ingest_account_insights(db, organization_id, account, days)})
        except Exception as exc:
            result.append({"account_id": str(account.id), "account": account.display_name,
                           "error": str(exc)[:240]})
    return {"accounts_processed": len(accounts), "results": result}

@router.get("/organizations/{organization_id}/analytics/heatmap")
def heatmap(organization_id: UUID, account_id: UUID | None = None, timezone_name: str = Query("Asia/Aden", alias="timezone"),
            days: int = Query(90, ge=7, le=365), _: Membership = Depends(require_roles(*READ_ROLES)), db: Session = Depends(get_db)):
    try: ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError: raise HTTPException(422, "Unknown IANA timezone")
    stmt = select(SocialAccount.id).where(SocialAccount.organization_id == organization_id, SocialAccount.is_active.is_(True))
    if account_id: stmt = stmt.where(SocialAccount.id == account_id)
    ids = list(db.scalars(stmt).all())
    matrix = activity_matrix(db, organization_id, ids, timezone_name, days)
    matrix["peak_slots"] = best_slots(matrix)
    return matrix

@router.get("/organizations/{organization_id}/analytics/suggest-slot")
def suggest_slot(organization_id: UUID, account_ids: list[UUID] = Query(...), timezone_name: str = Query("Asia/Aden", alias="timezone"),
                horizon_days: int = Query(14, ge=1, le=30), _: Membership = Depends(require_roles(*WRITE_ROLES)), db: Session = Depends(get_db)):
    accounts = db.scalars(select(SocialAccount).where(SocialAccount.organization_id == organization_id,
        SocialAccount.id.in_(set(account_ids)), SocialAccount.is_active.is_(True))).all()
    if len(accounts) != len(set(account_ids)): raise HTTPException(422, "Invalid or cross-organization account id")
    try: tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError: raise HTTPException(422, "Unknown IANA timezone")
    matrix = activity_matrix(db, organization_id, [a.id for a in accounts], timezone_name, 90)
    now = datetime.now(tz); candidates=[]
    for offset in range(horizon_days + 1):
        day = now.date() + timedelta(days=offset)
        for hour in range(24):
            candidate = datetime(day.year, day.month, day.day, hour, 0, tzinfo=tz)
            if candidate <= now + timedelta(minutes=10): continue
            score = matrix["scores"][candidate.weekday()][hour]
            candidates.append((score, candidate))
    candidates.sort(key=lambda x:(x[0], -x[1].timestamp()), reverse=True)
    chosen = candidates[0] if candidates else (0, now + timedelta(hours=1))
    return {"suggested_at": chosen[1].isoformat(), "score": chosen[0], "timezone": timezone_name,
            "observations": matrix["sample_count"], "confidence": "low" if matrix["sample_count"] < 20 else "medium" if matrix["sample_count"] < 100 else "higher",
            "note": "Recommendation uses observed inbound-message/comment timestamps. With little history, treat as a starting point rather than a statistically validated forecast."}

@router.get("/organizations/{organization_id}/dashboard/summary")
def dashboard_summary(organization_id: UUID, _: Membership = Depends(require_roles(*READ_ROLES)), db: Session = Depends(get_db)):
    messages = db.scalars(select(Message).where(Message.organization_id == organization_id)).all()
    inbound = [m for m in messages if m.direction == "inbound"]
    auto_replied = sum(1 for m in messages if m.direction == "outbound" and m.ai_generated and m.status == "sent" and m.human_approved_at is None)
    reviewed = sum(1 for m in messages if m.direction == "outbound" and m.human_approved_at is not None)
    pending_messages = db.scalar(select(func.count()).select_from(Message).where(Message.organization_id == organization_id, Message.status == "needs_human_approval")) or 0
    pending_comments = db.scalar(select(func.count()).select_from(PostEngagement).where(PostEngagement.organization_id == organization_id, PostEngagement.status == "needs_human_approval")) or 0
    total_posts = db.scalar(select(func.count()).select_from(ScheduledPost).where(ScheduledPost.organization_id == organization_id, ScheduledPost.status.in_(["published","partially_published","failed"]))) or 0
    succeeded = db.scalar(select(func.count()).select_from(ScheduledPost).where(ScheduledPost.organization_id == organization_id, ScheduledPost.status == "published")) or 0
    failed = db.scalar(select(func.count()).select_from(ScheduledPost).where(ScheduledPost.organization_id == organization_id, ScheduledPost.status == "failed")) or 0
    return {"messages_handled": len(inbound), "ai_auto_replied": auto_replied, "human_reviewed": reviewed,
        "ai_deflection_rate": round(auto_replied / max(1, auto_replied + reviewed) * 100, 2),
        "pending_approvals": pending_messages + pending_comments, "pending_message_approvals": pending_messages,
        "pending_comment_approvals": pending_comments, "publishing_successes": succeeded, "publishing_failures": failed,
        "publishing_success_rate": round(succeeded / max(1, total_posts) * 100, 2),
        "publishing_failure_rate": round(failed / max(1, total_posts) * 100, 2), "publishing_completed_total": total_posts}

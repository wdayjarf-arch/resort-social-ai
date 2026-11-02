"""Tenant-safe scheduling API for Meta publishing."""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_user, require_roles
from app.db.dependencies import get_db
from app.db.models.engagement import ScheduledPost, ScheduledPostTarget
from app.db.models.identity import Membership, MembershipRole, Provider, SocialAccount, User
from app.schemas.operations import ScheduledPostCreate

router = APIRouter(tags=["Scheduled posts"])
WRITE_ROLES = (MembershipRole.owner, MembershipRole.admin, MembershipRole.manager, MembershipRole.publisher)


def _serialize(row: ScheduledPost) -> dict:
    return {"id": str(row.id), "content": row.content, "media_urls": row.media_urls,
            "target_account_ids": [t.social_account_id for t in row.targets], "scheduled_at": row.scheduled_at,
            "status": row.status, "attempts": row.attempts, "last_error": row.last_error,
            "published_results": row.published_results, "created_at": row.created_at}


@router.post("/organizations/{organization_id}/scheduled-posts/suggested", status_code=201)
def create_suggested_post(
    organization_id: UUID, body: ScheduledPostCreate,
    timezone_name: str = Query(default="Asia/Aden", alias="timezone"),
    membership: Membership = Depends(require_roles(*WRITE_ROLES)), current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Queue a post at the highest-scoring future hour within the next 14 days."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    from datetime import timedelta
    from app.services.analytics import activity_matrix
    try: tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError: raise HTTPException(status_code=422, detail="Unknown IANA timezone")
    target_ids = set(body.target_account_ids)
    accounts = db.scalars(select(SocialAccount).where(SocialAccount.organization_id == organization_id,
        SocialAccount.id.in_(target_ids), SocialAccount.provider == Provider.meta, SocialAccount.is_active.is_(True))).all()
    if len(accounts) != len(target_ids): raise HTTPException(status_code=422, detail="One or more target accounts are invalid")
    for account in accounts:
        if account.account_type == "instagram_business" and not body.media_urls:
            raise HTTPException(status_code=422, detail="Instagram publishing requires a public HTTPS image URL")
    matrix = activity_matrix(db, organization_id, [a.id for a in accounts], timezone_name, 90)
    now = datetime.now(tz); candidates = []
    for offset in range(15):
        day = now.date() + timedelta(days=offset)
        for hour in range(24):
            candidate = datetime(day.year, day.month, day.day, hour, 0, tzinfo=tz)
            if candidate > now + timedelta(minutes=10):
                candidates.append((matrix["scores"][candidate.weekday()][hour], candidate))
    candidates.sort(key=lambda x: (x[0], -x[1].timestamp()), reverse=True)
    suggested_score, suggested_at = candidates[0] if candidates else (0, now + timedelta(hours=1))
    row = ScheduledPost(organization_id=organization_id, created_by_user_id=current_user.id, content=body.content,
        media_urls=[str(url) for url in body.media_urls], scheduled_at=suggested_at.astimezone(timezone.utc), status="scheduled", attempts=0)
    db.add(row); db.flush()
    for account in accounts: db.add(ScheduledPostTarget(organization_id=organization_id, scheduled_post_id=row.id, social_account_id=account.id))
    db.commit(); db.refresh(row)
    row = db.scalar(select(ScheduledPost).options(selectinload(ScheduledPost.targets)).where(ScheduledPost.id == row.id))
    result = _serialize(row); result["optimization"] = {"score": suggested_score, "timezone": timezone_name, "sample_count": matrix["sample_count"]}
    return result


@router.post("/organizations/{organization_id}/scheduled-posts", status_code=201)
def create_scheduled_post(
    organization_id: UUID, body: ScheduledPostCreate,
    membership: Membership = Depends(require_roles(*WRITE_ROLES)), current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if body.scheduled_at.tzinfo is None or body.scheduled_at.utcoffset() is None:
        raise HTTPException(status_code=422, detail="scheduled_at must include a timezone offset")
    if body.scheduled_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=422, detail="scheduled_at must be in the future")
    target_ids = set(body.target_account_ids)
    accounts = db.scalars(select(SocialAccount).where(
        SocialAccount.organization_id == organization_id, SocialAccount.id.in_(target_ids),
        SocialAccount.provider == Provider.meta, SocialAccount.is_active.is_(True),
    )).all()
    if len(accounts) != len(target_ids):
        raise HTTPException(status_code=422, detail="One or more target accounts are invalid or belong to another organization")
    for account in accounts:
        if account.account_type == "instagram_business" and not body.media_urls:
            raise HTTPException(status_code=422, detail="Instagram publishing requires at least one public HTTPS image URL")
    row = ScheduledPost(
        organization_id=organization_id, created_by_user_id=current_user.id, content=body.content,
        media_urls=[str(url) for url in body.media_urls], scheduled_at=body.scheduled_at.astimezone(timezone.utc),
        status="scheduled", attempts=0,
    )
    db.add(row)
    db.flush()
    for account in accounts:
        db.add(ScheduledPostTarget(organization_id=organization_id, scheduled_post_id=row.id, social_account_id=account.id))
    db.commit()
    db.refresh(row)
    row = db.scalar(select(ScheduledPost).options(selectinload(ScheduledPost.targets)).where(ScheduledPost.id == row.id))
    return _serialize(row)


@router.get("/organizations/{organization_id}/scheduled-posts")
def list_scheduled_posts(
    organization_id: UUID, status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0),
    _: Membership = Depends(require_roles(*WRITE_ROLES)), db: Session = Depends(get_db),
):
    stmt = select(ScheduledPost).options(selectinload(ScheduledPost.targets)).where(ScheduledPost.organization_id == organization_id)
    if status_filter:
        stmt = stmt.where(ScheduledPost.status == status_filter)
    rows = db.scalars(stmt.order_by(ScheduledPost.scheduled_at.desc()).limit(limit).offset(offset)).all()
    return [_serialize(row) for row in rows]


@router.post("/organizations/{organization_id}/scheduled-posts/{post_id}/cancel")
def cancel_scheduled_post(
    organization_id: UUID, post_id: UUID, _: Membership = Depends(require_roles(*WRITE_ROLES)), db: Session = Depends(get_db),
):
    row = db.scalar(select(ScheduledPost).where(ScheduledPost.organization_id == organization_id, ScheduledPost.id == post_id).with_for_update())
    if not row:
        raise HTTPException(status_code=404, detail="Scheduled post not found")
    if row.status != "scheduled":
        raise HTTPException(status_code=409, detail="Only scheduled posts can be cancelled")
    row.status = "cancelled"
    db.commit()
    return {"id": str(row.id), "status": row.status}

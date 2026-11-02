"""Unified inbox APIs; every read/write is scoped to the path tenant."""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db.dependencies import get_db
from app.db.models.engagement import Conversation, EngagementStatus, Message, PostEngagement
from app.db.models.identity import Membership, MembershipRole, User
from app.schemas.operations import ApproveReplyRequest, ConversationOut, EngagementReplyRequest, MessageOut
from app.api.deps import get_current_user
from app.workers.tasks import send_approved_reply, send_approved_comment_reply

router = APIRouter(tags=["Unified inbox"])
INBOX_ROLES = (MembershipRole.owner, MembershipRole.admin, MembershipRole.manager, MembershipRole.agent, MembershipRole.support)


@router.get("/organizations/{organization_id}/conversations", response_model=list[ConversationOut])
def list_conversations(
    organization_id: UUID,
    status_filter: str | None = Query(default=None, alias="status", max_length=40),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _: Membership = Depends(require_roles(*INBOX_ROLES)),
    db: Session = Depends(get_db),
):
    stmt = select(Conversation).where(Conversation.organization_id == organization_id)
    if status_filter:
        stmt = stmt.where(Conversation.status == status_filter)
    return db.scalars(stmt.order_by(Conversation.last_message_at.desc().nullslast()).limit(limit).offset(offset)).all()


@router.get("/organizations/{organization_id}/conversations/{conversation_id}/messages", response_model=list[MessageOut])
def list_messages(
    organization_id: UUID, conversation_id: UUID,
    _: Membership = Depends(require_roles(*INBOX_ROLES)), db: Session = Depends(get_db),
):
    conversation = db.scalar(select(Conversation).where(Conversation.organization_id == organization_id, Conversation.id == conversation_id))
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return db.scalars(select(Message).where(Message.organization_id == organization_id, Message.conversation_id == conversation_id).order_by(Message.created_at.asc())).all()


@router.post("/organizations/{organization_id}/conversations/{conversation_id}/reply")
def send_manual_dm_reply(
    organization_id: UUID, conversation_id: UUID, body: EngagementReplyRequest,
    _: Membership = Depends(require_roles(*INBOX_ROLES)), current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.integrations.meta.delivery import deliver_meta_direct_message
    from app.integrations.meta.client import MetaAPIError

    conversation = db.scalar(select(Conversation).where(
        Conversation.organization_id == organization_id, Conversation.id == conversation_id,
    ))
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conversation.channel not in {"messenger", "instagram"}:
        raise HTTPException(status_code=422, detail="Meta direct-message adapter does not support this channel")

    draft = Message(
        organization_id=organization_id, conversation_id=conversation.id,
        social_account_id=conversation.social_account_id, direction="outbound",
        recipient_external_id=conversation.participant_id, text=body.text,
        status="sending" if body.send_now else "needs_human_approval",
        human_approved_by_user_id=current_user.id if body.send_now else None,
        human_approved_at=datetime.now(timezone.utc) if body.send_now else None,
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)
    if not body.send_now:
        return {"id": str(draft.id), "status": draft.status}

    try:
        outcome = deliver_meta_direct_message(db, draft, conversation)
    except MetaAPIError as exc:
        # The delivery helper already persisted the safe outcome classification.
        raise HTTPException(
            status_code=502,
            detail=f"Meta delivery attempt failed; recorded state: {exc.delivery_state}",
        ) from exc
    if outcome["status"] != "sent":
        raise HTTPException(
            status_code=502,
            detail=f"Meta delivery was not confirmed; recorded state: {outcome['status']}",
        )
    return {
        "id": str(draft.id), "status": outcome["status"],
        "provider_message_id": outcome.get("provider_message_id"),
    }

@router.patch("/organizations/{organization_id}/conversations/{conversation_id}/status")
def update_conversation_status(
    organization_id: UUID, conversation_id: UUID, status: str,
    _: Membership = Depends(require_roles(*INBOX_ROLES)), db: Session = Depends(get_db),
):
    if status not in {"open", "pending", "resolved", "archived"}:
        raise HTTPException(status_code=422, detail="Invalid conversation status")
    row = db.scalar(select(Conversation).where(Conversation.organization_id == organization_id, Conversation.id == conversation_id))
    if not row:
        raise HTTPException(status_code=404, detail="Conversation not found")
    row.status = status
    db.commit()
    return {"id": str(row.id), "status": row.status}


@router.post("/organizations/{organization_id}/messages/{message_id}/approve")
def approve_message_reply(
    organization_id: UUID, message_id: UUID, body: ApproveReplyRequest,
    membership: Membership = Depends(require_roles(*INBOX_ROLES)), current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.integrations.social_delivery import is_retryable_social_message_failure

    row = db.scalar(select(Message).where(Message.organization_id == organization_id, Message.id == message_id))
    if not row or row.direction != "outbound":
        raise HTTPException(status_code=404, detail="Reply draft not found")
    safe_retry = is_retryable_social_message_failure(row)
    if row.status != "needs_human_approval" and not safe_retry:
        raise HTTPException(status_code=409, detail="Reply is not awaiting approval or safely retryable")
    if body.edited_text is not None:
        row.text = body.edited_text
    row.status = "approved"
    row.human_approved_by_user_id = current_user.id
    row.human_approved_at = datetime.now(timezone.utc)
    db.commit()
    try:
        send_approved_reply.apply_async(args=[str(row.id), str(current_user.id)])
        dispatch = "queued"
    except Exception:
        dispatch = "pending_recovery"
    return {"id": str(row.id), "status": "approved", "dispatch": dispatch}

@router.get("/organizations/{organization_id}/post-engagements")
def list_post_engagements(
    organization_id: UUID, status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0),
    _: Membership = Depends(require_roles(*INBOX_ROLES)), db: Session = Depends(get_db),
):
    stmt = select(PostEngagement).where(PostEngagement.organization_id == organization_id)
    if status_filter:
        stmt = stmt.where(PostEngagement.status == status_filter)
    rows = db.scalars(stmt.order_by(PostEngagement.created_at.desc()).limit(limit).offset(offset)).all()
    return [{"id": str(r.id), "social_account_id": str(r.social_account_id), "channel": r.channel,
             "provider_comment_id": r.provider_comment_id, "parent_post_id": r.parent_post_id,
             "parent_comment_id": r.parent_comment_id, "author_name": r.author_name, "text": r.text,
             "status": r.status, "drafted_reply": r.drafted_reply, "intent": r.intent,
             "ai_confidence": r.ai_confidence, "created_at": r.created_at} for r in rows]


@router.post("/organizations/{organization_id}/post-engagements/{engagement_id}/approve")
def approve_comment_reply(
    organization_id: UUID, engagement_id: UUID, body: ApproveReplyRequest,
    current_user: User = Depends(get_current_user), _: Membership = Depends(require_roles(*INBOX_ROLES)),
    db: Session = Depends(get_db),
):
    row = db.scalar(select(PostEngagement).where(PostEngagement.organization_id == organization_id, PostEngagement.id == engagement_id))
    if not row:
        raise HTTPException(status_code=404, detail="Post engagement not found")
    if row.status != "needs_human_approval":
        raise HTTPException(status_code=409, detail="Engagement is not awaiting approval")
    if body.edited_text is not None:
        row.drafted_reply = body.edited_text
    if not row.drafted_reply:
        raise HTTPException(status_code=422, detail="Reply text is required")
    row.status = "approved"
    db.commit()
    send_approved_comment_reply.apply_async(args=[str(row.id), str(current_user.id)])
    return {"id": str(row.id), "status": "approved", "dispatch": "queued"}


@router.post("/organizations/{organization_id}/post-engagements/{engagement_id}/reply")
def manual_comment_reply(
    organization_id: UUID, engagement_id: UUID, body: EngagementReplyRequest,
    current_user: User = Depends(get_current_user), _: Membership = Depends(require_roles(*INBOX_ROLES)),
    db: Session = Depends(get_db),
):
    row = db.scalar(select(PostEngagement).where(PostEngagement.organization_id == organization_id, PostEngagement.id == engagement_id))
    if not row:
        raise HTTPException(status_code=404, detail="Post engagement not found")
    row.drafted_reply = body.text
    row.status = "approved" if body.send_now else "needs_human_approval"
    db.commit()
    if body.send_now:
        send_approved_comment_reply.apply_async(args=[str(row.id), str(current_user.id)])
    return {"id": str(row.id), "status": row.status}

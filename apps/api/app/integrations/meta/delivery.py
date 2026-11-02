"""Safe Meta delivery helpers shared by automatic and human-approved replies.

A retry is permitted only when MetaClient classified the attempt as definitely
not sent, or Meta explicitly rejected it with a retryable rate-limit response.
Ambiguous outcomes are persisted as delivery_unknown and are never retried
blindly.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models.engagement import Conversation, Message, PostEngagement
from app.integrations.meta.client import MetaAPIError
from app.integrations.meta.operations import MetaPublishingService


_RETRYABLE_MESSAGE_CODES = {"meta:not_sent", "meta:rejected:429"}
_COMMENT_RETRYABLE_PREFIX = "meta_delivery|retryable=1|"


def is_retryable_message_failure(row: Message) -> bool:
    """Whether the same persisted DM draft may safely be retried."""
    return row.status == "failed" and row.provider_error_code in _RETRYABLE_MESSAGE_CODES


def is_retryable_comment_failure(row: PostEngagement) -> bool:
    """Whether the same persisted comment draft may safely be retried."""
    return (
        row.status == "failed"
        and isinstance(row.last_error, str)
        and row.last_error.startswith(_COMMENT_RETRYABLE_PREFIX)
    )


def _failure_info(exc: Exception) -> tuple[str, bool, str, str]:
    """Return delivery state, safe-retry flag, stored code, and safe detail."""
    if isinstance(exc, MetaAPIError):
        state = exc.delivery_state
        retryable = bool(exc.retryable) and state in {"not_sent", "rejected"}
        if state == "not_sent" and retryable:
            code = "meta:not_sent"
        elif state == "rejected" and retryable and exc.status_code == 429:
            code = "meta:rejected:429"
        else:
            suffix = exc.provider_code or (str(exc.status_code) if exc.status_code is not None else "provider")
            code = f"meta:{state}:{suffix}"[:80]
        detail = str(exc)[:1000]
        return state, retryable, code, detail

    if isinstance(exc, HTTPException):
        # Operations raises HTTPException during account/token preflight, before
        # making the Graph API request. It is safe from duplicate delivery but
        # not automatically retryable (permissions/configuration may need repair).
        detail = str(exc.detail)[:1000]
        return "not_sent", False, f"meta:preflight:{exc.status_code}"[:80], detail

    # Unknown exceptions from the provider call or local persistence are
    # conservative: the remote side may already have accepted the operation.
    return "unknown", False, f"meta:unknown:{type(exc).__name__}"[:80], type(exc).__name__[:300]


def _save_message_failure(db: Session, draft: Message, exc: Exception) -> tuple[str, bool]:
    state, retryable, code, detail = _failure_info(exc)
    draft.status = "delivery_unknown" if state == "unknown" else "failed"
    draft.provider_error_code = code
    draft.provider_error = detail
    db.commit()
    return draft.status, retryable


def _save_comment_failure(db: Session, row: PostEngagement, exc: Exception) -> tuple[str, bool]:
    state, retryable, code, detail = _failure_info(exc)
    row.status = "delivery_unknown" if state == "unknown" else "failed"
    row.last_error = (
        f"meta_delivery|retryable={1 if retryable else 0}|state={state}|"
        f"code={code}|error={detail}"
    )[:1500]
    db.commit()
    return row.status, retryable


def deliver_meta_direct_message(
    db: Session, draft: Message, conversation: Conversation
) -> dict[str, Any]:
    """Send one persisted DM draft, preserving ambiguous outcomes safely."""
    try:
        result = MetaPublishingService(db).reply_to_direct_message(
            organization_id=draft.organization_id,
            account_id=draft.social_account_id,
            recipient_id=conversation.participant_id,
            text=draft.text or "",
        )
        result_id = result.get("result_id") if isinstance(result, dict) else None
        if not isinstance(result_id, str) or not result_id.strip() or result_id == "unknown":
            raise MetaAPIError(
                "Meta returned no message identifier; delivery outcome is unknown",
                delivery_state="unknown",
            )
    except Exception as exc:
        db.rollback()
        stored = db.get(Message, draft.id)
        if stored is not None:
            status, retryable = _save_message_failure(db, stored, exc)
        else:
            status, retryable = "delivery_unknown", False
        if retryable:
            raise
        return {"status": status, "message_id": str(draft.id)}

    try:
        stored = db.get(Message, draft.id)
        if stored is None:
            return {"status": "delivery_unknown", "message_id": str(draft.id)}
        stored.provider_message_id = result_id
        stored.status = "sent"
        stored.sent_at = datetime.now(timezone.utc)
        stored.provider_error_code = None
        stored.provider_error = None
        db.commit()
        return {"status": "sent", "message_id": str(draft.id), "provider_message_id": result_id}
    except Exception:
        # Meta returned a success identifier, but local confirmation could not
        # be persisted. Never send the same content again automatically.
        db.rollback()
        stored = db.get(Message, draft.id)
        if stored is not None:
            stored.status = "delivery_unknown"
            stored.provider_error_code = "meta:unknown:local_persist"
            stored.provider_error = "Provider accepted the request; local confirmation was not saved"
            db.commit()
        return {"status": "delivery_unknown", "message_id": str(draft.id)}


def deliver_meta_comment_reply(db: Session, row: PostEngagement) -> dict[str, Any]:
    """Publish one persisted comment reply, preserving ambiguous outcomes."""
    try:
        result = MetaPublishingService(db).reply_to_comment(
            organization_id=row.organization_id,
            account_id=row.social_account_id,
            comment_id=row.provider_comment_id,
            text=row.drafted_reply or "",
        )
        result_id = result.get("result_id") if isinstance(result, dict) else None
        if not isinstance(result_id, str) or not result_id.strip() or result_id == "unknown":
            raise MetaAPIError(
                "Meta returned no comment-reply identifier; delivery outcome is unknown",
                delivery_state="unknown",
            )
    except Exception as exc:
        db.rollback()
        stored = db.get(PostEngagement, row.id)
        if stored is not None:
            status, retryable = _save_comment_failure(db, stored, exc)
        else:
            status, retryable = "delivery_unknown", False
        if retryable:
            raise
        return {"status": status, "engagement_id": str(row.id)}

    try:
        stored = db.get(PostEngagement, row.id)
        if stored is None:
            return {"status": "delivery_unknown", "engagement_id": str(row.id)}
        stored.provider_reply_id = result_id
        stored.status = "replied"
        stored.replied_at = datetime.now(timezone.utc)
        stored.last_error = None
        db.commit()
        return {"status": "replied", "engagement_id": str(row.id), "provider_reply_id": result_id}
    except Exception:
        db.rollback()
        stored = db.get(PostEngagement, row.id)
        if stored is not None:
            stored.status = "delivery_unknown"
            stored.last_error = "meta_delivery|retryable=0|state=unknown|code=meta:unknown:local_persist"
            db.commit()
        return {"status": "delivery_unknown", "engagement_id": str(row.id)}

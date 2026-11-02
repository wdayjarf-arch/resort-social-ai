"""Inbound webhook adapters for WhatsApp Cloud API and Telegram Bot API.

This phase normalizes inbound text/attachment events into the existing Conversation
and Message tables. Outbound delivery is intentionally handled in a later phase.
Secrets are never returned or logged.
"""
from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_roles
from app.core import crypto
from app.core.config import get_settings
from app.core.crypto import decrypt_token
from app.db.dependencies import get_db
from app.db.models.engagement import Conversation, Message
from app.db.models.identity import (
    Membership,
    MembershipRole,
    OAuthCredential,
    Provider,
    SocialAccount,
    User,
)
from app.workers.tasks import process_inbound_message

router = APIRouter(tags=["Social webhooks"])
_MAX_WEBHOOK_BYTES = 2 * 1024 * 1024
_CREDENTIAL_ROLES = (MembershipRole.owner, MembershipRole.admin)


class SocialCredentialRequest(BaseModel):
    credential_kind: Literal["access_token", "webhook_secret"]
    value: SecretStr = Field(min_length=8, max_length=10000)
    scopes: list[str] = Field(default_factory=list, max_length=100)


def verify_whatsapp_signature(raw_body: bytes, signature: str | None, app_secret: str | None) -> bool:
    """Verify X-Hub-Signature-256 using the Meta app secret."""
    if not app_secret or not signature or not signature.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.removeprefix("sha256="))


def extract_whatsapp_messages(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any], str | None]]:
    """Return (phone_number_id, message, contact_name) for valid inbound messages."""
    extracted: list[tuple[str, dict[str, Any], str | None]] = []
    entries = payload.get("entry", [])
    if not isinstance(entries, list):
        return extracted
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        changes = entry.get("changes", [])
        if not isinstance(changes, list):
            continue
        for change in changes:
            if not isinstance(change, dict) or str(change.get("field", "")).lower() != "messages":
                continue
            value = change.get("value") if isinstance(change.get("value"), dict) else {}
            metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
            phone_number_id = metadata.get("phone_number_id")
            messages = value.get("messages", [])
            contacts = value.get("contacts", [])
            if not phone_number_id or not isinstance(messages, list):
                continue
            names: dict[str, str] = {}
            if isinstance(contacts, list):
                for contact in contacts:
                    if not isinstance(contact, dict):
                        continue
                    wa_id = contact.get("wa_id")
                    profile = contact.get("profile") if isinstance(contact.get("profile"), dict) else {}
                    if wa_id and profile.get("name"):
                        names[str(wa_id)] = str(profile["name"])
            for message in messages:
                if isinstance(message, dict) and message.get("id") and message.get("from"):
                    extracted.append((str(phone_number_id), message, names.get(str(message.get("from")))))
    return extracted


def extract_telegram_message(update: dict[str, Any]) -> dict[str, Any] | None:
    """Return a new Telegram message or channel post; ignore edits and status updates."""
    message = update.get("message") or update.get("channel_post")
    if not isinstance(message, dict) or not message.get("message_id"):
        return None
    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    if not chat.get("id"):
        return None
    sender = message.get("from") if isinstance(message.get("from"), dict) else {}
    if sender.get("is_bot"):
        return None
    content = message.get("text") or message.get("caption")
    attachment_types = ("photo", "document", "video", "audio", "voice", "video_note", "sticker", "animation")
    attachments: list[dict[str, Any]] = []
    for kind in attachment_types:
        value = message.get(kind)
        if value:
            attachments.append({"type": kind, "data": value})
    return {
        "chat_id": str(chat["id"]),
        "chat_type": str(chat.get("type") or "unknown"),
        "message_id": str(message["message_id"]),
        "sender_id": str(sender.get("id") or chat["id"]),
        "text": str(content) if content is not None else None,
        "attachments": attachments,
        "date": message.get("date"),
    }


def _timestamp(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return datetime.now(timezone.utc)


def _message_text_and_attachments(message: dict[str, Any]) -> tuple[str | None, list[dict[str, Any]]]:
    kind = str(message.get("type") or "unknown")
    if kind == "text":
        content = message.get("text") if isinstance(message.get("text"), dict) else {}
        return (str(content.get("body")) if content.get("body") is not None else None, [])
    value = message.get(kind) if isinstance(message.get(kind), dict) else {}
    caption = value.get("caption")
    attachment = {"type": kind, "data": value}
    return (str(caption) if caption else None, [attachment])


def _get_or_create_conversation(
    db: Session, *, organization_id: UUID, account: SocialAccount, channel: str,
    thread_id: str, participant_id: str, at: datetime,
) -> Conversation:
    conversation = db.scalar(select(Conversation).where(
        Conversation.social_account_id == account.id,
        Conversation.external_thread_id == thread_id,
    ))
    if conversation is None:
        conversation = Conversation(
            organization_id=organization_id,
            social_account_id=account.id,
            provider=channel,
            channel=channel,
            external_thread_id=thread_id,
            participant_id=participant_id,
            status="open",
            last_message_at=at,
            unread_count=1,
        )
        db.add(conversation)
        db.flush()
    else:
        previous = conversation.last_message_at
        if previous is not None and previous.tzinfo is None:
            previous = previous.replace(tzinfo=timezone.utc)
        conversation.last_message_at = max(previous or at, at)
        conversation.unread_count = (conversation.unread_count or 0) + 1
    return conversation


def _queue_received_messages(message_ids: list[UUID]) -> None:
    """Queue after DB commit. Caller returns 503 so provider can redeliver if queueing fails."""
    for message_id in message_ids:
        process_inbound_message.apply_async(args=[str(message_id)])


@router.get("/webhooks/whatsapp", response_class=PlainTextResponse)
def verify_whatsapp_webhook(request: Request) -> PlainTextResponse:
    settings = get_settings()
    mode = request.query_params.get("hub.mode")
    verify_token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    expected = settings.whatsapp_webhook_verify_token
    if expected is None:
        raise HTTPException(status_code=503, detail="WhatsApp webhook verification is not configured")
    if mode == "subscribe" and verify_token and hmac.compare_digest(
        verify_token, expected.get_secret_value()
    ) and challenge is not None:
        return PlainTextResponse(challenge, status_code=200)
    raise HTTPException(status_code=403, detail="WhatsApp webhook verification failed")


@router.post("/webhooks/whatsapp", status_code=status.HTTP_200_OK)
async def receive_whatsapp_webhook(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    raw_body = await request.body()
    if len(raw_body) > _MAX_WEBHOOK_BYTES:
        raise HTTPException(status_code=413, detail="Webhook payload too large")
    settings = get_settings()
    app_secret = settings.meta_webhook_app_secret or settings.meta_app_secret
    secret_value = app_secret.get_secret_value() if app_secret is not None else None
    if not verify_whatsapp_signature(raw_body, request.headers.get("X-Hub-Signature-256"), secret_value):
        raise HTTPException(status_code=401, detail="Invalid WhatsApp webhook signature")
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid webhook JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Webhook JSON must be an object")

    queued_ids: list[UUID] = []
    for phone_number_id, inbound, _contact_name in extract_whatsapp_messages(payload):
        account = db.scalar(select(SocialAccount).where(
            SocialAccount.provider == Provider.whatsapp,
            SocialAccount.provider_account_id == phone_number_id,
            SocialAccount.is_active.is_(True),
        ))
        if account is None:
            # Never infer tenant ownership from a webhook payload.
            continue
        provider_message_id = str(inbound["id"])
        existing = db.scalar(select(Message).where(
            Message.social_account_id == account.id,
            Message.provider_message_id == provider_message_id,
        ))
        if existing is not None:
            if existing.direction == "inbound" and existing.status == "received":
                queued_ids.append(existing.id)
            continue
        sender_id = str(inbound["from"])
        at = _timestamp(inbound.get("timestamp"))
        text, attachments = _message_text_and_attachments(inbound)
        conversation = _get_or_create_conversation(
            db, organization_id=account.organization_id, account=account, channel="whatsapp",
            thread_id=sender_id, participant_id=sender_id, at=at,
        )
        row = Message(
            organization_id=account.organization_id,
            conversation_id=conversation.id,
            social_account_id=account.id,
            provider_message_id=provider_message_id,
            direction="inbound",
            sender_external_id=sender_id,
            recipient_external_id=phone_number_id,
            text=text,
            attachments=attachments,
            status="received",
            created_at=at,
        )
        db.add(row)
        db.flush()
        queued_ids.append(row.id)
    db.commit()
    try:
        _queue_received_messages(queued_ids)
    except Exception as exc:
        # Persisted messages remain recoverable by provider redelivery.
        raise HTTPException(status_code=503, detail="Message saved but processing queue is unavailable; provider retry required") from exc
    return {"status": "accepted", "queued": len(queued_ids)}


@router.post("/webhooks/telegram/{account_id}", status_code=status.HTTP_200_OK)
async def receive_telegram_webhook(
    account_id: UUID, request: Request, db: Session = Depends(get_db)
) -> dict[str, Any]:
    account = db.scalar(select(SocialAccount).where(
        SocialAccount.id == account_id,
        SocialAccount.provider == Provider.telegram,
        SocialAccount.is_active.is_(True),
    ))
    if account is None:
        raise HTTPException(status_code=404, detail="Connected Telegram account not found")
    credential = db.scalar(select(OAuthCredential).where(
        OAuthCredential.organization_id == account.organization_id,
        OAuthCredential.social_account_id == account.id,
        OAuthCredential.credential_kind == "webhook_secret",
        OAuthCredential.revoked_at.is_(None),
    ))
    if credential is None:
        raise HTTPException(status_code=503, detail="Telegram webhook secret is not configured for this account")
    try:
        expected_secret = decrypt_token(credential.encrypted_value)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Could not decrypt Telegram webhook secret") from exc
    supplied_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if not supplied_secret or not hmac.compare_digest(supplied_secret, expected_secret):
        raise HTTPException(status_code=401, detail="Invalid Telegram webhook secret")
    raw_body = await request.body()
    if len(raw_body) > _MAX_WEBHOOK_BYTES:
        raise HTTPException(status_code=413, detail="Webhook payload too large")
    try:
        update = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid Telegram update JSON") from exc
    if not isinstance(update, dict):
        raise HTTPException(status_code=400, detail="Telegram update must be an object")
    item = extract_telegram_message(update)
    if item is None:
        return {"status": "ignored", "queued": 0}

    provider_message_id = f"telegram:{item['chat_id']}:{item['message_id']}"
    existing = db.scalar(select(Message).where(
        Message.social_account_id == account.id,
        Message.provider_message_id == provider_message_id,
    ))
    if existing is not None:
        queued_ids = [existing.id] if existing.direction == "inbound" and existing.status == "received" else []
    else:
        at = _timestamp(item.get("date"))
        conversation = _get_or_create_conversation(
            db, organization_id=account.organization_id, account=account, channel="telegram",
            thread_id=item["chat_id"], participant_id=item["chat_id"], at=at,
        )
        row = Message(
            organization_id=account.organization_id,
            conversation_id=conversation.id,
            social_account_id=account.id,
            provider_message_id=provider_message_id,
            direction="inbound",
            sender_external_id=item["sender_id"],
            recipient_external_id=item["chat_id"],
            text=item["text"],
            attachments=item["attachments"],
            status="received",
            created_at=at,
        )
        db.add(row)
        db.flush()
        queued_ids = [row.id]
    db.commit()
    try:
        _queue_received_messages(queued_ids)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Message saved but processing queue is unavailable; provider retry required") from exc
    return {"status": "accepted", "queued": len(queued_ids)}


@router.put("/organizations/{organization_id}/social-accounts/{account_id}/credentials")
def save_social_account_credential(
    organization_id: UUID,
    account_id: UUID,
    body: SocialCredentialRequest,
    _: Membership = Depends(require_roles(*_CREDENTIAL_ROLES)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Store per-account WhatsApp/Telegram secrets encrypted at rest."""
    del current_user  # Authorization is enforced by the live tenant membership dependency.
    account = db.scalar(select(SocialAccount).where(
        SocialAccount.organization_id == organization_id,
        SocialAccount.id == account_id,
        SocialAccount.is_active.is_(True),
    ))
    if account is None or account.provider not in {Provider.whatsapp, Provider.telegram}:
        raise HTTPException(status_code=404, detail="WhatsApp or Telegram account not found")
    if account.provider == Provider.whatsapp and body.credential_kind == "webhook_secret":
        raise HTTPException(status_code=422, detail="WhatsApp webhook verification uses the Meta app secret and configured verify token")
    encrypt = getattr(crypto, "encrypt_token", None)
    if not callable(encrypt):
        raise HTTPException(status_code=500, detail="Credential encryption function is unavailable")
    encrypted = encrypt(body.value.get_secret_value())
    row = db.scalar(select(OAuthCredential).where(
        OAuthCredential.organization_id == organization_id,
        OAuthCredential.social_account_id == account_id,
        OAuthCredential.credential_kind == body.credential_kind,
    ))
    if row is None:
        row = OAuthCredential(
            organization_id=organization_id,
            social_account_id=account_id,
            credential_kind=body.credential_kind,
            encrypted_value=encrypted,
            scopes=body.scopes,
        )
        db.add(row)
    else:
        row.encrypted_value = encrypted
        row.scopes = body.scopes
        row.revoked_at = None
    db.commit()
    return {"status": "saved", "account_id": str(account_id), "credential_kind": body.credential_kind}

"""Convert Meta webhook payloads into idempotent normalized inbox rows."""
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.engagement import Conversation, Message, PostEngagement
from app.db.models.identity import MetaWebhookEvent, Provider, SocialAccount


def _dt_from_unix(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(float(value) / 1000 if float(value) > 10_000_000_000 else float(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return datetime.now(timezone.utc)


def normalize_meta_event(db: Session, event: MetaWebhookEvent) -> tuple[list[UUID], list[UUID]]:
    """Return new inbound message and comment IDs; safe to retry due provider ID uniqueness."""
    payload = event.payload if isinstance(event.payload, dict) else {}
    obj = str(payload.get("object") or event.provider_object or "page").lower()
    new_messages: list[UUID] = []
    new_engagements: list[UUID] = []
    entries = payload.get("entry", [])
    if not isinstance(entries, list):
        return new_messages, new_engagements

    for entry in entries:
        if not isinstance(entry, dict) or entry.get("id") is None:
            continue
        external_account_id = str(entry["id"])
        account = db.scalar(select(SocialAccount).where(
            SocialAccount.provider == Provider.meta,
            SocialAccount.provider_account_id == external_account_id,
            SocialAccount.is_active.is_(True),
        ))
        if account is None:
            # Ignore subscriptions not connected to a tenant account; never guess ownership.
            continue
        organization_id = account.organization_id
        channel = "instagram" if obj == "instagram" or account.account_type == "instagram_business" else "messenger"

        messaging = entry.get("messaging", [])
        if isinstance(messaging, list):
            for item in messaging:
                if not isinstance(item, dict):
                    continue
                message_payload = item.get("message")
                if isinstance(message_payload, dict):
                    sender = item.get("sender") if isinstance(item.get("sender"), dict) else {}
                    recipient = item.get("recipient") if isinstance(item.get("recipient"), dict) else {}
                    sender_id = str(sender.get("id") or "unknown")
                    recipient_id = str(recipient.get("id") or external_account_id)
                    is_echo = bool(message_payload.get("is_echo")) or sender_id == external_account_id
                    participant_id = recipient_id if is_echo else sender_id
                    provider_message_id = message_payload.get("mid") or message_payload.get("id")
                    if provider_message_id:
                        existing_message = db.scalar(select(Message).where(
                            Message.social_account_id == account.id,
                            Message.provider_message_id == str(provider_message_id),
                        ))
                        if existing_message:
                            if existing_message.direction == "inbound" and existing_message.status == "received":
                                already_has_reply = db.scalar(select(Message.id).where(Message.reply_to_message_id == existing_message.id))
                                if not already_has_reply:
                                    new_messages.append(existing_message.id)
                            continue
                    conversation = db.scalar(select(Conversation).where(
                        Conversation.social_account_id == account.id,
                        Conversation.external_thread_id == participant_id,
                    ))
                    now = _dt_from_unix(item.get("timestamp"))
                    if conversation is None:
                        conversation = Conversation(
                            organization_id=organization_id, social_account_id=account.id, provider="meta",
                            channel=channel, external_thread_id=participant_id, participant_id=participant_id,
                            status="open", last_message_at=now, unread_count=0 if is_echo else 1,
                        )
                        db.add(conversation)
                        db.flush()
                    else:
                        previous_time = conversation.last_message_at
                        if previous_time is not None and previous_time.tzinfo is None:
                            previous_time = previous_time.replace(tzinfo=timezone.utc)
                        conversation.last_message_at = max(previous_time or now, now)
                        if not is_echo:
                            conversation.unread_count = (conversation.unread_count or 0) + 1
                    message = Message(
                        organization_id=organization_id, conversation_id=conversation.id,
                        social_account_id=account.id,
                        provider_message_id=str(provider_message_id) if provider_message_id else None,
                        direction="outbound" if is_echo else "inbound", sender_external_id=sender_id,
                        recipient_external_id=recipient_id, text=message_payload.get("text"),
                        attachments=message_payload.get("attachments", []) if isinstance(message_payload.get("attachments", []), list) else [],
                        status="sent" if is_echo else "received", created_at=now,
                    )
                    db.add(message)
                    db.flush()
                    if not is_echo:
                        new_messages.append(message.id)
                # Delivery/read receipts update known outbound messages if mids are supplied.
                delivery = item.get("delivery") if isinstance(item.get("delivery"), dict) else None
                read = item.get("read") if isinstance(item.get("read"), dict) else None
                if delivery or read:
                    mids = (delivery or read or {}).get("mids", [])
                    if isinstance(mids, list):
                        for mid in mids:
                            row = db.scalar(select(Message).where(
                                Message.social_account_id == account.id,
                                Message.provider_message_id == str(mid),
                            ))
                            if row:
                                if read:
                                    row.status = "read"
                                    row.read_at = datetime.now(timezone.utc)
                                else:
                                    row.status = "delivered"
                                    row.delivered_at = datetime.now(timezone.utc)

        changes = entry.get("changes", [])
        if isinstance(changes, list):
            for change in changes:
                if not isinstance(change, dict):
                    continue
                field = str(change.get("field") or "").lower()
                value = change.get("value") if isinstance(change.get("value"), dict) else {}
                comment_id = value.get("id") or value.get("comment_id")
                if field == "comments":
                    author = value.get("from") if isinstance(value.get("from"), dict) else {}
                    comment_text = value.get("text") or value.get("message")
                    post_id = value.get("media_id") or value.get("post_id")
                    parent_id = value.get("parent_id")
                elif field == "feed" and (value.get("item") == "comment" or value.get("comment_id")):
                    author = value.get("from") if isinstance(value.get("from"), dict) else {}
                    comment_text = value.get("message") or value.get("text")
                    post_id = value.get("post_id")
                    parent_id = value.get("parent_id")
                    comment_id = value.get("comment_id") or comment_id
                else:
                    continue
                if not comment_id or not comment_text:
                    continue
                existing_engagement = db.scalar(select(PostEngagement).where(
                    PostEngagement.social_account_id == account.id,
                    PostEngagement.provider_comment_id == str(comment_id),
                ))
                if existing_engagement:
                    if existing_engagement.status == "received" and not existing_engagement.drafted_reply:
                        new_engagements.append(existing_engagement.id)
                    continue
                engagement = PostEngagement(
                    organization_id=organization_id, social_account_id=account.id,
                    provider="meta", channel=channel, provider_comment_id=str(comment_id),
                    parent_post_id=str(post_id) if post_id else None,
                    parent_comment_id=str(parent_id) if parent_id else None,
                    author_external_id=str(author.get("id")) if author.get("id") else None,
                    author_name=str(author.get("name")) if author.get("name") else None,
                    text=str(comment_text), status="received",
                )
                db.add(engagement)
                db.flush()
                new_engagements.append(engagement.id)
    return new_messages, new_engagements

"""Per-account outbound delivery for WhatsApp and Telegram, with Meta delegation.

This module is deliberately conservative:
- credentials are loaded from the tenant-scoped SocialAccount/OAuthCredential rows;
- WhatsApp free-form text is sent only inside the 24-hour customer-service window;
- provider timeouts/5xx without a definitive rejection are delivery_unknown;
- only definitely-not-sent connection failures and explicit HTTP 429s are retryable;
- tokens and provider response bodies are never included in stored error details.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import decrypt_token
from app.db.models.engagement import Conversation, Message
from app.db.models.identity import OAuthCredential, Provider, SocialAccount
from app.integrations.meta.delivery import (
    deliver_meta_direct_message,
    is_retryable_message_failure,
)


class SocialDeliveryError(RuntimeError):
    """Safe provider failure classification; never contains credentials or raw URLs."""

    def __init__(
        self,
        provider: str,
        code: str,
        detail: str,
        *,
        delivery_state: str,
        retryable: bool = False,
    ) -> None:
        super().__init__(detail)
        self.provider = provider
        self.code = code
        self.detail = detail[:500]
        self.delivery_state = delivery_state
        self.retryable = bool(retryable) and delivery_state in {"not_sent", "rejected"}


def is_retryable_social_message_failure(row: Message) -> bool:
    """Return True only for a persisted, explicitly safe-to-retry delivery failure."""
    if row.status != "failed":
        return False
    if is_retryable_message_failure(row):
        return True
    code = row.provider_error_code or ""
    return code in {
        "whatsapp:not_sent:connect_failure",
        "whatsapp:rejected:429",
        "telegram:not_sent:connect_failure",
        "telegram:rejected:429",
    }


def _provider_name(value: Any) -> str:
    return str(getattr(value, "value", value) or "").lower()


def _load_account_and_token(
    db: Session, draft: Message, conversation: Conversation
) -> tuple[SocialAccount, str, str]:
    channel = str(conversation.channel or "").lower()
    provider_by_channel = {
        "whatsapp": Provider.whatsapp,
        "telegram": Provider.telegram,
    }
    expected_provider = provider_by_channel.get(channel)
    if expected_provider is None:
        raise SocialDeliveryError(
            channel or "social", "unsupported_channel", "Unsupported social messaging channel",
            delivery_state="not_sent",
        )

    account = db.scalar(
        select(SocialAccount).where(
            SocialAccount.organization_id == draft.organization_id,
            SocialAccount.id == draft.social_account_id,
            SocialAccount.provider == expected_provider,
            SocialAccount.is_active.is_(True),
        )
    )
    if account is None:
        raise SocialDeliveryError(
            channel, "account_unavailable", "Connected social account is unavailable",
            delivery_state="not_sent",
        )
    if _provider_name(account.provider) != channel:
        raise SocialDeliveryError(
            channel, "provider_mismatch", "Connected account provider does not match the conversation channel",
            delivery_state="not_sent",
        )

    credential = db.scalar(
        select(OAuthCredential).where(
            OAuthCredential.organization_id == draft.organization_id,
            OAuthCredential.social_account_id == account.id,
            OAuthCredential.credential_kind == "access_token",
            OAuthCredential.revoked_at.is_(None),
        )
    )
    if credential is None:
        raise SocialDeliveryError(
            channel, "credential_missing", "Connected account access token is not configured",
            delivery_state="not_sent",
        )
    try:
        token = decrypt_token(credential.encrypted_value)
    except Exception as exc:
        raise SocialDeliveryError(
            channel, "credential_decryption_failed", "Connected account credential could not be decrypted",
            delivery_state="not_sent",
        ) from exc
    if not token:
        raise SocialDeliveryError(
            channel, "credential_empty", "Connected account access token is empty",
            delivery_state="not_sent",
        )
    return account, token, channel


def _connection_failure(provider: str, exc: Exception) -> SocialDeliveryError:
    # Connection establishment failures are known to occur before a request is
    # delivered. Read/write timeouts and protocol errors are intentionally handled
    # as ambiguous by the caller.
    return SocialDeliveryError(
        provider,
        "not_sent:connect_failure",
        "Provider connection could not be established; safe retry is permitted",
        delivery_state="not_sent",
        retryable=True,
    )


def _raise_http_failure(provider: str, status_code: int, *, telegram_error_code: int | None = None) -> None:
    effective = telegram_error_code or status_code
    if effective == 429 or status_code == 429:
        raise SocialDeliveryError(
            provider, "rejected:429", "Provider rate-limited the request; safe retry is permitted",
            delivery_state="rejected", retryable=True,
        )
    if status_code >= 500 or effective >= 500:
        raise SocialDeliveryError(
            provider, f"unknown:http_{effective}", "Provider returned a server error; delivery outcome is unknown",
            delivery_state="unknown",
        )
    raise SocialDeliveryError(
        provider, f"rejected:http_{effective}", "Provider rejected the request; review account permissions and message policy",
        delivery_state="rejected",
    )


def _latest_inbound_at(db: Session, conversation: Conversation) -> datetime | None:
    row = db.scalar(
        select(Message)
        .where(
            Message.organization_id == conversation.organization_id,
            Message.conversation_id == conversation.id,
            Message.direction == "inbound",
        )
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    if row is None or row.created_at is None:
        return None
    value = row.created_at
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _send_whatsapp_text(
    db: Session, draft: Message, conversation: Conversation, account: SocialAccount, token: str
) -> str:
    provider = "whatsapp"
    text = draft.text or ""
    if not text.strip():
        raise SocialDeliveryError(provider, "empty_text", "Message text is required", delivery_state="not_sent")
    if len(text) > 4096:
        raise SocialDeliveryError(provider, "text_too_long", "WhatsApp text exceeds the supported 4096-character limit", delivery_state="not_sent")
    recipient = str(conversation.participant_id or "").strip()
    phone_number_id = str(account.provider_account_id or "").strip()
    if not recipient or not phone_number_id:
        raise SocialDeliveryError(provider, "recipient_or_phone_id_missing", "WhatsApp recipient or phone-number ID is missing", delivery_state="not_sent")

    last_inbound = _latest_inbound_at(db, conversation)
    now = datetime.now(timezone.utc)
    if last_inbound is None or now < last_inbound or now - last_inbound > timedelta(hours=24):
        raise SocialDeliveryError(
            provider,
            "service_window_closed",
            "WhatsApp free-form replies require an open 24-hour customer-service window; use an approved template workflow",
            delivery_state="rejected",
        )

    version = str(get_settings().meta_api_version or "v25.0")
    if not (version.startswith("v") and version[1:].replace(".", "").isdigit()):
        raise SocialDeliveryError(provider, "api_version_invalid", "WhatsApp API version is invalid", delivery_state="not_sent")
    url = f"https://graph.facebook.com/{version}/{phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }
    try:
        with httpx.Client(timeout=20.0, follow_redirects=False) as client:
            response = client.post(url, headers={"Authorization": f"Bearer {token}"}, json=payload)
    except (httpx.ConnectTimeout, httpx.ConnectError, httpx.PoolTimeout) as exc:
        raise _connection_failure(provider, exc) from exc
    except httpx.HTTPError as exc:
        raise SocialDeliveryError(
            provider, "unknown:transport", "WhatsApp transport failed after request dispatch may have begun; delivery outcome is unknown",
            delivery_state="unknown",
        ) from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise SocialDeliveryError(provider, "unknown:invalid_response", "WhatsApp returned an unreadable response; delivery outcome is unknown", delivery_state="unknown") from exc
    if not 200 <= response.status_code < 300:
        _raise_http_failure(provider, response.status_code)
    messages = data.get("messages") if isinstance(data, dict) else None
    result_id = messages[0].get("id") if isinstance(messages, list) and messages and isinstance(messages[0], dict) else None
    if not isinstance(result_id, str) or not result_id.strip():
        raise SocialDeliveryError(provider, "unknown:missing_message_id", "WhatsApp returned no message identifier; delivery outcome is unknown", delivery_state="unknown")
    return result_id


def _telegram_reply_parameters(db: Session, draft: Message, conversation: Conversation) -> dict[str, Any] | None:
    if not draft.reply_to_message_id:
        return None
    source = db.get(Message, draft.reply_to_message_id)
    if source is None or not source.provider_message_id:
        return None
    parts = str(source.provider_message_id).split(":", 2)
    if len(parts) != 3 or parts[0] != "telegram" or parts[1] != str(conversation.participant_id):
        return None
    try:
        message_id = int(parts[2])
    except (TypeError, ValueError):
        return None
    return {"message_id": message_id, "allow_sending_without_reply": True}


def _send_telegram_text(
    db: Session, draft: Message, conversation: Conversation, token: str
) -> str:
    provider = "telegram"
    text = draft.text or ""
    if not text.strip():
        raise SocialDeliveryError(provider, "empty_text", "Message text is required", delivery_state="not_sent")
    if len(text) > 4096:
        raise SocialDeliveryError(provider, "text_too_long", "Telegram text exceeds the supported 4096-character limit", delivery_state="not_sent")
    chat_id = str(conversation.participant_id or "").strip()
    if not chat_id:
        raise SocialDeliveryError(provider, "chat_id_missing", "Telegram chat ID is missing", delivery_state="not_sent")

    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    reply_parameters = _telegram_reply_parameters(db, draft, conversation)
    if reply_parameters is not None:
        payload["reply_parameters"] = reply_parameters

    # Telegram bot tokens are part of the URL path in the official Bot API.
    # Never propagate an HTTP exception or request URL into logs or persisted errors.
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        with httpx.Client(timeout=20.0, follow_redirects=False) as client:
            response = client.post(url, json=payload)
    except (httpx.ConnectTimeout, httpx.ConnectError, httpx.PoolTimeout) as exc:
        raise _connection_failure(provider, exc) from None
    except httpx.HTTPError:
        raise SocialDeliveryError(
            provider, "unknown:transport", "Telegram transport failed after request dispatch may have begun; delivery outcome is unknown",
            delivery_state="unknown",
        ) from None

    try:
        data = response.json()
    except ValueError as exc:
        raise SocialDeliveryError(provider, "unknown:invalid_response", "Telegram returned an unreadable response; delivery outcome is unknown", delivery_state="unknown") from exc

    if not 200 <= response.status_code < 300 or not isinstance(data, dict) or not data.get("ok"):
        error_code = data.get("error_code") if isinstance(data, dict) else None
        code = int(error_code) if isinstance(error_code, (int, str)) and str(error_code).isdigit() else response.status_code
        _raise_http_failure(provider, response.status_code, telegram_error_code=code)

    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    result_id = result.get("message_id")
    if result_id is None or str(result_id).strip() == "":
        raise SocialDeliveryError(provider, "unknown:missing_message_id", "Telegram returned no message identifier; delivery outcome is unknown", delivery_state="unknown")
    return str(result_id)


def _persist_failure(db: Session, draft: Message, exc: SocialDeliveryError) -> dict[str, Any]:
    db.rollback()
    stored = db.get(Message, draft.id)
    if stored is None:
        return {"status": "delivery_unknown", "message_id": str(draft.id), "retryable": False}
    stored.status = "delivery_unknown" if exc.delivery_state == "unknown" else "failed"
    stored.provider_error_code = f"{exc.provider}:{exc.code}"[:80]
    stored.provider_error = exc.detail[:1000]
    db.commit()
    return {
        "status": stored.status,
        "message_id": str(stored.id),
        "provider_error_code": stored.provider_error_code,
        "retryable": exc.retryable,
    }


def deliver_social_direct_message(
    db: Session, draft: Message, conversation: Conversation
) -> dict[str, Any]:
    """Deliver one persisted draft using the adapter matching the connected account."""
    channel = str(conversation.channel or "").lower()
    if channel in {"messenger", "instagram"}:
        return deliver_meta_direct_message(db, draft, conversation)

    try:
        account, token, provider = _load_account_and_token(db, draft, conversation)
        if provider == "whatsapp":
            result_id = _send_whatsapp_text(db, draft, conversation, account, token)
        elif provider == "telegram":
            result_id = _send_telegram_text(db, draft, conversation, token)
        else:  # defensive even though _load_account_and_token has already restricted it
            raise SocialDeliveryError(provider, "unsupported_channel", "Unsupported social messaging channel", delivery_state="not_sent")
    except SocialDeliveryError as exc:
        outcome = _persist_failure(db, draft, exc)
        if exc.retryable:
            raise
        return outcome
    except Exception as exc:
        # Any unclassified exception after entering the delivery path is ambiguous.
        db.rollback()
        stored = db.get(Message, draft.id)
        if stored is not None:
            stored.status = "delivery_unknown"
            stored.provider_error_code = f"{channel}:unknown:{type(exc).__name__}"[:80]
            stored.provider_error = "Unclassified delivery error; provider may have accepted the request"
            db.commit()
        return {"status": "delivery_unknown", "message_id": str(draft.id), "retryable": False}

    # A successful HTTP response is not considered delivered until its provider
    # message ID is safely persisted. If local commit fails, do not send again.
    try:
        stored = db.get(Message, draft.id)
        if stored is None:
            return {"status": "delivery_unknown", "message_id": str(draft.id), "retryable": False}
        stored.provider_message_id = result_id
        stored.status = "sent"
        stored.sent_at = datetime.now(timezone.utc)
        stored.provider_error_code = None
        stored.provider_error = None
        db.commit()
        return {"status": "sent", "message_id": str(stored.id), "provider_message_id": result_id}
    except Exception:
        db.rollback()
        stored = db.get(Message, draft.id)
        if stored is not None:
            stored.status = "delivery_unknown"
            stored.provider_error_code = f"{channel}:unknown:local_persist"[:80]
            stored.provider_error = "Provider accepted the request; local confirmation was not saved"
            db.commit()
        return {"status": "delivery_unknown", "message_id": str(draft.id), "retryable": False}

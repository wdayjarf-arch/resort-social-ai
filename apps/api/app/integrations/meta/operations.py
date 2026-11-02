"""Publishing and messaging operations for connected Meta accounts."""
from __future__ import annotations

from urllib.parse import urlparse
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_token
from app.db.models.identity import OAuthCredential, Provider, SocialAccount
from app.integrations.meta.client import MetaAPIError, MetaGraphClient


def _get_account_and_token(db: Session, organization_id: UUID, account_id: UUID) -> tuple[SocialAccount, str]:
    account = db.scalar(select(SocialAccount).where(
        SocialAccount.organization_id == organization_id,
        SocialAccount.id == account_id,
        SocialAccount.provider == Provider.meta,
        SocialAccount.is_active.is_(True),
    ))
    if account is None:
        raise HTTPException(status_code=404, detail="Connected Meta account not found")
    credential = db.scalar(select(OAuthCredential).where(
        OAuthCredential.organization_id == organization_id,
        OAuthCredential.social_account_id == account.id,
        OAuthCredential.credential_kind == "access_token",
        OAuthCredential.revoked_at.is_(None),
    ))
    if credential is None:
        raise HTTPException(status_code=409, detail="Connected account has no active access token")
    if credential.expires_at is not None:
        from datetime import datetime, timezone
        expires = credential.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires <= datetime.now(timezone.utc):
            raise HTTPException(status_code=409, detail="Connected account token has expired; reconnect Meta")
    try:
        return account, decrypt_token(credential.encrypted_value)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail="Could not decrypt connected account credentials") from exc


def _https_image_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise HTTPException(status_code=422, detail="image_url must be an HTTPS URL")
    if parsed.hostname.lower() in {"localhost", "127.0.0.1", "::1"}:
        raise HTTPException(status_code=422, detail="image_url must be publicly reachable")
    return value


class MetaPublishingService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.client = MetaGraphClient()

    def publish_post(
        self, *, organization_id: UUID, account_id: UUID, text: str, image_url: str | None = None,
    ) -> dict:
        account, token = _get_account_and_token(self.db, organization_id, account_id)
        if account.account_type == "facebook_page":
            if image_url:
                response = self.client.post(f"{account.provider_account_id}/photos", token=token, data={
                    "url": _https_image_url(image_url), "caption": text,
                })
            else:
                response = self.client.post(f"{account.provider_account_id}/feed", token=token, data={"message": text})
            return {"account_id": account.provider_account_id, "result_id": response.get("post_id") or response.get("id"), "raw": response}

        if account.account_type == "instagram_business":
            if not image_url:
                raise HTTPException(status_code=422, detail="Instagram feed publishing requires image_url")
            media = self.client.post(f"{account.provider_account_id}/media", token=token, data={
                "image_url": _https_image_url(image_url), "caption": text,
            })
            creation_id = media.get("id")
            if not creation_id:
                raise HTTPException(status_code=502, detail="Meta did not return an Instagram media container ID")
            published = self.client.post(f"{account.provider_account_id}/media_publish", token=token,
                                         data={"creation_id": str(creation_id)})
            return {"account_id": account.provider_account_id, "result_id": published.get("id"), "raw": published}
        raise HTTPException(status_code=422, detail="This Meta account type does not support publishing")

    def reply_to_direct_message(
        self, *, organization_id: UUID, account_id: UUID, recipient_id: str, text: str,
    ) -> dict:
        account, token = _get_account_and_token(self.db, organization_id, account_id)
        if account.account_type == "facebook_page":
            response = self.client.post(f"{account.provider_account_id}/messages", token=token, data={
                "recipient": __import__("json").dumps({"id": recipient_id}),
                "message": __import__("json").dumps({"text": text}),
                "messaging_type": "RESPONSE",
            })
            return {"account_id": account.provider_account_id,
                    "result_id": response.get("message_id"), "raw": response}
        if account.account_type == "instagram_business":
            response = self.client.post(f"{account.provider_account_id}/messages", token=token, data={
                "recipient": __import__("json").dumps({"id": recipient_id}),
                "message": __import__("json").dumps({"text": text}),
            })
            return {"account_id": account.provider_account_id,
                    "result_id": response.get("message_id"), "raw": response}
        raise HTTPException(status_code=422, detail="This Meta account type does not support direct messages")


    def reply_to_comment(
        self, *, organization_id: UUID, account_id: UUID, comment_id: str, text: str,
    ) -> dict:
        account, token = _get_account_and_token(self.db, organization_id, account_id)
        if account.account_type not in {"facebook_page", "instagram_business"}:
            raise HTTPException(status_code=422, detail="This Meta account type does not support comment replies")
        response = self.client.post(f"{comment_id}/replies", token=token, data={"message": text})
        return {"account_id": account.provider_account_id, "result_id": response.get("id"), "raw": response}


def translate_meta_error(exc: MetaAPIError) -> HTTPException:
    if exc.status_code in {400, 401, 403}:
        return HTTPException(status_code=502, detail="Meta rejected the operation; check token, permissions, and messaging policy")
    return HTTPException(status_code=502, detail="Meta operation failed; retry later")

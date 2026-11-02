"""Meta OAuth, account discovery, and encrypted credential persistence."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import encrypt_token
from app.db.models.identity import Membership, MembershipRole, MetaOAuthState, OAuthCredential, Organization, Provider, SocialAccount, User
from app.integrations.meta.client import MetaAPIError, MetaGraphClient

META_SCOPES = (
    "pages_show_list",
    "pages_read_engagement",
    "pages_manage_posts",
    "pages_messaging",
    "instagram_basic",
    "instagram_manage_messages",
    "instagram_manage_comments",
    # Required to publish media through the Instagram Content Publishing API.
    "instagram_content_publish",
    # Required for Page webhook subscriptions in common Page/Messenger configurations.
    "pages_manage_metadata",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def create_authorization_url(db: Session, *, organization: Organization, user: User) -> str:
    settings = get_settings()
    if not settings.meta_app_id or not settings.meta_app_secret:
        raise HTTPException(status_code=503, detail="Meta OAuth is not configured")
    raw_state = secrets.token_urlsafe(32)
    db.add(MetaOAuthState(
        state_digest=_digest(raw_state), organization_id=organization.id, user_id=user.id,
        expires_at=_utcnow() + timedelta(minutes=settings.meta_oauth_state_ttl_minutes),
    ))
    db.commit()
    query = urlencode({
        "client_id": settings.meta_app_id,
        "redirect_uri": settings.meta_oauth_redirect_uri,
        "response_type": "code",
        "scope": ",".join(META_SCOPES),
        "state": raw_state,
    })
    return f"https://www.facebook.com/{settings.meta_api_version}/dialog/oauth?{query}"


def _exchange_tokens(code: str) -> tuple[str, datetime | None, list[str]]:
    settings = get_settings()
    if not settings.meta_app_id or not settings.meta_app_secret:
        raise HTTPException(status_code=503, detail="Meta OAuth is not configured")
    client = MetaGraphClient()
    # Exchange authorization code for short-lived user token.
    short = client.get("oauth/access_token", params={
        "client_id": settings.meta_app_id,
        "client_secret": settings.meta_app_secret.get_secret_value(),
        "redirect_uri": settings.meta_oauth_redirect_uri,
        "code": code,
    })
    short_token = short.get("access_token")
    if not isinstance(short_token, str) or not short_token:
        raise HTTPException(status_code=502, detail="Meta did not return an access token")
    # Exchange short-lived user token for long-lived user token (~60 days).
    long = client.get("oauth/access_token", params={
        "grant_type": "fb_exchange_token",
        "client_id": settings.meta_app_id,
        "client_secret": settings.meta_app_secret.get_secret_value(),
        "fb_exchange_token": short_token,
    })
    token = long.get("access_token")
    if not isinstance(token, str) or not token:
        raise HTTPException(status_code=502, detail="Meta did not return a long-lived user token")
    expires_at = None
    if isinstance(long.get("expires_in"), (int, float)) and long["expires_in"] > 0:
        expires_at = _utcnow() + timedelta(seconds=int(long["expires_in"]))
    permissions = client.get("me/permissions", token=token).get("data", [])
    granted = [item["permission"] for item in permissions if item.get("status") == "granted" and item.get("permission")]
    return token, expires_at, granted


def _save_credential(
    db: Session, account: SocialAccount, *, kind: str, token: str,
    expires_at: datetime | None, scopes: list[str],
) -> None:
    credential = db.scalar(select(OAuthCredential).where(
        OAuthCredential.organization_id == account.organization_id,
        OAuthCredential.social_account_id == account.id,
        OAuthCredential.credential_kind == kind,
    ))
    encrypted = encrypt_token(token)
    if credential is None:
        credential = OAuthCredential(
            organization_id=account.organization_id, social_account_id=account.id,
            credential_kind=kind, encrypted_value=encrypted, expires_at=expires_at,
            scopes=scopes, key_version="fernet-v1",
        )
        db.add(credential)
    else:
        credential.encrypted_value = encrypted
        credential.expires_at = expires_at
        credential.scopes = scopes
        credential.revoked_at = None
        credential.key_version = "fernet-v1"


def _upsert_account(
    db: Session, *, organization_id: UUID, provider_account_id: str,
    display_name: str, account_type: str, scopes: list[str], metadata: dict[str, Any],
    access_token: str, access_expires_at: datetime | None, user_token: str,
    user_expires_at: datetime | None,
) -> SocialAccount:
    account = db.scalar(select(SocialAccount).where(
        SocialAccount.organization_id == organization_id,
        SocialAccount.provider == Provider.meta,
        SocialAccount.provider_account_id == provider_account_id,
    ))
    if account is None:
        # Existing schema has a provider-wide unique constraint, so an account already
        # connected to another tenant will fail safely rather than be reassigned.
        account = SocialAccount(
            organization_id=organization_id, provider=Provider.meta,
            provider_account_id=provider_account_id, display_name=display_name,
            account_type=account_type, granted_scopes=scopes, metadata_json=metadata,
        )
        db.add(account)
        db.flush()
    else:
        account.display_name = display_name
        account.account_type = account_type
        account.granted_scopes = scopes
        account.metadata_json = metadata
        account.is_active = True
    _save_credential(db, account, kind="access_token", token=access_token,
                     expires_at=access_expires_at, scopes=scopes)
    _save_credential(db, account, kind="user_access_token", token=user_token,
                     expires_at=user_expires_at, scopes=scopes)
    return account


def complete_oauth_callback(db: Session, *, code: str, state: str) -> dict[str, Any]:
    now = _utcnow()
    state_row = db.scalar(select(MetaOAuthState).where(
        MetaOAuthState.state_digest == _digest(state), MetaOAuthState.consumed_at.is_(None),
        MetaOAuthState.expires_at > now,
    ).with_for_update())
    if state_row is None:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    state_row.consumed_at = now
    db.commit()  # one-time state is consumed even if Meta later fails
    organization = db.get(Organization, state_row.organization_id)
    user = db.get(User, state_row.user_id)
    membership = db.scalar(select(Membership).where(
        Membership.organization_id == state_row.organization_id,
        Membership.user_id == state_row.user_id,
        Membership.is_active.is_(True),
        Membership.role.in_([MembershipRole.owner, MembershipRole.admin]),
    ))
    if organization is None or not organization.is_active or user is None or not user.is_active or membership is None:
        raise HTTPException(status_code=400, detail="OAuth initiator no longer has permission to connect Meta")

    try:
        user_token, user_expires_at, granted_scopes = _exchange_tokens(code)
        client = MetaGraphClient()
        # /me/accounts can paginate; follow only Meta's next URL by extracting its cursor,
        # and rebuild requests against the configured Graph API base URL.
        pages: list[dict[str, Any]] = []
        page = client.get("me/accounts", token=user_token,
                          params={"fields": "id,name,access_token,tasks,instagram_business_account{id,username}", "limit": 100})
        while True:
            pages.extend(item for item in page.get("data", []) if isinstance(item, dict))
            next_url = (page.get("paging") or {}).get("next")
            if not next_url:
                break
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(next_url)
            if parsed.hostname != "graph.facebook.com":
                raise HTTPException(status_code=502, detail="Unexpected pagination host from Meta")
            query = parse_qs(parsed.query)
            cursor = query.get("after", [None])[0]
            if not cursor:
                break
            page = client.get("me/accounts", token=user_token, params={
                "fields": "id,name,access_token,tasks,instagram_business_account{id,username}",
                "limit": 100, "after": cursor,
            })
        if "pages_show_list" not in granted_scopes:
            raise HTTPException(status_code=403, detail="Meta did not grant pages_show_list; reconnect and approve required permissions")
        saved_pages = 0
        saved_instagrams = 0
        for item in pages:
            page_id, page_name, page_token = item.get("id"), item.get("name"), item.get("access_token")
            if not page_id or not page_token:
                continue
            page_account = _upsert_account(
                db, organization_id=organization.id, provider_account_id=str(page_id),
                display_name=str(page_name or f"Facebook Page {page_id}"), account_type="facebook_page",
                scopes=granted_scopes, metadata={"tasks": item.get("tasks", [])},
                access_token=str(page_token), access_expires_at=None, user_token=user_token,
                user_expires_at=user_expires_at,
            )
            saved_pages += 1
            ig = item.get("instagram_business_account")
            if isinstance(ig, dict) and ig.get("id"):
                _upsert_account(
                    db, organization_id=organization.id, provider_account_id=str(ig["id"]),
                    display_name=str(ig.get("username") or f"Instagram {ig['id']}"),
                    account_type="instagram_business", scopes=granted_scopes,
                    metadata={"linked_facebook_page_id": str(page_id), "username": ig.get("username")},
                    access_token=str(page_token), access_expires_at=None, user_token=user_token,
                    user_expires_at=user_expires_at,
                )
                saved_instagrams += 1
        db.commit()
        return {"connected_pages": saved_pages, "connected_instagram_accounts": saved_instagrams}
    except HTTPException:
        db.rollback()
        raise
    except MetaAPIError as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail="Meta connection failed; check granted permissions and account setup") from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="A discovered Meta account is already connected to another organization") from exc
    except Exception:
        db.rollback()
        raise

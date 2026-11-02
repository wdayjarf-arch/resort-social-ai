"""Tenant-authorized Meta OAuth and publishing/messaging routes."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_owner_admin, require_roles
from app.db.dependencies import get_db
from app.db.models.identity import Membership, MembershipRole, Organization, User
from app.integrations.meta.client import MetaAPIError
from app.integrations.meta.oauth import complete_oauth_callback, create_authorization_url
from app.integrations.meta.operations import MetaPublishingService, translate_meta_error
from app.schemas.meta import MetaAuthorizeResponse, MetaDirectMessageReplyRequest, MetaOperationResult, MetaOAuthResult, MetaPublishRequest

router = APIRouter(tags=["Meta integrations"])


@router.get("/organizations/{organization_id}/integrations/meta/authorize", response_model=MetaAuthorizeResponse)
def meta_authorize(
    organization_id: UUID,
    _: Membership = Depends(require_owner_admin),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MetaAuthorizeResponse:
    organization = db.get(Organization, organization_id)
    if organization is None or not organization.is_active:
        raise HTTPException(status_code=404, detail="Organization not found")
    return MetaAuthorizeResponse(authorization_url=create_authorization_url(db, organization=organization, user=current_user))


@router.get("/integrations/meta/callback", response_model=MetaOAuthResult)
def meta_oauth_callback(
    code: str | None = Query(default=None, min_length=1),
    state: str | None = Query(default=None, min_length=20),
    error: str | None = None,
    db: Session = Depends(get_db),
) -> MetaOAuthResult | JSONResponse:
    if error:
        return JSONResponse(status_code=400, content={"detail": "Meta authorization was cancelled or denied"})
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing OAuth code or state")
    result = complete_oauth_callback(db, code=code, state=state)
    return MetaOAuthResult(**result)


@router.post("/organizations/{organization_id}/social-accounts/{account_id}/publish", response_model=MetaOperationResult)
def publish_meta_post(
    organization_id: UUID,
    account_id: UUID,
    body: MetaPublishRequest,
    _: Membership = Depends(require_roles(MembershipRole.owner, MembershipRole.admin, MembershipRole.manager, MembershipRole.publisher)),
    db: Session = Depends(get_db),
) -> MetaOperationResult:
    try:
        result = MetaPublishingService(db).publish_post(
            organization_id=organization_id, account_id=account_id,
            text=body.text, image_url=str(body.image_url) if body.image_url else None,
        )
    except MetaAPIError as exc:
        raise translate_meta_error(exc) from exc
    return MetaOperationResult(account_id=result["account_id"], result_id=result.get("result_id"), raw=result.get("raw", {}))


@router.post("/organizations/{organization_id}/social-accounts/{account_id}/messages/reply", response_model=MetaOperationResult)
def reply_meta_dm(
    organization_id: UUID,
    account_id: UUID,
    body: MetaDirectMessageReplyRequest,
    _: Membership = Depends(require_roles(MembershipRole.owner, MembershipRole.admin, MembershipRole.manager, MembershipRole.support)),
    db: Session = Depends(get_db),
) -> MetaOperationResult:
    try:
        result = MetaPublishingService(db).reply_to_direct_message(
            organization_id=organization_id, account_id=account_id,
            recipient_id=body.recipient_id, text=body.text,
        )
    except MetaAPIError as exc:
        raise translate_meta_error(exc) from exc
    return MetaOperationResult(account_id=result["account_id"], result_id=result.get("result_id"), raw=result.get("raw", {}))

"""Minimal tenant-scoped connected-account read endpoint."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_agent_or_above, require_tenant_access
from app.db.dependencies import get_db
from app.db.models.identity import Membership, SocialAccount

router = APIRouter(tags=["social accounts"])


class SocialAccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    organization_id: UUID
    property_id: UUID | None
    provider: str
    provider_account_id: str
    display_name: str
    is_active: bool


@router.get("/organizations/{organization_id}/social-accounts", response_model=list[SocialAccountRead])
def list_social_accounts(
    organization_id: UUID,
    _: Membership = Depends(require_agent_or_above),
    db: Session = Depends(get_db),
) -> list[SocialAccount]:
    # Never query by account ID alone; every tenant-owned query includes organization_id.
    return list(db.scalars(
        select(SocialAccount)
        .where(SocialAccount.organization_id == organization_id)
        .order_by(SocialAccount.display_name)
    ).all())


@router.get("/organizations/{organization_id}/social-accounts/{account_id}", response_model=SocialAccountRead)
def get_social_account(
    organization_id: UUID,
    account_id: UUID,
    _: Membership = Depends(require_agent_or_above),
    db: Session = Depends(get_db),
) -> SocialAccount:
    account = db.scalar(select(SocialAccount).where(
        SocialAccount.organization_id == organization_id,
        SocialAccount.id == account_id,
    ))
    if account is None:
        raise HTTPException(status_code=404, detail="Social account not found")
    return account

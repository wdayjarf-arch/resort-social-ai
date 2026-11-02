import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_owner, require_owner_admin, require_tenant_access
from app.core.config import get_settings
from app.db.dependencies import get_db
from app.db.models.identity import (
    Membership, MembershipRole, Organization, OrganizationInvitation, Property, User
)
from app.schemas.security import (
    InvitationAccept, InvitationCreate, InvitationRead, OrganizationCreate,
    OrganizationRead, OrganizationUpdate, PropertyCreate, PropertyRead, PropertyUpdate
)
from app.security.passwords import hash_password
from app.services.audit import write_audit

router = APIRouter(tags=["organizations and properties"])


@router.post("/organizations", response_model=OrganizationRead, status_code=201)
def create_organization(
    payload: OrganizationCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Organization:
    org = Organization(name=payload.name.strip(), slug=payload.slug)
    try:
        db.add(org)
        db.flush()
        db.add(Membership(
            organization_id=org.id, user_id=current_user.id,
            role=MembershipRole.owner, is_active=True,
        ))
        write_audit(
            db, action="organization.create", resource_type="organization",
            organization_id=org.id, actor_user_id=current_user.id, resource_id=str(org.id),
        )
        db.commit()
        db.refresh(org)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Organization slug already exists")
    return org


@router.get("/organizations/{organization_id}", response_model=OrganizationRead)
def get_organization(
    organization_id: UUID,
    _: Membership = Depends(require_tenant_access),
    db: Session = Depends(get_db),
) -> Organization:
    org = db.scalar(select(Organization).where(
        Organization.id == organization_id, Organization.is_active.is_(True)
    ))
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


@router.patch("/organizations/{organization_id}", response_model=OrganizationRead)
def update_organization(
    payload: OrganizationUpdate,
    organization_id: UUID,
    membership: Membership = Depends(require_owner_admin),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Organization:
    org = db.scalar(select(Organization).where(Organization.id == organization_id, Organization.is_active.is_(True)))
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        if value is not None:
            setattr(org, key, value.strip() if key == "name" else value)
    write_audit(db, action="organization.update", resource_type="organization",
                organization_id=organization_id, actor_user_id=current_user.id,
                resource_id=str(organization_id), details={"fields": sorted(changes)})
    try:
        db.commit()
        db.refresh(org)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Organization slug already exists")
    return org


@router.delete("/organizations/{organization_id}", status_code=204)
def deactivate_organization(
    organization_id: UUID,
    membership: Membership = Depends(require_owner),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    org = db.scalar(select(Organization).where(Organization.id == organization_id))
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    org.is_active = False
    write_audit(db, action="organization.deactivate", resource_type="organization",
                organization_id=organization_id, actor_user_id=current_user.id,
                resource_id=str(organization_id))
    db.commit()


@router.post("/organizations/{organization_id}/properties", response_model=PropertyRead, status_code=201)
def create_property(
    payload: PropertyCreate,
    organization_id: UUID,
    membership: Membership = Depends(require_owner_admin),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Property:
    if payload.brand_id is not None:
        # Explicit tenant check prevents cross-org brand references before DB constraint.
        from app.db.models.identity import Brand
        brand = db.scalar(select(Brand).where(
            Brand.id == payload.brand_id, Brand.organization_id == organization_id
        ))
        if brand is None:
            raise HTTPException(status_code=404, detail="Brand not found")
    prop = Property(
        organization_id=organization_id, brand_id=payload.brand_id,
        name=payload.name.strip(), slug=payload.slug, timezone=payload.timezone,
    )
    db.add(prop)
    write_audit(db, action="property.create", resource_type="property",
                organization_id=organization_id, actor_user_id=current_user.id,
                details={"slug": payload.slug})
    try:
        db.commit()
        db.refresh(prop)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Property slug or brand relationship conflicts")
    return prop


@router.get("/organizations/{organization_id}/properties", response_model=list[PropertyRead])
def list_properties(
    organization_id: UUID,
    _: Membership = Depends(require_tenant_access),
    db: Session = Depends(get_db),
) -> list[Property]:
    # The tenant predicate is mandatory even after the membership dependency.
    return list(db.scalars(
        select(Property).where(Property.organization_id == organization_id).order_by(Property.name)
    ).all())


@router.get("/organizations/{organization_id}/properties/{property_id}", response_model=PropertyRead)
def get_property(
    organization_id: UUID,
    property_id: UUID,
    _: Membership = Depends(require_tenant_access),
    db: Session = Depends(get_db),
) -> Property:
    prop = db.scalar(select(Property).where(
        Property.organization_id == organization_id, Property.id == property_id
    ))
    if prop is None:
        raise HTTPException(status_code=404, detail="Property not found")
    return prop


@router.patch("/organizations/{organization_id}/properties/{property_id}", response_model=PropertyRead)
def update_property(
    payload: PropertyUpdate,
    organization_id: UUID,
    property_id: UUID,
    membership: Membership = Depends(require_owner_admin),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Property:
    prop = db.scalar(select(Property).where(
        Property.organization_id == organization_id, Property.id == property_id
    ))
    if prop is None:
        raise HTTPException(status_code=404, detail="Property not found")
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("brand_id") is not None:
        from app.db.models.identity import Brand
        brand = db.scalar(select(Brand).where(
            Brand.id == changes["brand_id"], Brand.organization_id == organization_id
        ))
        if brand is None:
            raise HTTPException(status_code=404, detail="Brand not found")
    for key, value in changes.items():
        if value is not None:
            setattr(prop, key, value.strip() if key == "name" else value)
    write_audit(db, action="property.update", resource_type="property",
                organization_id=organization_id, actor_user_id=current_user.id,
                resource_id=str(property_id), details={"fields": sorted(changes)})
    try:
        db.commit()
        db.refresh(prop)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Property slug or brand relationship conflicts")
    return prop


@router.delete("/organizations/{organization_id}/properties/{property_id}", status_code=204)
def deactivate_property(
    organization_id: UUID,
    property_id: UUID,
    membership: Membership = Depends(require_owner_admin),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    prop = db.scalar(select(Property).where(
        Property.organization_id == organization_id, Property.id == property_id
    ))
    if prop is None:
        raise HTTPException(status_code=404, detail="Property not found")
    prop.is_active = False
    write_audit(db, action="property.deactivate", resource_type="property",
                organization_id=organization_id, actor_user_id=current_user.id,
                resource_id=str(property_id))
    db.commit()


@router.post("/organizations/{organization_id}/invitations", response_model=InvitationRead, status_code=201)
def create_invitation(
    payload: InvitationCreate,
    organization_id: UUID,
    membership: Membership = Depends(require_owner_admin),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InvitationRead:
    email = str(payload.email).lower()
    existing_user = db.scalar(select(User).where(User.email == email))
    if existing_user and db.scalar(select(Membership.id).where(
        Membership.organization_id == organization_id, Membership.user_id == existing_user.id
    )):
        raise HTTPException(status_code=409, detail="User is already a member of this organization")
    role = MembershipRole(payload.role)
    if role == MembershipRole.owner:
        raise HTTPException(status_code=400, detail="Owner role cannot be assigned by invitation")
    raw_token = secrets.token_urlsafe(36)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=get_settings().invitation_ttl_hours)
    invitation = OrganizationInvitation(
        organization_id=organization_id, email=email, role=role, token_hash=token_hash,
        invited_by_user_id=current_user.id, expires_at=expires_at,
    )
    db.add(invitation)
    write_audit(db, action="member.invitation.create", resource_type="invitation",
                organization_id=organization_id, actor_user_id=current_user.id,
                details={"email": email, "role": role.value})
    db.commit()
    db.refresh(invitation)
    # The raw token is returned exactly once for delivery by the trusted caller/email service.
    return InvitationRead(
        id=invitation.id, email=invitation.email, role=invitation.role.value,
        expires_at=invitation.expires_at, invitation_token=raw_token,
    )


@router.post("/invitations/accept", response_model=dict, status_code=201)
def accept_invitation(payload: InvitationAccept, db: Session = Depends(get_db)) -> dict:
    token_hash = hashlib.sha256(payload.token.encode()).hexdigest()
    invitation = db.scalar(select(OrganizationInvitation).where(
        OrganizationInvitation.token_hash == token_hash,
        OrganizationInvitation.accepted_at.is_(None),
    ).with_for_update())
    now = datetime.now(timezone.utc)
    if invitation is None or invitation.expires_at <= now:
        raise HTTPException(status_code=400, detail="Invitation is invalid or expired")
    user = db.scalar(select(User).where(User.email == invitation.email))
    if user is None:
        user = User(
            email=invitation.email, display_name=payload.display_name.strip(),
            password_hash=hash_password(payload.password),
        )
        db.add(user)
        db.flush()
    elif user.password_hash is None:
        # External-identity accounts must complete account recovery through their IdP.
        raise HTTPException(status_code=409, detail="This account must be activated through its identity provider")
    if db.scalar(select(Membership.id).where(
        Membership.organization_id == invitation.organization_id, Membership.user_id == user.id
    )):
        raise HTTPException(status_code=409, detail="User is already a member of this organization")
    db.add(Membership(
        organization_id=invitation.organization_id, user_id=user.id,
        role=invitation.role, is_active=True,
    ))
    invitation.accepted_at = now
    write_audit(db, action="member.invitation.accept", resource_type="invitation",
                organization_id=invitation.organization_id, actor_user_id=user.id,
                resource_id=str(invitation.id))
    db.commit()
    return {"status": "accepted"}

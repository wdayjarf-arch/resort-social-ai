"""Authentication, tenant access, and role dependencies."""
from collections.abc import Callable
from uuid import UUID

from fastapi import Depends, HTTPException, Path, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.dependencies import get_db
from app.db.models.identity import Membership, MembershipRole, Organization, User
from app.security.jwt import decode_and_validate_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    return decode_and_validate_access_token(db, token)


def require_tenant_access(
    organization_id: UUID = Path(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Membership:
    """Resolve current membership for the organization in the path.

    Never trusts an organization_id claim from a token or request body.
    """
    org = db.scalar(
        select(Organization).where(
            Organization.id == organization_id,
            Organization.is_active.is_(True),
        )
    )
    membership = db.scalar(
        select(Membership).where(
            Membership.organization_id == organization_id,
            Membership.user_id == current_user.id,
            Membership.is_active.is_(True),
        )
    )
    if org is None or membership is None:
        # A 404 avoids disclosing whether another organization's ID exists.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    return membership


def require_roles(*allowed_roles: MembershipRole) -> Callable:
    """Build a dependency that enforces roles using the live membership row."""
    def role_dependency(
        membership: Membership = Depends(require_tenant_access),
    ) -> Membership:
        if membership.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient organization role",
            )
        return membership
    return role_dependency


require_owner = require_roles(MembershipRole.owner)
require_owner_admin = require_roles(MembershipRole.owner, MembershipRole.admin)
require_property_manager = require_roles(
    MembershipRole.owner, MembershipRole.admin, MembershipRole.manager
)
require_agent_or_above = require_roles(
    MembershipRole.owner, MembershipRole.admin, MembershipRole.manager, MembershipRole.agent
)

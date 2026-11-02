from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models.identity import AuditLog


def write_audit(
    db: Session,
    *,
    action: str,
    resource_type: str,
    organization_id: UUID | None,
    actor_user_id: UUID | None,
    resource_id: str | None = None,
    outcome: str = "success",
    request_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Write metadata-only audit records; never include passwords or provider tokens."""
    db.add(AuditLog(
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        request_id=request_id,
        details=details or {},
    ))

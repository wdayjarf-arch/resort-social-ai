import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean, DateTime, Enum, ForeignKey, ForeignKeyConstraint, Index, JSON,
    String, Text, UniqueConstraint, func, text
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False
    )


class MembershipRole(str, enum.Enum):
    owner = "owner"
    admin = "admin"
    manager = "manager"
    agent = "agent"
    publisher = "publisher"
    analyst = "analyst"
    support = "support"
    viewer = "viewer"


class Provider(str, enum.Enum):
    meta = "meta"
    tiktok = "tiktok"
    x = "x"
    youtube = "youtube"
    snapchat = "snapchat"
    whatsapp = "whatsapp"
    telegram = "telegram"


class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    brands: Mapped[list["Brand"]] = relationship(back_populates="organization")
    properties: Mapped[list["Property"]] = relationship(back_populates="organization", overlaps="brand,properties")


class Brand(Base, TimestampMixin):
    __tablename__ = "brands"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_brands_org_id"),
        UniqueConstraint("organization_id", "slug", name="uq_brands_org_slug"),
        ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    organization: Mapped["Organization"] = relationship(back_populates="brands")
    properties: Mapped[list["Property"]] = relationship(back_populates="brand", overlaps="properties,organization")


class Property(Base, TimestampMixin):
    __tablename__ = "properties"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_properties_org_id"),
        UniqueConstraint("organization_id", "slug", name="uq_properties_org_slug"),
        ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["organization_id", "brand_id"], ["brands.organization_id", "brands.id"],
            ondelete="RESTRICT", name="fk_properties_brand_same_org"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    brand_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    timezone: Mapped[str] = mapped_column(String(80), nullable=False, server_default=text("'Asia/Aden'"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    organization: Mapped["Organization"] = relationship(back_populates="properties", overlaps="brand,properties")
    brand: Mapped["Brand | None"] = relationship(back_populates="properties", overlaps="organization,properties")


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    identity_provider_subject: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    memberships: Mapped[list["Membership"]] = relationship(back_populates="user")


class Membership(Base, TimestampMixin):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_membership_org_user"),
        ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        Index("ix_memberships_user_active", "user_id", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    role: Mapped[MembershipRole] = mapped_column(
        Enum(MembershipRole, name="membership_role"), nullable=False, default=MembershipRole.viewer
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    user: Mapped["User"] = relationship(back_populates="memberships")


class SocialAccount(Base, TimestampMixin):
    __tablename__ = "social_accounts"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_social_accounts_org_id"),
        UniqueConstraint("provider", "provider_account_id", name="uq_social_provider_account"),
        ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["organization_id", "property_id"], ["properties.organization_id", "properties.id"],
            ondelete="RESTRICT", name="fk_social_accounts_property_same_org"
        ),
        Index("ix_social_accounts_org_provider", "organization_id", "provider"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    property_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    provider: Mapped[Provider] = mapped_column(Enum(Provider, name="social_provider"), nullable=False)
    provider_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    account_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    granted_scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, default=dict)

    credentials: Mapped[list["OAuthCredential"]] = relationship(
        back_populates="social_account", cascade="all, delete-orphan"
    )


class OAuthCredential(Base, TimestampMixin):
    __tablename__ = "oauth_credentials"
    __table_args__ = (
        UniqueConstraint("social_account_id", "credential_kind", name="uq_oauth_account_credential_kind"),
        ForeignKeyConstraint(
            ["organization_id", "social_account_id"],
            ["social_accounts.organization_id", "social_accounts.id"],
            ondelete="CASCADE", name="fk_oauth_social_account_same_org"
        ),
        Index("ix_oauth_credentials_expiry", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    social_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    credential_kind: Mapped[str] = mapped_column(String(40), nullable=False, default="access_token")
    encrypted_value: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    key_version: Mapped[str] = mapped_column(String(40), nullable=False, server_default=text("'fernet-v1'"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    social_account: Mapped["SocialAccount"] = relationship(back_populates="credentials")


class MetaOAuthState(Base):
    """One-time OAuth state tied to a user and tenant; only its digest is stored."""
    __tablename__ = "meta_oauth_states"
    __table_args__ = (
        UniqueConstraint("state_digest", name="uq_meta_oauth_states_digest"),
        Index("ix_meta_oauth_states_expires", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    state_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MetaWebhookEvent(Base):
    """Durable, deduplicated Meta webhook inbox. Payload retention should be bounded operationally."""
    __tablename__ = "meta_webhook_events"
    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_meta_webhook_events_fingerprint"),
        Index("ix_meta_webhook_events_received", "received_at"),
        Index("ix_meta_webhook_events_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    provider_object: Mapped[str] = mapped_column(String(80), nullable=False)
    event_types: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="received")
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_org_created", "organization_id", "created_at"),
        Index("ix_audit_actor_created", "actor_user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    outcome: Mapped[str] = mapped_column(String(40), nullable=False, default="success")
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuthSession(Base):
    """Server-tracked JWT session; revocation takes effect on the next request."""
    __tablename__ = "auth_sessions"
    __table_args__ = (
        UniqueConstraint("jti", name="uq_auth_sessions_jti"),
        Index("ix_auth_sessions_user_expires", "user_id", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    jti: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class OrganizationInvitation(Base):
    """One-time invitation. Only a SHA-256 digest of the random token is persisted."""
    __tablename__ = "organization_invitations"
    __table_args__ = (
        Index("ix_org_invitations_org_email", "organization_id", "email"),
        Index("ix_org_invitations_expires", "expires_at"),
        ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["invited_by_user_id"], ["users.id"], ondelete="SET NULL"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[MembershipRole] = mapped_column(
        Enum(MembershipRole, name="membership_role", create_type=False), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    invited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

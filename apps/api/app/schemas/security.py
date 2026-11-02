from datetime import datetime
from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints
from typing import Annotated
from uuid import UUID

Password = Annotated[str, StringConstraints(min_length=12, max_length=1024)]


class RegisterRequest(BaseModel):
    email: EmailStr
    display_name: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    password: Password


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: EmailStr
    display_name: str


class OrganizationCreate(BaseModel):
    name: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    slug: Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=2, max_length=120)]


class OrganizationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    slug: str
    is_active: bool


class OrganizationUpdate(BaseModel):
    name: Annotated[str, StringConstraints(min_length=1, max_length=200)] | None = None
    slug: Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=2, max_length=120)] | None = None


class PropertyCreate(BaseModel):
    name: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    slug: Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=2, max_length=120)]
    timezone: Annotated[str, StringConstraints(min_length=1, max_length=80)] = "Asia/Aden"
    brand_id: UUID | None = None


class PropertyUpdate(BaseModel):
    name: Annotated[str, StringConstraints(min_length=1, max_length=200)] | None = None
    slug: Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=2, max_length=120)] | None = None
    timezone: Annotated[str, StringConstraints(min_length=1, max_length=80)] | None = None
    brand_id: UUID | None = None
    is_active: bool | None = None


class PropertyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    organization_id: UUID
    brand_id: UUID | None
    name: str
    slug: str
    timezone: str
    is_active: bool


class InvitationCreate(BaseModel):
    email: EmailStr
    role: str = Field(pattern=r"^(admin|manager|agent|viewer|analyst|publisher|support)$")


class InvitationRead(BaseModel):
    id: UUID
    email: EmailStr
    role: str
    expires_at: datetime
    invitation_token: str


class InvitationAccept(BaseModel):
    token: str = Field(min_length=32, max_length=256)
    display_name: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    password: Password

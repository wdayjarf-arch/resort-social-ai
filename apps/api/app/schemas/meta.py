from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints
from typing import Annotated, Literal
from uuid import UUID

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]
RecipientId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


class MetaAuthorizeResponse(BaseModel):
    authorization_url: HttpUrl


class MetaOAuthResult(BaseModel):
    status: Literal["connected"] = "connected"
    connected_pages: int
    connected_instagram_accounts: int


class MetaPublishRequest(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2200)]
    image_url: HttpUrl | None = None


class MetaOperationResult(BaseModel):
    provider: str = "meta"
    account_id: str
    result_id: str | None = None
    status: str = "submitted"
    raw: dict = Field(default_factory=dict)


class MetaDirectMessageReplyRequest(BaseModel):
    recipient_id: RecipientId
    text: NonEmptyText


class SocialAccountOperationContext(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    organization_id: UUID

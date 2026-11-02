"""Resort knowledge items and embeddings."""
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKeyConstraint, Index, JSON, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
try:
    from pgvector.sqlalchemy import Vector
except ImportError:  # Allows model-only SQLite tests before optional deps are installed.
    from sqlalchemy.types import TypeDecorator, JSON as SQLAlchemyJSON

    class Vector(TypeDecorator):
        impl = SQLAlchemyJSON
        cache_ok = True

        def __init__(self, dimensions: int):
            self.dimensions = dimensions
            super().__init__()

        def cosine_distance(self, other):
            return self.op("<=>")(other)

from app.db.base import Base

EMBEDDING_DIMENSIONS = 1536


class ResortKnowledgeItem(Base):
    __tablename__ = "resort_knowledge_items"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_knowledge_org_id"),
        ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["organization_id", "property_id"], ["properties.organization_id", "properties.id"], ondelete="RESTRICT"),
        Index("ix_knowledge_org_category_active", "organization_id", "category", "is_active"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    property_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, default=dict)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

# Allow lightweight SQLite unit tests; production migrations use PostgreSQL's native VECTOR.
from sqlalchemy.ext.compiler import compiles


@compiles(Vector, "sqlite")
def _compile_vector_sqlite(_type, _compiler, **_kw):
    return "JSON"

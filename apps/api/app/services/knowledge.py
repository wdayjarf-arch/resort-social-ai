"""Tenant-filtered resort knowledge authoring and semantic retrieval."""
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.providers import embed_text
from app.db.models.knowledge import ResortKnowledgeItem


@dataclass(frozen=True)
class KnowledgeSnippet:
    id: UUID
    category: str
    title: str
    content: str
    similarity: float


class KnowledgeService:
    def __init__(self, db: Session):
        self.db = db

    def upsert_embedding(self, item: ResortKnowledgeItem) -> None:
        item.embedding = embed_text(
            f"{item.category}: {item.title}\n{item.content}",
            purpose="document", title=item.title,
        )

    def retrieve(self, *, organization_id: UUID, query: str, top_k: int = 5, property_id: UUID | None = None) -> list[KnowledgeSnippet]:
        vector = embed_text(query, purpose="query")
        distance = ResortKnowledgeItem.embedding.cosine_distance(vector)
        stmt = select(ResortKnowledgeItem, distance.label("distance")).where(
            ResortKnowledgeItem.organization_id == organization_id,
            ResortKnowledgeItem.is_active.is_(True),
            ResortKnowledgeItem.embedding.is_not(None),
        )
        if property_id:
            stmt = stmt.where((ResortKnowledgeItem.property_id == property_id) | (ResortKnowledgeItem.property_id.is_(None)))
        rows = self.db.execute(stmt.order_by(distance).limit(max(1, min(top_k, 10)))).all()
        return [KnowledgeSnippet(id=item.id, category=item.category, title=item.title, content=item.content,
                                 similarity=max(0.0, min(1.0, 1.0 - float(dist)))) for item, dist in rows]

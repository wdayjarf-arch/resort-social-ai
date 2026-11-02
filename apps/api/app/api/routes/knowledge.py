"""Resort knowledge authoring and tenant-filtered semantic search."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.providers import AIProviderError
from app.api.deps import require_roles
from app.db.dependencies import get_db
from app.db.models.identity import Membership, MembershipRole, Property
from app.db.models.knowledge import ResortKnowledgeItem
from app.schemas.operations import KnowledgeCreate, KnowledgeOut, KnowledgeSearchRequest
from app.services.knowledge import KnowledgeService

router = APIRouter(tags=["Resort knowledge"])
READ_ROLES = (MembershipRole.owner, MembershipRole.admin, MembershipRole.manager, MembershipRole.agent, MembershipRole.support)
WRITE_ROLES = (MembershipRole.owner, MembershipRole.admin, MembershipRole.manager)


@router.get("/organizations/{organization_id}/knowledge", response_model=list[KnowledgeOut])
def list_knowledge(organization_id: UUID, _: Membership = Depends(require_roles(*READ_ROLES)), db: Session = Depends(get_db)):
    return db.scalars(select(ResortKnowledgeItem).where(
        ResortKnowledgeItem.organization_id == organization_id,
        ResortKnowledgeItem.is_active.is_(True),
    ).order_by(ResortKnowledgeItem.category, ResortKnowledgeItem.title)).all()


@router.post("/organizations/{organization_id}/knowledge", response_model=KnowledgeOut, status_code=201)
def create_knowledge(organization_id: UUID, body: KnowledgeCreate, _: Membership = Depends(require_roles(*WRITE_ROLES)), db: Session = Depends(get_db)):
    if body.property_id and not db.scalar(select(Property.id).where(Property.organization_id == organization_id, Property.id == body.property_id, Property.is_active.is_(True))):
        raise HTTPException(status_code=404, detail="Property not found")
    row = ResortKnowledgeItem(
        organization_id=organization_id, property_id=body.property_id, category=body.category.lower(),
        title=body.title, content=body.content, source_url=str(body.source_url) if body.source_url else None,
        metadata_json=body.metadata, is_active=True,
    )
    db.add(row)
    try:
        if body.embed_now:
            KnowledgeService(db).upsert_embedding(row)
        db.commit()
        db.refresh(row)
        return row
    except AIProviderError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Embedding service unavailable; check AI_PROVIDER and its server-side API key") from exc


@router.put("/organizations/{organization_id}/knowledge/{item_id}", response_model=KnowledgeOut)
def update_knowledge(organization_id: UUID, item_id: UUID, body: KnowledgeCreate, _: Membership = Depends(require_roles(*WRITE_ROLES)), db: Session = Depends(get_db)):
    row = db.scalar(select(ResortKnowledgeItem).where(ResortKnowledgeItem.organization_id == organization_id, ResortKnowledgeItem.id == item_id))
    if not row:
        raise HTTPException(status_code=404, detail="Knowledge item not found")
    if body.property_id and not db.scalar(select(Property.id).where(Property.organization_id == organization_id, Property.id == body.property_id, Property.is_active.is_(True))):
        raise HTTPException(status_code=404, detail="Property not found")
    row.property_id = body.property_id
    row.category = body.category.lower()
    row.title = body.title
    row.content = body.content
    row.source_url = str(body.source_url) if body.source_url else None
    row.metadata_json = body.metadata
    row.is_active = True
    row.embedding = None
    try:
        if body.embed_now:
            KnowledgeService(db).upsert_embedding(row)
        db.commit()
        db.refresh(row)
        return row
    except AIProviderError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Embedding service unavailable; update was not saved") from exc


@router.delete("/organizations/{organization_id}/knowledge/{item_id}", status_code=204)
def deactivate_knowledge(organization_id: UUID, item_id: UUID, _: Membership = Depends(require_roles(*WRITE_ROLES)), db: Session = Depends(get_db)):
    row = db.scalar(select(ResortKnowledgeItem).where(ResortKnowledgeItem.organization_id == organization_id, ResortKnowledgeItem.id == item_id))
    if not row:
        raise HTTPException(status_code=404, detail="Knowledge item not found")
    row.is_active = False
    db.commit()


@router.post("/organizations/{organization_id}/knowledge/search")
def search_knowledge(organization_id: UUID, body: KnowledgeSearchRequest, _: Membership = Depends(require_roles(*READ_ROLES)), db: Session = Depends(get_db)):
    if body.property_id and not db.scalar(select(Property.id).where(Property.organization_id == organization_id, Property.id == body.property_id)):
        raise HTTPException(status_code=404, detail="Property not found")
    try:
        snippets = KnowledgeService(db).retrieve(organization_id=organization_id, query=body.query, top_k=body.top_k, property_id=body.property_id)
    except AIProviderError as exc:
        raise HTTPException(status_code=503, detail="Semantic retrieval is unavailable; check AI_PROVIDER, API key, and embedding configuration") from exc
    return [{"id": str(s.id), "category": s.category, "title": s.title, "content": s.content, "similarity": s.similarity} for s in snippets]

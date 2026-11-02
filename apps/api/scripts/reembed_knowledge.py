"""Rebuild active resort-knowledge vectors after changing embedding providers.

Run from apps/api after setting AI_PROVIDER=gemini and GEMINI_API_KEY in the
root .env file: python -m scripts.reembed_knowledge
"""
from __future__ import annotations

import argparse

from sqlalchemy import select

from app.core.config import get_settings
from app.db.models.knowledge import ResortKnowledgeItem
from app.db.session import SessionLocal
from app.services.knowledge import KnowledgeService


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild resort knowledge embeddings")
    parser.add_argument("--limit", type=int, default=0, help="Optional maximum number of active records")
    parser.add_argument("--batch-size", type=int, default=25, help="Commit after this many records")
    args = parser.parse_args()
    if args.limit < 0 or args.batch_size < 1:
        parser.error("--limit must be >= 0 and --batch-size must be >= 1")

    settings = get_settings()
    print(f"Provider: {settings.ai_provider}; embedding model: {settings.ai_embedding_model}")
    if settings.ai_provider == "gemini" and not (settings.gemini_api_key and settings.gemini_api_key.get_secret_value().strip()):
        raise SystemExit("GEMINI_API_KEY is missing or empty; no records were changed.")

    db = SessionLocal()
    updated = 0
    try:
        stmt = select(ResortKnowledgeItem).where(ResortKnowledgeItem.is_active.is_(True)).order_by(ResortKnowledgeItem.created_at)
        if args.limit:
            stmt = stmt.limit(args.limit)
        rows = db.scalars(stmt).all()
        total = len(rows)
        print(f"Active knowledge records to re-embed: {total}")
        service = KnowledgeService(db)
        for row in rows:
            service.upsert_embedding(row)
            updated += 1
            if updated % args.batch_size == 0:
                db.commit()
                print(f"Updated {updated}/{total}", flush=True)
        db.commit()
        print(f"Finished: {updated} active knowledge records re-embedded.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()

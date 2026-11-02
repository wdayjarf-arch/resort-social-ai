from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import engine
from app.api.routes.auth import router as auth_router
from app.api.routes.organizations import router as organizations_router
from app.api.routes.social_accounts import router as social_accounts_router
from app.api.routes.meta_integrations import router as meta_integrations_router
from app.api.routes.meta_webhooks import router as meta_webhooks_router
from app.api.routes.social_webhooks import router as social_webhooks_router
from app.api.routes.inbox import router as inbox_router
from app.api.routes.knowledge import router as knowledge_router
from app.api.routes.scheduled_posts import router as scheduled_posts_router
from app.api.routes.analytics import router as analytics_router
from app.api.routes.crm import router as crm_router
from app.api.routes.ai_assistant import router as ai_assistant_router

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Fail early if database credentials/configuration are invalid.
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    yield
    engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    debug=settings.app_debug,
    lifespan=lifespan,
)

app.include_router(auth_router, prefix=settings.api_v1_prefix)
app.include_router(organizations_router, prefix=settings.api_v1_prefix)
app.include_router(social_accounts_router, prefix=settings.api_v1_prefix)
app.include_router(meta_integrations_router, prefix=settings.api_v1_prefix)
app.include_router(meta_webhooks_router, prefix=settings.api_v1_prefix)
app.include_router(social_webhooks_router, prefix=settings.api_v1_prefix)
app.include_router(inbox_router, prefix=settings.api_v1_prefix)
app.include_router(knowledge_router, prefix=settings.api_v1_prefix)
app.include_router(scheduled_posts_router, prefix=settings.api_v1_prefix)
app.include_router(analytics_router, prefix=settings.api_v1_prefix)
app.include_router(crm_router, prefix=settings.api_v1_prefix)
app.include_router(ai_assistant_router, prefix=settings.api_v1_prefix)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)


@app.get("/health/live", tags=["health"])
def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
def ready() -> dict[str, str]:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return {"status": "ready", "database": "ok"}

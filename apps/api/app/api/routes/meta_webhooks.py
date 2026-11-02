"""Meta webhook handshake and signature-verified async ingress."""
import hashlib
import hmac
import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.dependencies import get_db
from app.db.models.identity import MetaWebhookEvent, Provider, SocialAccount
from app.workers.tasks import process_meta_webhook_event

router = APIRouter(tags=["Meta webhooks"])
MAX_WEBHOOK_BYTES = 2 * 1024 * 1024


def _verify_signature(raw_body: bytes, signature: str | None) -> bool:
    settings = get_settings()
    configured = settings.meta_webhook_app_secret or settings.meta_app_secret
    if configured is None or not signature or not signature.startswith("sha256="):
        return False
    expected = hmac.new(
        configured.get_secret_value().encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature.removeprefix("sha256="))


@router.get("/webhooks/meta", response_class=PlainTextResponse)
def verify_meta_webhook(
    request: Request,
) -> PlainTextResponse:
    settings = get_settings()
    mode = request.query_params.get("hub.mode")
    verify_token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    expected = settings.meta_webhook_verify_token
    if expected is None:
        raise HTTPException(status_code=503, detail="Meta webhook verification is not configured")
    if mode == "subscribe" and verify_token and hmac.compare_digest(
        verify_token, expected.get_secret_value()
    ) and challenge is not None:
        return PlainTextResponse(challenge, status_code=200)
    raise HTTPException(status_code=403, detail="Webhook verification failed")


@router.post("/webhooks/meta", status_code=status.HTTP_200_OK)
async def receive_meta_webhook(request: Request, db: Session = Depends(get_db)) -> Response:
    raw_body = await request.body()
    if len(raw_body) > MAX_WEBHOOK_BYTES:
        raise HTTPException(status_code=413, detail="Webhook payload too large")
    signature = request.headers.get("X-Hub-Signature-256")
    if not _verify_signature(raw_body, signature):
        raise HTTPException(status_code=401, detail="Invalid Meta webhook signature")
    try:
        payload = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Webhook JSON must be an object")
    fingerprint = hashlib.sha256(raw_body).hexdigest()
    existing = db.scalar(select(MetaWebhookEvent).where(MetaWebhookEvent.fingerprint == fingerprint))
    if existing is None:
        entries = payload.get("entry", [])
        entry_ids = [str(entry["id"]) for entry in entries if isinstance(entry, dict) and entry.get("id") is not None] if isinstance(entries, list) else []
        linked_accounts = []
        if entry_ids:
            linked_accounts = list(db.scalars(select(SocialAccount).where(
                SocialAccount.provider == Provider.meta,
                SocialAccount.provider_account_id.in_(entry_ids),
                SocialAccount.is_active.is_(True),
            )).all())
        tenant_ids = {account.organization_id for account in linked_accounts}
        # A webhook request can batch entries for multiple accounts. Do not assign a mixed
        # batch to one tenant; organization_id remains NULL unless the batch is unambiguous.
        event_organization_id = next(iter(tenant_ids)) if len(tenant_ids) == 1 else None
        event = MetaWebhookEvent(
            fingerprint=fingerprint,
            organization_id=event_organization_id,
            provider_object=str(payload.get("object") or "unknown")[:80],
            event_types=[],
            payload=payload,
            status="received",
        )
        db.add(event)
        try:
            db.commit()
            db.refresh(event)
        except IntegrityError:
            db.rollback()
            existing = db.scalar(select(MetaWebhookEvent).where(MetaWebhookEvent.fingerprint == fingerprint))
            if existing is None:
                raise HTTPException(status_code=500, detail="Could not persist webhook delivery")
            event = existing
    else:
        event = existing

    # The verified event is durable before acknowledgement. Queue failures do not block or
    # lose the delivery; Celery Beat periodically requeues rows left in `received` status.
    if event.status == "received":
        try:
            process_meta_webhook_event.apply_async(args=[str(event.id)])
        except Exception:
            pass
    return Response(status_code=200)

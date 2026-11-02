# Phase 3 — Meta Provider Integration

This phase adds Facebook Page / Messenger and linked Instagram Professional account integration to the Phase 2.1 API.

## Included

- Tenant-authorized OAuth start endpoint with random, single-use state stored as a SHA-256 digest.
- Authorization-code exchange followed by short-lived to long-lived user-token exchange.
- Page discovery through `/me/accounts`, including linked `instagram_business_account` records.
- Page and user tokens encrypted with the existing Fernet `TOKEN_ENCRYPTION_KEY` before persistence in `oauth_credentials`.
- Meta webhook GET verification and raw-body `X-Hub-Signature-256` validation.
- Durable webhook inbox, SHA-256 delivery deduplication, basic event classification, Celery processing, and periodic recovery of queued events.
- Facebook Page post/photo publishing, Instagram image publishing, and Messenger / Instagram DM reply operations.
- Tenant-scoped account access and organization role checks on all operational endpoints.

## Configuration

Set these values in `.env` before starting the stack:

```dotenv
META_APP_ID=your-meta-app-id
META_APP_SECRET=your-meta-app-secret
META_API_VERSION=v25.0
META_OAUTH_REDIRECT_URI=https://api.example.com/api/v1/integrations/meta/callback
META_WEBHOOK_VERIFY_TOKEN=generate-a-long-random-webhook-verification-token
META_WEBHOOK_APP_SECRET=your-meta-app-secret
META_OAUTH_STATE_TTL_MINUTES=10
```

`META_WEBHOOK_APP_SECRET` can be left empty to use `META_APP_SECRET`. The OAuth redirect URI must exactly match the URI registered in the Meta app dashboard. Keep `.env`, app secrets, Fernet keys, and database backups out of source control. Use a stable Fernet key and a documented key-rotation process; changing it without re-encrypting credentials makes stored tokens unreadable.

The authorization scopes include the requested scopes plus `instagram_content_publish` (required for Instagram content publishing) and `pages_manage_metadata` (commonly required for Page webhook subscriptions). Only permissions reported as granted by Meta are recorded. Some permissions require Meta App Review / Advanced Access before production use.

## Migrations and start

```bash
cd apps/api
uv sync
uv run alembic upgrade head
uv run pytest -q
```

From the repository root, `docker compose up --build` starts PostgreSQL, Redis, the API, a Celery worker, and Celery Beat. The Beat service requeues durable webhook inbox rows that remained in `received` status if Redis was unavailable at delivery time.

## API endpoints

- `GET /api/v1/organizations/{organization_id}/integrations/meta/authorize` — Owner/Admin only; returns `authorization_url` for the browser to follow.
- `GET /api/v1/integrations/meta/callback` — Meta OAuth callback; validates and consumes the one-time state.
- `GET /api/v1/webhooks/meta` — Meta webhook handshake; echoes `hub.challenge` only when the verify token matches.
- `POST /api/v1/webhooks/meta` — verifies signature, durably records the delivery, queues asynchronous work, and returns HTTP 200. Invalid signatures receive 401. Database persistence errors return non-200 so Meta can retry.
- `POST /api/v1/organizations/{organization_id}/social-accounts/{account_id}/publish` — Owner/Admin/Manager/Publisher; body `{ "text": "...", "image_url": "https://..." }`. Facebook accepts text-only or image + caption; Instagram feed publishing requires a publicly reachable HTTPS image URL.
- `POST /api/v1/organizations/{organization_id}/social-accounts/{account_id}/messages/reply` — Owner/Admin/Manager/Agent/Support; body `{ "recipient_id": "...", "text": "..." }`.

Use a bearer access token for authenticated endpoints. Never accept an organization ID from the body as an authorization substitute; the organization path and live membership are checked by the existing Phase 2.1 dependencies.

## Meta dashboard checklist

1. Configure Facebook Login / Facebook Login for Business and add the appropriate Messenger and Instagram products.
2. Register the exact HTTPS OAuth redirect URI above.
3. Configure the Webhooks callback as `https://api.example.com/api/v1/webhooks/meta` and use the exact same verify token as `META_WEBHOOK_VERIFY_TOKEN`.
4. Subscribe the app to the Page and Instagram webhook fields needed by the product (typically Page `messages`, `messaging_postbacks`, `feed` / comment notifications as appropriate; Instagram `messages` and `comments`). Subscribe each connected Page/account where Meta requires per-account subscription.
5. Ensure the Instagram account is a Professional account and linked to a Facebook Page for the Facebook Login integration path.
6. Complete Meta App Review and business verification for the permissions and use cases required by your customers.

## Webhook processing boundary

The current foundation stores each verified, deduplicated payload in `meta_webhook_events`, maps its `entry.id` to a tenant when the batch maps unambiguously to one organization, and classifies Messenger DMs, Instagram DMs, and comments asynchronously. Mixed-tenant batches are deliberately left without a single `organization_id`. The Phase 2.1 codebase does not yet define conversation/message/comment domain tables, so this phase intentionally keeps the raw payload as the durable inbox record; a subsequent conversation-ingestion phase should normalize those records into those tables and apply retention/deletion policies. Do not expose this table or raw payloads to tenant users without tenant-scoped authorization.

## Production hardening notes

- Webhook handlers do no AI or provider business processing inline. They validate, persist, enqueue, and acknowledge. If queue dispatch fails after persistence, the delivery is still acknowledged because the durable inbox plus Celery Beat recovery prevents loss.
- Keep Redis private, authenticated, and TLS-protected where supported. Do not log request bodies, authorization headers, access tokens, or full OAuth callback URLs.
- Apply a retention policy to `meta_webhook_events`; webhook payloads can contain personal data. Encrypt database volumes/backups and restrict operational access.
- Meta can retry deliveries and send batches; deduplication is based on the raw request-body fingerprint. A semantically identical payload with different JSON formatting is treated as a separate delivery.
- Graph API permissions, messaging windows, recipient eligibility, and account capabilities are enforced by Meta and can change. Errors are returned as a generic 502 to avoid leaking token-bearing provider details.
- Publishing and replies are explicit API calls. No automatic AI reply is sent by webhook ingestion.

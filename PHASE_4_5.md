# Phase 4 & 5 — Unified Inbox, Resort Knowledge, AI Engagement, Scheduling

This phase extends the Phase 2.1/3 project. PostgreSQL is the source of truth; Celery/Redis handle webhook normalization, AI work, approved replies, and scheduled publication.

## Added domain tables

- `conversations`: tenant/account-scoped Messenger and Instagram DM threads.
- `messages`: inbound/outbound messages, provider IDs, delivery state, reply linkage, AI intent/confidence, and human-approval status.
- `post_engagements`: Facebook/Instagram comments, parent post/comment IDs, drafted replies, and reply status.
- `scheduled_posts` + `scheduled_post_targets`: one scheduled content item with normalized per-account execution state/results.
- `resort_knowledge_items`: tenant/property-scoped approved resort facts with 1536-dimensional pgvector embeddings.

`0004_unified_ops` enables the PostgreSQL `vector` extension and adds a cosine-distance HNSW index. The Compose file already uses `pgvector/pgvector:pg16`.

## Environment

Add these values to `.env` (never commit real keys):

```dotenv
AI_AUTO_REPLY_ENABLED=false
AI_REQUIRE_HUMAN_APPROVAL=true
AI_PROVIDER=openai
AI_MODEL=gpt-4.1-mini
AI_EMBEDDING_MODEL=text-embedding-3-small
AI_CONFIDENCE_THRESHOLD=0.88
AI_MIN_RETRIEVAL_SIMILARITY=0.68
AI_MAX_REPLY_CHARS=1800
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
```

The current adapter uses OpenAI embeddings for pgvector in either chat-provider mode. Therefore `OPENAI_API_KEY` is required for embedding/semantic search. For `AI_PROVIDER=openai`, it is also used for chat. For `AI_PROVIDER=anthropic`, set `ANTHROPIC_API_KEY` for chat and `OPENAI_API_KEY` for embeddings.

## Setup

From the project root:

```bash
cp .env.example .env
# Fill in the secrets and provider keys.
docker compose up --build
```

The API container runs `alembic upgrade head` on startup. To run tests inside the API container:

```bash
docker compose exec api pytest -q
```

## API endpoints

All routes use `/api/v1` prefix and require a valid session/token plus organization membership unless noted.

### Unified inbox

- `GET /organizations/{organization_id}/conversations?status=open&limit=50&offset=0`
- `GET /organizations/{organization_id}/conversations/{conversation_id}/messages`
- `PATCH /organizations/{organization_id}/conversations/{conversation_id}/status?status=resolved`
- `POST /organizations/{organization_id}/conversations/{conversation_id}/reply` — human-sent DM reply
- `POST /organizations/{organization_id}/messages/{message_id}/approve` — optional `{"edited_text":"..."}`; sends asynchronously
- `GET /organizations/{organization_id}/post-engagements?status=needs_human_approval`
- `POST /organizations/{organization_id}/post-engagements/{engagement_id}/approve` — optional edited reply; sends asynchronously
- `POST /organizations/{organization_id}/post-engagements/{engagement_id}/reply` — manual comment reply, `{"text":"...","send_now":true}`

### Knowledge base

- `GET /organizations/{organization_id}/knowledge`
- `POST /organizations/{organization_id}/knowledge`
- `PUT /organizations/{organization_id}/knowledge/{item_id}`
- `DELETE /organizations/{organization_id}/knowledge/{item_id}` — soft deactivation
- `POST /organizations/{organization_id}/knowledge/search` — `{"query":"thermal pool hours","top_k":5,"property_id":null}`

Example create payload:

```json
{
  "category": "thermal_pools",
  "title": "Thermal pool opening hours",
  "content": "The outdoor thermal pools are open daily from 08:00 to 21:00. Confirm seasonal hours before publishing this fact.",
  "property_id": null,
  "source_url": null,
  "metadata": {"approved_by": "operations"},
  "embed_now": true
}
```

Only enter facts approved by resort operations. The sample is a schema example, not an assertion about any actual resort's hours.

### Scheduled publishing

- `POST /organizations/{organization_id}/scheduled-posts`
- `GET /organizations/{organization_id}/scheduled-posts?status=scheduled`
- `POST /organizations/{organization_id}/scheduled-posts/{post_id}/cancel`

Example:

```json
{
  "content": "Discover a quieter way to unwind.",
  "media_urls": ["https://cdn.example.com/approved-resort-photo.jpg"],
  "target_account_ids": ["00000000-0000-0000-0000-000000000000"],
  "scheduled_at": "2027-01-10T16:00:00+03:00"
}
```

Scheduling requires a timezone-aware future timestamp and active Meta account IDs belonging to the same organization. This first version supports text-only or one-image Facebook Page posts and one-image Instagram feed posts. Instagram publishing needs a public HTTPS image URL and the relevant Meta publishing permission.

## Webhook-to-inbox pipeline

1. The Phase 3 endpoint verifies `X-Hub-Signature-256` against the raw body.
2. It persists a fingerprint-deduplicated `MetaWebhookEvent` before returning HTTP 200.
3. Celery normalizes incoming DM/comment records into `conversations`, `messages`, and `post_engagements` using provider IDs for idempotency.
4. New inbound records are dispatched to the AI task after commit. Delivery/read receipts update message status when provider message IDs match.
5. Duplicate deliveries re-dispatch unprocessed normalized rows when needed, avoiding a lost AI job if Redis was temporarily unavailable.

## AI policy

The agent retrieves only active knowledge for the matching organization, and optionally the connected account's property. It labels intent and confidence, drafts a reply, and fails closed on provider/retrieval errors.

- Informational/general greeting can be sent automatically only when `AI_AUTO_REPLY_ENABLED=true`, `AI_REQUIRE_HUMAN_APPROVAL=false`, confidence exceeds the configured threshold, and retrieval grounding meets the configured similarity threshold for factual inquiries.
- Booking requests, complaints, unknown intent, weak grounding, and low confidence are kept as `needs_human_approval` drafts in the inbox.
- The model is instructed to never claim a booking is confirmed or invent resort facts. Deterministic English/Arabic escalation cues override the model's intent classification for common booking/complaint phrases.
- Start with auto-send disabled. Populate and review the knowledge base, test with real resort policies, and only then consider enabling automatic informational replies.

## Celery Beat tasks

- `app.meta.requeue_pending_webhooks` — recovers durable webhook rows when queue dispatch was unavailable.
- `app.inbox.requeue_approved_replies` — requeues approved inbox drafts if a queue dispatch was lost.
- `app.scheduler.publish_due_posts` — publishes due schedules and retries failed targets up to three attempts while skipping targets already published.
- `app.scheduler.recover_stuck_publications` — recovers jobs left in `publishing` after a worker crash.

## Operational notes before production

- Apply Meta App Review and permission approval for messaging, comments, and publishing; configure webhook subscriptions for the connected Pages/Instagram accounts.
- Provider API sends are external side effects. No database can guarantee exactly-once delivery if a worker crashes after Meta accepts a send but before the success commit. Monitor provider IDs and failed/uncertain operations before manually retrying.
- Add retention policies for raw webhook payloads, rate limits, alerting, dead-letter handling, and a reviewed human escalation SLA before enabling unattended automation.
- Keep tenant IDs in every repository predicate. All inbox, knowledge, and scheduling endpoints scope reads/writes by `organization_id`; target account IDs are revalidated against that tenant.
- Verify account-level Meta permissions and the 24-hour messaging window/policy before attempting replies. The app must not send outside the permitted messaging context.

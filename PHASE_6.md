# Phase 6 — Audience Analytics, Peak-Time Engine & Dashboard

## What is included
- Tenant-scoped `analytics_snapshots` persistence and Alembic migration `0005_audience_analytics`.
- Meta Insights ingestion for Facebook Page and Instagram Business accounts; per-metric failures are tolerated because Meta metrics/scopes vary by account and Graph API version.
- Activity heatmap (7 x 24) normalized from timestamped inbound DMs and post comments, plus top slots and a recommended future slot.
- Dashboard summary metrics: inbound messages handled, AI-only sent replies, human-reviewed replies, pending approvals, and completed publishing success/failure rates.
- Smart queue endpoint that schedules at the best observed hour: `POST /api/v1/organizations/{organization_id}/scheduled-posts/suggested` (same request body as regular scheduling; optional `?timezone=Asia/Aden`).
- Next.js + Tailwind + Lucide single-page operations dashboard with overview, inbox, calendar, knowledge form, account hub, analytics heatmap.

## Important analytics semantics
Meta Insights mostly supplies period aggregates (daily reach/impressions/engagement), not a timestamp for every like. The engine does **not** fabricate hourly likes. It learns day/hour activity from actual inbound message and comment timestamps, stores daily Meta aggregates separately, and exposes the observation count/confidence so sparse data is visible. A more statistically reliable publishing model needs a longer event history and, where permitted, post-level insights matched to individual post publication times.

## Run locally with Docker Compose
1. Copy `.env.example` to `.env` and replace `POSTGRES_PASSWORD`, `TOKEN_ENCRYPTION_KEY`, and `JWT_SECRET_KEY` with strong random values. Set Meta app settings and `OPENAI_API_KEY` for embeddings/knowledge base.
2. `docker compose up --build -d`
3. Check API: `http://localhost:8000/health/ready`; Swagger: `http://localhost:8000/docs`; dashboard: `http://localhost:3000`.
4. Authenticate using the configured identity flow, enter the access token and organization UUID in dashboard → Connected Accounts. Connect Meta accounts through the API's Meta OAuth routes, then run Insights sync.
5. Create/edit knowledge through `/api/v1/organizations/{id}/knowledge`; the frontend form embeds immediately and therefore requires a working OpenAI embedding key.
6. Use the dashboard's API settings to load analytics. For direct scheduling, POST to `/organizations/{id}/scheduled-posts/suggested` with `content`, `target_account_ids`, optional `media_urls`, and a timezone query parameter.

## API endpoints
- `POST /organizations/{id}/analytics/ingest?days=30[&account_id=UUID]`
- `GET /organizations/{id}/analytics/heatmap?timezone=Asia/Aden&days=90[&account_id=UUID]`
- `GET /organizations/{id}/analytics/suggest-slot?account_ids=UUID&timezone=Asia/Aden`
- `GET /organizations/{id}/dashboard/summary`
- `POST /organizations/{id}/scheduled-posts/suggested?timezone=Asia/Aden`

All endpoints are protected by the existing JWT + active membership checks. `analyst` role can ingest and query analytics; read roles can access dashboard summary and heatmap. The UI contains demo rows and KPI placeholders until live API responses are connected; it is a frontend foundation, not a claim that live data was populated in this build environment.

## Deployment notes
- Meta must approve the permissions required by your enabled messaging, publishing, and Insights features. Metric names vary by account type and API version; review the ingestion results for per-account errors.
- `NEXT_PUBLIC_API_URL` is a build-time value. For a public deployment, set it to the HTTPS API origin and configure `CORS_ORIGINS` accordingly. Do not expose the API, Postgres, or Redis publicly without TLS, network restrictions, rate limiting, and production identity management.
- The repository's existing identity foundation notes that a trusted OIDC/session strategy should be configured before public production exposure. Do that before going live.

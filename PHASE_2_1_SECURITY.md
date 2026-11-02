# Phase 2.1 — Security & Tenant Access Control

## Routes

- `POST /api/v1/auth/register` — register a password user (does not grant organization access).
- `POST /api/v1/auth/login` — OAuth2 password form fields `username` (email) and `password`.
- `GET /api/v1/auth/me` — current authenticated user.
- `POST /api/v1/auth/logout` — revoke the current bearer session.
- `POST /api/v1/organizations` — create organization; caller becomes owner.
- `GET/PATCH/DELETE /api/v1/organizations/{organization_id}` — organization read/update/deactivate.
- `POST/GET /api/v1/organizations/{organization_id}/properties` — create/list properties.
- `GET/PATCH/DELETE /api/v1/organizations/{organization_id}/properties/{property_id}` — property read/update/deactivate.
- `POST /api/v1/organizations/{organization_id}/invitations` — create one-time invitation.
- `POST /api/v1/invitations/accept` — accept invitation and create membership.
- `GET /api/v1/organizations/{organization_id}/social-accounts` — tenant-scoped account list.
- `GET /api/v1/organizations/{organization_id}/social-accounts/{account_id}` — tenant-scoped account read.

## Local setup

Add a unique `JWT_SECRET_KEY` (at least 32 bytes) to `.env`. The project uses Argon2id via `pwdlib[argon2]` and PyJWT.

```bash
docker compose up --build
docker compose exec api pytest -q
```

If running outside Docker:
```bash
cd apps/api
uv lock
uv sync --group dev
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
uv run pytest -q
```

## Invitation delivery

The invitation endpoint returns the one-time raw token to the authenticated organization administrator. Persisted storage contains only its SHA-256 digest. Deliver the returned token through a trusted email provider; do not log it or place it in analytics. Add rate limiting and abuse controls before exposing registration/invitation endpoints publicly.

## Role policy

- Owner: full organization management.
- Admin: organization/property management and invitations.
- Manager: agent-level operational access; property administration is intentionally not granted in this initial policy.
- Agent: read properties and connected account metadata.
- Existing `publisher`, `analyst`, `support`, and `viewer` roles remain supported for compatibility.

## Production checklist

- Configure a unique high-entropy JWT secret via a secret manager; never use the example value.
- Use HTTPS, a trusted reverse proxy, request-size limits, and rate limiting for login/register/invitation endpoints.
- Consider a managed identity provider or MFA for administrators.
- Use secure email delivery for invitations; do not return raw invitation tokens to untrusted clients.
- Add CSRF protections if authentication later moves into cookies.
- Add DB connection TLS, backups, restore tests, monitoring, and key rotation.
- Keep JWT access tokens short-lived; sessions are database checked and revocable.
- Review permissions before enabling destructive or provider write endpoints.

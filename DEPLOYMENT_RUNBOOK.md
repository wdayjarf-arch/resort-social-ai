# Production Delivery & Deployment Runbook

This runbook configures the FastAPI + PostgreSQL/pgvector + Redis/Celery + Next.js stack. **Do not enable live campaigns until provider credentials, consent capture, opt-out handling, and a small internal test are verified.** Provider policy changes; verify current requirements in each provider's official docs before launch.

## 0. Prerequisites

- Linux VPS (Ubuntu 22.04/24.04 recommended), 2+ vCPU, 4+ GB RAM for a small pilot; more for larger workloads.
- DNS names for the web UI and API, HTTPS via Caddy/Nginx + Let's Encrypt.
- Docker Engine and Docker Compose plugin; Git; firewall allowing only SSH and HTTPS publicly.
- A dedicated non-root deployment user. Keep PostgreSQL and Redis bound to localhost/private Docker network, never expose them publicly.

## 1. Configure secrets and start services

```bash
git clone <your-private-repository-url> resort-social-ai
cd resort-social-ai
cp .env.example .env
python3 - <<'PY'
import secrets
from cryptography.fernet import Fernet
print('JWT_SECRET_KEY=' + secrets.token_urlsafe(48))
print('TOKEN_ENCRYPTION_KEY=' + Fernet.generate_key().decode())
print('POSTGRES_PASSWORD=' + secrets.token_urlsafe(32))
PY
```

Copy those generated values into `.env` (do not leave example defaults). Set `APP_ENV=production`, `APP_DEBUG=false`, `WEB_BASE_URL=https://app.example.com`, `CORS_ORIGINS=https://app.example.com`, `NEXT_PUBLIC_API_URL=https://api.example.com/api/v1`, and `META_OAUTH_REDIRECT_URI=https://api.example.com/api/v1/integrations/meta/callback`. Set `DATABASE_URL` to use the same PostgreSQL password as `POSTGRES_PASSWORD`; the compose default interpolates `local-change-me`, so explicitly set the production URL. Generate unique, high-entropy `META_WEBHOOK_VERIFY_TOKEN` and JWT/encryption secrets. Never commit `.env` or paste secrets into tickets/logs.

Build and start:

```bash
docker compose config   # inspect resolved config; ensure no defaults/secrets remain
docker compose build --pull
docker compose up -d
docker compose ps
docker compose logs --tail=150 api worker beat
```

The API container runs `alembic upgrade head` before Uvicorn. Verify health at `https://api.example.com/health/ready` (and `/health/live`) and the UI over HTTPS. If no health endpoint is present, verify `docker compose ps`, API logs, and `GET /docs` from a trusted network. Do not expose interactive API docs publicly in production without access controls.

## 2. Meta app (Facebook Messenger / Instagram)

1. Visit [Meta for Developers](https://developers.facebook.com/) and create a Business-type app for the resort.
2. Add Messenger and Instagram Graph API products as needed. Connect the Facebook Page and professional Instagram account; configure the required messaging permissions/scopes and webhook subscriptions for messages. Complete App Review/business verification where Meta requires it.
3. Configure valid HTTPS webhook callback and verify token. Subscribe the Page/account to relevant webhook events. Add `META_APP_ID`, `META_APP_SECRET`, `META_API_VERSION`, `META_WEBHOOK_VERIFY_TOKEN`, and `META_WEBHOOK_APP_SECRET` to `.env`.
4. Complete the app's OAuth flow from the platform and verify that the connected SocialAccount has active credentials and correct scopes. Campaign delivery currently uses `META_CAMPAIGN_PAGE_ACCESS_TOKEN`; provide a page-scoped token through a secret manager/environment only if your app is authorized for that page. Never use a user token in place of the required Page token.
5. **Messaging policy:** this dispatcher suppresses Messenger/Instagram campaign messages after 24 hours from the lead's last recorded contact timestamp. It does not use a message tag to bypass the window. The optional `CONFIRMED_EVENT_UPDATE` tag is restricted to Messenger, must be explicitly configured on a non-marketing campaign segment, and must meet Meta's current tag eligibility rules. Do not label promotions as event updates. Validate Instagram-specific policy separately; do not assume Messenger tags apply to Instagram.
6. Before launch, test inbound webhooks and a reply to an account you control. Verify opt-outs and delivery errors.

Official references: [Meta Messenger Platform](https://developers.facebook.com/docs/messenger-platform/), [Instagram Messaging API](https://developers.facebook.com/docs/messenger-platform/instagram/), [Meta Webhooks](https://developers.facebook.com/docs/graph-api/webhooks/).

## 3. WhatsApp Business Cloud API (template-only campaign sends)

1. In [Meta for Developers](https://developers.facebook.com/), create/configure a business app and add WhatsApp. Connect a WhatsApp Business Account and phone number.
2. Complete business verification/display-name/number setup as required. In WhatsApp Manager, create and get approval for the outbound message templates and languages you intend to use.
3. Create a system-user/permanent access token using the least privileges required, and obtain the **Phone Number ID**. Put these into `WHATSAPP_ACCESS_TOKEN` and `WHATSAPP_PHONE_NUMBER_ID` in `.env`.
4. Campaign `channel` must be `whatsapp`. Set the campaign `template` field to JSON such as:

```json
{"name":"approved_resort_offer","language":"en_US","components":[{"type":"body","parameters":[{"type":"text","text":"{{message}}"}]}]}
```

The named template must already be approved and its body must have a compatible first text parameter. Use approved template categories/consent as required by WhatsApp. Do not send free-form text for outbound marketing.
5. Test with Meta's test number first, then a small opted-in internal audience. Monitor quality rating, template status, errors, opt-outs, and rate limits. Never buy or import scraped recipient lists.

Official reference: [WhatsApp Cloud API — Send Messages](https://developers.facebook.com/docs/whatsapp/cloud-api/guides/send-messages/).

## 4. Telegram Bot broadcast

1. Create a bot using [@BotFather](https://t.me/BotFather), store its token in `TELEGRAM_BOT_TOKEN` as a secret.
2. Users must first start/interact with the bot and explicitly opt in; capture their Telegram chat ID and consent in the CRM. Telegram does not let a bot initiate a conversation with arbitrary users.
3. Add/import these contacts with `source_channel="telegram"`, `external_contact_id` set to the chat ID, and `marketing_consent=true` only after documented consent. Test using your own chat before a small opted-in group.

Official reference: [Telegram Bot API](https://core.telegram.org/bots/api).

## 5. OpenAI and Google People API (optional)

### OpenAI
1. Create a project/API key in the [OpenAI Platform](https://platform.openai.com/api-keys).
2. Set `OPENAI_API_KEY` and (optionally) `CRM_EXTRACTION_MODEL` in `.env`; use project-level limits and billing alerts. Avoid sending unnecessary sensitive data to the model.

### Google Contacts
1. In [Google Cloud Console](https://console.cloud.google.com/), create/select a project and enable **People API**.
2. Configure OAuth consent screen and create OAuth client credentials. Authorize the intended Google account with the contacts scope and obtain a refresh token using a secure OAuth flow. Store `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `GOOGLE_REFRESH_TOKEN` as secrets.
3. Google sync currently creates contacts with resort metadata; it does not yet reconcile duplicate contacts or create a native Google Contacts label group. Test with a non-production Google account first.

## 6. Initial administrator bootstrap

This repository intentionally does **not** expose a public first-user signup route. Do not invent a default password or expose a bootstrap endpoint unauthenticated. Create the first owner using a one-time, audited command/script that calls the project's existing identity models and password hashing helper, then remove the script and rotate credentials. The exact model fields and password API should be checked against `apps/api/app/db/models/identity.py` and `apps/api/app/security/passwords.py` before running.

Suggested safe bootstrap workflow (run inside API container, with a DB backup and a one-time secret supplied via environment; do not put the password in shell history):

```bash
docker compose exec api sh
cd /app
uv run --no-sync python - <<'PY'
# Use this as a template, not a blind migration: verify model constructors first.
import os
from getpass import getpass
from sqlalchemy import select
from app.db.session import SessionLocal
from app.db.models.identity import User, Organization, Membership, MembershipRole
from app.security.passwords import hash_password

email = input('Admin email: ').strip().lower()
password = getpass('Temporary strong password: ')
if len(password) < 14:
    raise SystemExit('Use at least 14 characters')
db = SessionLocal()
try:
    if db.scalar(select(User.id).where(User.email == email)):
        raise SystemExit('User already exists; refusing duplicate bootstrap')
    # Check identity.py constructors before production use; adapt required fields to this release.
    org = Organization(name='Resort', slug='resort')
    user = User(email=email, display_name=email.split('@')[0], password_hash=hash_password(password), is_active=True)
    db.add_all([org, user]); db.flush()
    db.add(Membership(organization_id=org.id, user_id=user.id, role=MembershipRole.owner))
    db.commit()
    print('Owner created. Sign in via the configured trusted authentication flow, then rotate the temporary password.')
except Exception:
    db.rollback(); raise
finally:
    db.close()
PY
```

**Important:** the current foundation documents authentication as not yet production-complete and does not expose a public login route. Verify `User` fields, `hash_password` signature, `Membership` role enum, and your trusted OIDC/session implementation before running. Do not expose the app to the public internet until a complete, tested authentication/session flow and account recovery are in place.

## 7. Campaign dispatch safety checklist

- Capture evidence/source/time of consent; a direct message alone is not consent.
- Human review and approval are required before dispatch.
- The dispatcher re-checks consent immediately before sending and suppresses STOP/UNSUBSCRIBE/Arabic opt-out phrases received in inbound messages.
- Configure `CAMPAIGN_MIN_INTERVAL_SECONDS` conservatively (default 1.5 seconds). This is a global per-org/channel minimum interval using Redis coordination, not a guarantee of compliance with all provider-specific quotas; also honor provider `429`/quality controls.
- WhatsApp uses approved template configuration. Messenger/Instagram campaigns outside the 24-hour window are suppressed. Telegram requires a valid opted-in chat ID.
- `sending` rows after a worker crash require operator review: a timeout can mean the provider accepted the message but the response was lost. Do not blindly reset/retry ambiguous rows or you may duplicate messages.
- Start with a one-recipient internal campaign. Confirm provider dashboard, message ID, CRM status, opt-out behavior, and audit trail before expanding.
- Maintain a suppression list, privacy/retention policy, backups, TLS, OS updates, container image updates, log redaction, monitoring/alerts, and an incident response contact.

## 8. Operations / recovery

```bash
docker compose ps
docker compose logs -f api worker beat
docker compose exec api alembic current
docker compose exec postgres pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > backup-$(date +%F-%H%M).sql
docker compose pull
docker compose build --pull
docker compose up -d
```

Before upgrades: take a tested database backup and inspect migration heads. Keep secrets in a secret manager for production; `.env` is a small-install convenience only. Never run two Celery Beat instances against the same schedule. Use a reverse proxy with TLS and keep ports 5432/6379 private.

## Known operational limits

- The adapter layer is a conservative initial production integration, but it has not been live-tested against your actual Meta/WhatsApp/Telegram accounts. Provider credentials, app review, template approval, account restrictions, and regional availability remain external prerequisites.
- Campaign delivery is at-least-once around network failures; provider timeouts can be ambiguous. The worker records failures and avoids automatic retrying of individual sends, but operators must review `sending` rows after crashes.
- The dispatcher uses the dedicated `last_inbound_at` timestamp for the Meta 24-hour check. Apply migration `0007_campaign_delivery` and verify inbound webhook ingestion updates this field before enabling Meta campaign delivery.

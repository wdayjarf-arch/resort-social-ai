# Resort CRM & Retargeting Extension

## Included
- Tenant-scoped `CustomerLead`, `Campaign`, and `CampaignRecipient` models and Alembic migration `0006_resort_crm`.
- Incoming DM fact extraction with an optional OpenAI JSON extractor; conservative regex fallback; E.164 normalization for international `+` numbers and Yemen local numbers.
- Periodic profile summarizer for transcript summary, guest tone, preferred services, sentiment score (-100..100), and lead status.
- Consent-gated audience segmentation and campaign recipient staging; manager approval is required before a queue task runs.
- Google People API adapter, vCard and CSV exports, CRM dashboard view.

## Migrate and start
```bash
cp .env.example .env
# Set OPENAI_API_KEY for LLM extraction/summaries (regex extraction still works without it).
# For Google sync, set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN.
docker compose up --build -d
```
The migration chain ends at `0006_resort_crm`. If migrations are not auto-run in your environment, run `alembic upgrade head` inside the API container.

## API routes
- `GET /api/v1/organizations/{organization_id}/customers` with `interest`, `lead_status`, `inactive_days`, `min_engagement_score`, `limit` filters
- `POST /api/v1/organizations/{organization_id}/customers` create/upsert a contact; `marketing_consent=true` should only be supplied when consent was actually obtained
- `GET /api/v1/organizations/{organization_id}/customers/export.vcf`
- `GET /api/v1/organizations/{organization_id}/customers/export.csv`
- `POST /api/v1/organizations/{organization_id}/customers/{lead_id}/google-sync`
- `POST /api/v1/organizations/{organization_id}/campaigns` stage a campaign and eligible recipients for review
- `GET /api/v1/organizations/{organization_id}/campaigns`
- `POST /api/v1/organizations/{organization_id}/campaigns/{campaign_id}/approve`

## Important delivery boundary
Campaign creation filters out contacts without explicit recorded marketing consent. Approval only queues consented recipients; `queue_approved_campaign` deliberately does **not** send messages. Implement and test a provider-specific delivery adapter before enabling actual broadcast. Meta Messenger/Instagram messaging APIs are not general-purpose promotional broadcast channels and have messaging-window/template restrictions; WhatsApp requires approved templates for many business-initiated messages. Honor opt-outs, local law, provider rate limits, and per-channel policy. Do not turn this queue into unsolicited mass messaging.

## Google Contacts
Create OAuth credentials and obtain a refresh token with the Google People API contacts scope. The included adapter creates a contact with `Resort Guest` biography/user-defined label and stores the resource name. It currently creates new contacts; it does not reconcile/update an existing Google contact or create a native Google Contacts label group. Restrict access to the refresh token and grant only the minimum required OAuth scope.

## Validation
This extension should be tested against a disposable PostgreSQL database, real Meta test accounts, and a Google test account before production. It has not been live-validated against provider accounts in this workspace. Customer contact data and transcripts are personal data: configure retention, deletion/export procedures, access audit, encryption/backups, and privacy notices.

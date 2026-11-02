"""Celery jobs for webhook normalization, AI engagement, and scheduled publishing."""
import hashlib
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.workers.celery_app import celery_app


@celery_app.task(name="app.foundation.ping")
def foundation_ping() -> dict[str, str]:
    return {"status": "ok"}


def classify_meta_event_types(payload: dict[str, Any]) -> list[str]:
    labels: set[str] = set()
    provider_object = payload.get("object")
    entries = payload.get("entry", [])
    if not isinstance(entries, list):
        entries = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        messaging = entry.get("messaging", [])
        if isinstance(messaging, list):
            for item in messaging:
                if not isinstance(item, dict):
                    continue
                if isinstance(item.get("message"), dict):
                    labels.add("instagram_dm" if provider_object == "instagram" else "messenger_dm")
                elif item.get("read") or item.get("delivery"):
                    labels.add("message_status")
        changes = entry.get("changes", [])
        if isinstance(changes, list):
            for change in changes:
                if isinstance(change, dict):
                    field = str(change.get("field", "")).lower()
                    if field == "comments":
                        labels.add("instagram_comment" if provider_object == "instagram" else "facebook_comment")
                    elif field == "feed" and (change.get("value", {}).get("item") == "comment" if isinstance(change.get("value"), dict) else False):
                        labels.add("facebook_comment")
                    elif field:
                        labels.add(f"{provider_object or 'meta'}_{field}")
    return sorted(labels or {f"{provider_object or 'meta'}_unclassified"})


@celery_app.task(name="app.meta.process_webhook_event", bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 5})
def process_meta_webhook_event(self, event_id: str) -> dict[str, Any]:
    from app.db.models.identity import MetaWebhookEvent
    from app.db.session import SessionLocal
    from app.services.meta_normalizer import normalize_meta_event

    db = SessionLocal()
    try:
        event = db.scalar(select(MetaWebhookEvent).where(MetaWebhookEvent.id == UUID(event_id)).with_for_update())
        if event is None:
            return {"status": "missing", "event_id": event_id}
        if event.status == "processed":
            return {"status": "duplicate", "event_id": event_id, "event_types": event.event_types}
        event.status = "processing"
        db.flush()
        message_ids, engagement_ids = normalize_meta_event(db, event)
        event.event_types = classify_meta_event_types(event.payload)
        event.status = "processed"
        event.processed_at = datetime.now(timezone.utc)
        event.last_error = None
        db.commit()
        # Dispatch only after normalized rows are committed.
        for message_id in message_ids:
            process_inbound_message.apply_async(args=[str(message_id)])
        for engagement_id in engagement_ids:
            process_inbound_engagement.apply_async(args=[str(engagement_id)])
        return {"status": "processed", "event_id": event_id, "messages": len(message_ids), "engagements": len(engagement_ids)}
    except Exception as exc:
        db.rollback()
        try:
            event = db.get(MetaWebhookEvent, UUID(event_id))
            if event is not None:
                event.status = "received"
                event.last_error = type(exc).__name__[:500]
                db.commit()
        except Exception:
            db.rollback()
        raise
    finally:
        db.close()


@celery_app.task(name="app.meta.requeue_pending_webhooks")
def requeue_pending_meta_webhooks() -> dict[str, int]:
    from app.db.models.identity import MetaWebhookEvent
    from app.db.session import SessionLocal
    db = SessionLocal()
    queued = 0
    try:
        pending = db.scalars(select(MetaWebhookEvent).where(MetaWebhookEvent.status == "received").order_by(MetaWebhookEvent.received_at).limit(500)).all()
        for event in pending:
            process_meta_webhook_event.apply_async(args=[str(event.id)])
            queued += 1
        return {"queued": queued}
    finally:
        db.close()


@celery_app.task(name="app.ai.process_inbound_message", bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def process_inbound_message(self, message_id: str) -> dict[str, Any]:
    from app.ai.agent import decide_reply
    from app.db.models.engagement import Conversation, Message
    from app.db.models.identity import SocialAccount
    from app.db.session import SessionLocal
    from app.integrations.meta.delivery import deliver_meta_direct_message, is_retryable_message_failure

    db = SessionLocal()
    try:
        inbound = db.scalar(select(Message).where(Message.id == UUID(message_id)).with_for_update())
        if inbound is None or inbound.direction != "inbound":
            return {"status": "ignored"}
        conversation_for_crm = db.get(Conversation, inbound.conversation_id)
        if inbound.text and conversation_for_crm:
            from app.services.intent_classifier import classify_intent
            intent_result = classify_intent(inbound.text)
            conversation_for_crm.intent_tag = intent_result.tag
            conversation_for_crm.intent_confidence = intent_result.confidence
            inbound.intent = intent_result.tag
            inbound.ai_confidence = intent_result.confidence
            db.commit()

        existing_draft = db.scalar(
            select(Message).where(Message.reply_to_message_id == inbound.id).with_for_update()
        )
        retry_existing = existing_draft is not None and is_retryable_message_failure(existing_draft)
        if existing_draft is not None and not retry_existing:
            if inbound.status != "processed":
                inbound.status = "processed"
                db.commit()
            return {
                "status": "duplicate",
                "draft_id": str(existing_draft.id),
                "draft_status": existing_draft.status,
                "intent_tag": conversation_for_crm.intent_tag if conversation_for_crm else None,
            }

        if existing_draft is None and inbound.text and conversation_for_crm:
            from app.services.crm import llm_extract_facts, upsert_lead
            facts = llm_extract_facts(inbound.text)
            facts["_source_message_id"] = str(inbound.id)
            lead = upsert_lead(
                db, inbound.organization_id, conversation_for_crm.channel,
                conversation_for_crm.participant_id, facts, inbound.created_at,
            )
            import re
            optout = bool(re.search(r"(?i)(?:^|\b)(?:stop|unsubscribe|cancel|opt\s*out)(?:\b|$)", inbound.text)) or any(
                x in inbound.text for x in (
                    "ط¥ظ„ط؛ط§ط، ط§ظ„ط§ط´طھط±ط§ظƒ", "ط§ظ„ط؛ط§ط، ط§ظ„ط§ط´طھط±ط§ظƒ", "ط£ظˆظ‚ظپ ط§ظ„ط±ط³ط§ط¦ظ„", "ط§ظˆظ‚ظپ ط§ظ„ط±ط³ط§ط¦ظ„", "ط¥ظٹظ‚ط§ظپ", "ط§ظٹظ‚ط§ظپ"
                )
            )
            if optout:
                lead.marketing_consent = False
                lead.consent_source = "guest_opt_out"
                lead.consent_at = datetime.now(timezone.utc)
                inbound.status = "processed"
                db.commit()
                return {"status": "opt_out_recorded", "lead_id": str(lead.id)}
            db.commit()

        conversation = db.get(Conversation, inbound.conversation_id)
        account = db.get(SocialAccount, inbound.social_account_id)
        if not conversation or not account or not inbound.text:
            inbound.status = "processed"
            db.commit()
            return {"status": "no_text_or_conversation"}

        if retry_existing:
            draft = existing_draft
            can_auto_send = True
        else:
            decision = decide_reply(
                db, organization_id=inbound.organization_id, query=inbound.text,
                property_id=account.property_id,
            )
            can_auto_send = decision.can_auto_send and conversation.channel in {"messenger", "instagram"}
            draft = Message(
                organization_id=inbound.organization_id, conversation_id=inbound.conversation_id,
                social_account_id=inbound.social_account_id, direction="outbound",
                recipient_external_id=conversation.participant_id, text=decision.answer,
                status="sending" if can_auto_send else "needs_human_approval",
                reply_to_message_id=inbound.id, intent=decision.intent,
                ai_confidence=decision.confidence, ai_generated=True,
            )
            db.add(draft)

        inbound.status = "processed"
        if not can_auto_send:
            db.commit()
            db.refresh(draft)
            return {
                "status": "needs_human_approval", "draft_id": str(draft.id),
                "intent": draft.intent,
            }

        draft.status = "sending"
        db.commit()
        db.refresh(draft)
        return deliver_meta_direct_message(db, draft, conversation)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

@celery_app.task(name="app.ai.process_inbound_engagement", bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def process_inbound_engagement(self, engagement_id: str) -> dict[str, Any]:
    from app.ai.agent import decide_reply
    from app.db.models.engagement import PostEngagement
    from app.db.models.identity import SocialAccount
    from app.db.session import SessionLocal
    from app.integrations.meta.delivery import deliver_meta_comment_reply, is_retryable_comment_failure

    db = SessionLocal()
    try:
        engagement = db.scalar(
            select(PostEngagement).where(PostEngagement.id == UUID(engagement_id)).with_for_update()
        )
        if engagement is None or not engagement.text:
            return {"status": "ignored"}

        if is_retryable_comment_failure(engagement):
            engagement.status = "sending"
            db.commit()
            return deliver_meta_comment_reply(db, engagement)

        if engagement.drafted_reply or engagement.status != "received":
            return {"status": "duplicate", "engagement_id": engagement_id, "draft_status": engagement.status}

        linked_account = db.get(SocialAccount, engagement.social_account_id)
        if linked_account is None:
            engagement.status = "needs_human_approval"
            engagement.last_error = "social_account_unavailable"
            db.commit()
            return {"status": "needs_human_approval", "engagement_id": engagement_id}

        decision = decide_reply(
            db, organization_id=engagement.organization_id, query=engagement.text,
            property_id=linked_account.property_id,
        )
        engagement.drafted_reply = decision.answer
        engagement.intent = decision.intent
        engagement.ai_confidence = decision.confidence
        engagement.ai_generated = True
        if not decision.can_auto_send or engagement.channel not in {"messenger", "instagram"}:
            engagement.status = "needs_human_approval"
            db.commit()
            return {"status": "needs_human_approval", "engagement_id": engagement_id, "intent": decision.intent}

        engagement.status = "sending"
        db.commit()
        return deliver_meta_comment_reply(db, engagement)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

@celery_app.task(name="app.inbox.send_approved_reply", bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def send_approved_reply(self, message_id: str, approved_by_user_id: str | None = None) -> dict[str, Any]:
    from app.db.models.engagement import Conversation, Message
    from app.db.session import SessionLocal
    from app.integrations.social_delivery import deliver_social_direct_message, is_retryable_social_message_failure

    db = SessionLocal()
    try:
        draft = db.scalar(select(Message).where(Message.id == UUID(message_id)).with_for_update())
        if draft is None or draft.direction != "outbound":
            return {"status": "not_approved_or_missing"}
        retrying = is_retryable_social_message_failure(draft)
        if draft.status != "approved" and not retrying:
            return {"status": "not_approved_or_missing", "draft_status": draft.status}

        conversation = db.get(Conversation, draft.conversation_id)
        if conversation is None or not draft.text:
            return {"status": "missing_conversation_or_text"}
        if conversation.channel not in {"messenger", "instagram", "whatsapp", "telegram"}:
            draft.status = "failed"
            draft.provider_error_code = "social:preflight:unsupported_channel"
            draft.provider_error = "No outbound adapter is configured for this channel"
            db.commit()
            return {"status": "unsupported_channel", "message_id": message_id}

        draft.status = "sending"
        if approved_by_user_id and approved_by_user_id != "recovery":
            draft.human_approved_by_user_id = UUID(approved_by_user_id)
            draft.human_approved_at = draft.human_approved_at or datetime.now(timezone.utc)
        db.commit()
        return deliver_social_direct_message(db, draft, conversation)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

@celery_app.task(name="app.inbox.send_approved_comment_reply", bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def send_approved_comment_reply(self, engagement_id: str, approved_by_user_id: str) -> dict[str, Any]:
    from app.db.models.engagement import PostEngagement
    from app.db.session import SessionLocal
    from app.integrations.meta.delivery import deliver_meta_comment_reply, is_retryable_comment_failure

    db = SessionLocal()
    try:
        row = db.scalar(
            select(PostEngagement).where(PostEngagement.id == UUID(engagement_id)).with_for_update()
        )
        if row is None or not row.drafted_reply:
            return {"status": "not_approved_or_missing"}
        retrying = is_retryable_comment_failure(row)
        if row.status != "approved" and not retrying:
            return {"status": "not_approved_or_missing", "engagement_status": row.status}
        if row.channel not in {"messenger", "instagram"}:
            row.status = "failed"
            row.last_error = "meta_delivery|retryable=0|state=not_sent|code=meta:preflight:unsupported_channel"
            db.commit()
            return {"status": "unsupported_channel", "engagement_id": engagement_id}

        row.status = "sending"
        db.commit()
        return deliver_meta_comment_reply(db, row)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

@celery_app.task(name="app.inbox.requeue_approved_replies")
def requeue_approved_replies() -> dict[str, int]:
    from app.db.models.engagement import Message, PostEngagement
    from app.db.session import SessionLocal
    db = SessionLocal()
    messages = comments = 0
    try:
        pending_messages = db.scalars(select(Message.id).where(Message.direction == "outbound", Message.status == "approved").limit(200)).all()
        pending_comments = db.scalars(select(PostEngagement.id).where(PostEngagement.status == "approved").limit(200)).all()
        for item_id in pending_messages:
            send_approved_reply.apply_async(args=[str(item_id)])
            messages += 1
        for item_id in pending_comments:
            send_approved_comment_reply.apply_async(args=[str(item_id), "recovery"])
            comments += 1
        return {"messages": messages, "comments": comments}
    finally:
        db.close()


@celery_app.task(name="app.scheduler.recover_stuck_publications")
def recover_stuck_publications() -> dict[str, int]:
    from datetime import timedelta
    from app.db.models.engagement import ScheduledPost
    from app.db.session import SessionLocal
    db = SessionLocal()
    recovered = failed = 0
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
    try:
        rows = db.scalars(select(ScheduledPost).where(ScheduledPost.status == "publishing", ScheduledPost.updated_at < cutoff).with_for_update(skip_locked=True).limit(100)).all()
        for row in rows:
            if row.attempts < 3:
                row.status = "scheduled"
                row.last_error = "recovered_stuck_publication"
                recovered += 1
            else:
                row.status = "failed"
                row.last_error = "publication_worker_lost_after_retries"
                failed += 1
        db.commit()
        return {"recovered": recovered, "failed": failed}
    finally:
        db.close()


@celery_app.task(name="app.scheduler.publish_due_posts", bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 2})
def publish_due_scheduled_posts(self) -> dict[str, int]:
    from app.db.models.engagement import ScheduledPost, ScheduledPostTarget
    from app.db.models.identity import AuditLog, SocialAccount
    from app.db.session import SessionLocal
    from app.integrations.meta.operations import MetaPublishingService

    db = SessionLocal()
    published = failed = 0
    now = datetime.now(timezone.utc)
    try:
        posts = db.scalars(select(ScheduledPost).where(
            ScheduledPost.status == "scheduled", ScheduledPost.scheduled_at <= now,
        ).order_by(ScheduledPost.scheduled_at).limit(50).with_for_update(skip_locked=True)).all()
        for post in posts:
            post.status = "publishing"
            post.attempts += 1
        db.commit()
        for post in posts:
            post = db.get(ScheduledPost, post.id)
            results: dict[str, Any] = dict(post.published_results or {})
            successes = errors = 0
            targets = db.scalars(select(ScheduledPostTarget).where(
                ScheduledPostTarget.organization_id == post.organization_id,
                ScheduledPostTarget.scheduled_post_id == post.id,
                ScheduledPostTarget.status != "published",
            )).all()
            for target in targets:
                try:
                    account = db.scalar(select(SocialAccount).where(
                        SocialAccount.organization_id == post.organization_id,
                        SocialAccount.id == target.social_account_id,
                        SocialAccount.is_active.is_(True),
                    ))
                    if account is None:
                        raise RuntimeError("target_account_unavailable")
                    image_url = post.media_urls[0] if post.media_urls else None
                    result = MetaPublishingService(db).publish_post(
                        organization_id=post.organization_id, account_id=account.id,
                        text=post.content, image_url=image_url,
                    )
                    target.status = "published"
                    target.published_id = result.get("result_id")
                    target.last_error = None
                    results[str(account.id)] = {"status": "published", "result_id": target.published_id}
                    successes += 1
                except Exception as exc:
                    target.status = "failed"
                    target.last_error = type(exc).__name__[:500]
                    results[str(target.social_account_id)] = {"status": "failed", "error": type(exc).__name__}
                    errors += 1
                    db.add(AuditLog(organization_id=post.organization_id, action="scheduled_post.publish_failed",
                                    resource_type="scheduled_post", resource_id=str(post.id), outcome="failure",
                                    details={"target_account_id": str(target.social_account_id), "error_type": type(exc).__name__}))
            post.published_results = results
            if errors and post.attempts < 3:
                # Beat will retry this due row; already-published targets are skipped next time.
                post.status = "scheduled"
            else:
                any_published = any(v.get("status") == "published" for v in results.values())
                post.status = "partially_published" if errors and any_published else "failed" if errors else "published" if any_published else "failed"
            post.last_error = "one_or_more_targets_failed" if errors else None
            db.commit()
            published += int(successes > 0)
            failed += int(successes == 0)
        return {"published_posts": published, "failed_posts": failed}
    finally:
        db.close()

@celery_app.task(name="app.analytics.sync_meta_insights", bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def sync_meta_insights(self) -> dict[str, Any]:
    """Periodically ingest recent daily Meta Insights for every active connected account."""
    from app.db.session import SessionLocal
    from app.db.models.identity import Provider, SocialAccount
    from app.services.analytics import ingest_account_insights
    db = SessionLocal()
    processed = 0
    errors = 0
    try:
        accounts = db.scalars(select(SocialAccount).where(SocialAccount.provider == Provider.meta, SocialAccount.is_active.is_(True))).all()
        for account in accounts:
            try:
                ingest_account_insights(db, account.organization_id, account, days=30)
                processed += 1
            except Exception:
                errors += 1
        return {"accounts_processed": processed, "accounts_with_errors": errors}
    finally:
        db.close()


@celery_app.task(name="app.crm.queue_approved_campaign")
def queue_approved_campaign(campaign_id: str) -> dict[str, Any]:
    """Queue only approved and currently consented recipients, then dispatch asynchronously."""
    from app.db.models.crm import Campaign, CampaignRecipient, CustomerLead
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        campaign = db.get(Campaign, UUID(campaign_id))
        if not campaign or campaign.status not in {"approved", "sending"}: return {"status": "ignored"}
        recipients = db.scalars(select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign.id, CampaignRecipient.status == "pending_review").limit(5000)).all()
        queued = 0
        for recipient in recipients:
            lead = db.get(CustomerLead, recipient.lead_id)
            if not lead or not lead.marketing_consent:
                recipient.status = "suppressed"; recipient.error_code = "missing_marketing_consent"; continue
            recipient.status = "queued"; queued += 1
        campaign.status = "sending" if queued else "completed"
        db.commit()
        dispatch_campaign.delay(campaign_id)
        return {"status": "queued", "campaign_id": campaign_id, "recipients": queued}
    finally: db.close()


@celery_app.task(name="app.crm.dispatch_campaign", bind=True, max_retries=3, default_retry_delay=30)
def dispatch_campaign(self, campaign_id: str) -> dict[str, Any]:
    """Deliver queued recipients with cross-worker throttle and consent re-check immediately before send."""
    import os
    from app.db.models.crm import Campaign, CampaignRecipient, CustomerLead
    from app.db.session import SessionLocal
    from app.integrations.campaign_delivery import adapter_for, throttle, DeliveryError
    db = SessionLocal(); sent = failed = suppressed = 0
    try:
        campaign = db.get(Campaign, UUID(campaign_id))
        if not campaign or campaign.status not in {"approved", "sending"}: return {"status": "ignored"}
        batch = db.scalars(select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign.id, CampaignRecipient.status == "queued").order_by(CampaignRecipient.created_at).limit(1000)).all()
        min_interval = max(0.2, min(60.0, float(os.getenv("CAMPAIGN_MIN_INTERVAL_SECONDS", "1.5"))))
        for row in batch:
            # Lock and re-read current consent before every provider call.
            row = db.scalar(select(CampaignRecipient).where(CampaignRecipient.id == row.id).with_for_update())
            lead = db.get(CustomerLead, row.lead_id) if row else None
            if not row or row.status != "queued": continue
            if not lead or not lead.marketing_consent:
                row.status = "suppressed"; row.error_code = "opted_out_or_consent_missing"; suppressed += 1; db.commit(); continue
            if campaign.channel == "whatsapp" and not lead.phone_e164:
                row.status = "failed"; row.error_code = "missing_phone"; failed += 1; db.commit(); continue
            if campaign.channel in {"messenger", "instagram"} and lead.source_channel != campaign.channel:
                row.status = "failed"; row.error_code = "channel_recipient_mismatch"; failed += 1; db.commit(); continue
            row.status = "sending"; db.commit()
            try:
                throttle(f"campaign-throttle:{campaign.organization_id}:{campaign.channel}", min_interval)
                recipient = {"phone_e164":lead.phone_e164, "external_contact_id":lead.external_contact_id, "last_inbound_at":lead.last_inbound_at}
                result_id = adapter_for(campaign.channel).send(recipient, row.personalized_message, {"channel":campaign.channel,"template":campaign.template,"segment":campaign.segment or {}})
                row.provider_message_id = result_id; row.status = "sent"; row.sent_at = datetime.now(timezone.utc); row.error_code = None; sent += 1
                db.commit()
            except DeliveryError as exc:
                row.status = "suppressed" if exc.code in {"outside_messaging_window", "messaging_tag_not_allowed_for_marketing"} else "failed"
                row.error_code = exc.code[:100]; failed += 1; db.commit()
            except Exception as exc:
                db.rollback(); row = db.get(CampaignRecipient, row.id)
                if row: row.status = "failed"; row.error_code = type(exc).__name__[:100]; db.commit()
                failed += 1
        remaining = db.scalar(select(CampaignRecipient.id).where(CampaignRecipient.campaign_id == campaign.id, CampaignRecipient.status.in_(["queued", "sending"])).limit(1))
        if not remaining:
            campaign.status = "completed"; db.commit()
        return {"status":"processed", "campaign_id":campaign_id, "sent":sent, "failed":failed, "suppressed":suppressed}
    finally: db.close()


@celery_app.task(name="app.crm.summarize_customer_profiles")
def summarize_customer_profiles() -> dict[str, int]:
    """Periodically summarize guest profiles using the configured AI provider."""
    from app.ai.providers import AIProviderError, generate_json
    from app.core.config import get_settings
    from app.db.models.crm import CustomerLead
    from app.db.models.engagement import Conversation, Message
    from app.db.session import SessionLocal

    settings = get_settings()
    provider_key = {
        "openai": settings.openai_api_key,
        "anthropic": settings.anthropic_api_key,
        "gemini": settings.gemini_api_key,
    }.get(settings.ai_provider)
    ai_available = bool(provider_key and provider_key.get_secret_value().strip())

    db = SessionLocal()
    updated = 0
    try:
        leads = db.scalars(
            select(CustomerLead)
            .where(CustomerLead.last_contacted_at.is_not(None))
            .order_by(CustomerLead.last_contacted_at.desc())
            .limit(200)
        ).all()
        for lead in leads:
            if not lead.external_contact_id:
                continue
            convs = db.scalars(
                select(Conversation)
                .where(
                    Conversation.organization_id == lead.organization_id,
                    Conversation.participant_id == lead.external_contact_id,
                )
                .order_by(Conversation.last_message_at.desc())
                .limit(5)
            ).all()
            conversation_ids = [conversation.id for conversation in convs]
            if not conversation_ids:
                continue
            messages = db.scalars(
                select(Message)
                .where(
                    Message.organization_id == lead.organization_id,
                    Message.conversation_id.in_(conversation_ids),
                )
                .order_by(Message.created_at.desc())
                .limit(30)
            ).all()
            transcript = "\n".join(
                f"{message.direction}: {message.text}"
                for message in reversed(messages)
                if message.text
            )[:9000]
            if not transcript:
                continue

            if ai_available:
                try:
                    data = generate_json(
                        "Summarize this guest conversation. Return JSON keys summary, tone, "
                        "preferred_services (array), sentiment_score (integer -100..100), "
                        "lead_status (hot/warm/cold/past_guest). Do not invent facts; "
                        "complaints should be flagged in summary.",
                        transcript,
                    )
                except AIProviderError:
                    continue
                lead.transcript_summary = str(data.get("summary", ""))[:4000] or lead.transcript_summary
                lead.guest_tone = str(data.get("tone", ""))[:120] or lead.guest_tone
                lead.preferred_services = sorted(
                    set(lead.preferred_services or [])
                    | (set(data.get("preferred_services", [])) & {"villas", "pools", "corporate", "events", "dining", "spa", "other"})
                )
                score = data.get("sentiment_score")
                if isinstance(score, int):
                    lead.sentiment_score = max(-100, min(100, score))
                status = data.get("lead_status")
                if status in {"hot", "warm", "cold", "past_guest"}:
                    lead.lead_status = status
            else:
                # Preserve a local-only summary when no AI key is configured.
                lead.transcript_summary = transcript[-1500:]
            updated += 1
        db.commit()
        return {"updated": updated}
    finally:
        db.close()

@celery_app.task(name="app.marketing.publish_scheduled_posts")
def publish_scheduled_marketing_posts() -> dict[str, int]:
    """Publish due tourism posts through connected Meta accounts. Mock mode is opt-in and dev-only."""
    from app.core.config import get_settings
    from app.db.models.marketing import MarketingPost
    from app.db.models.identity import SocialAccount, AuditLog
    from app.db.models.identity import Provider
    from app.db.session import SessionLocal
    from app.integrations.meta.operations import MetaPublishingService
    settings = get_settings()
    db = SessionLocal()
    done = failed = 0
    now = datetime.now(timezone.utc)
    try:
        due = db.scalars(select(MarketingPost).where(
            MarketingPost.status == "scheduled", MarketingPost.scheduled_at <= now
        ).order_by(MarketingPost.scheduled_at).limit(25).with_for_update(skip_locked=True)).all()
        for row in due:
            row.status = "publishing"
        db.commit()
        for candidate in due:
            row = db.scalar(select(MarketingPost).where(MarketingPost.id == candidate.id).with_for_update())
            if row is None or row.status != "publishing":
                continue
            try:
                targets = set(row.target_platforms or [])
                if not targets or not targets.issubset({"facebook", "instagram"}):
                    raise RuntimeError("unsupported_or_empty_target_platforms")
                accounts = db.scalars(select(SocialAccount).where(
                    SocialAccount.organization_id == row.organization_id,
                    SocialAccount.is_active.is_(True), SocialAccount.provider == Provider.meta,
                    SocialAccount.account_type.in_(["facebook_page", "instagram_business"]),
                )).all()
                accounts = [a for a in accounts if ("facebook" if a.account_type == "facebook_page" else "instagram") in targets]
                available_platforms = {"facebook" if a.account_type == "facebook_page" else "instagram" for a in accounts}
                missing_platforms = targets - available_platforms
                if missing_platforms and not (not accounts and settings.marketing_mock_publish and settings.app_env != "production"):
                    raise RuntimeError("no_active_connected_accounts_for_platforms:" + ",".join(sorted(missing_platforms)))
                if not accounts:
                    if settings.marketing_mock_publish and settings.app_env != "production":
                        row.status = "published"
                        row.published_at = now
                        row.performance_metrics = {"simulated": True, "note": "Development mock publish; no provider API was called", "likes": 0, "shares": 0, "comments": 0, "reach": 0, "targets": sorted(targets)}
                        row.error_message = None
                        db.add(AuditLog(organization_id=row.organization_id, action="marketing.mock_published", resource_type="marketing_post", resource_id=str(row.id), outcome="success", details={"targets": sorted(targets)}))
                        db.commit(); done += 1; continue
                    raise RuntimeError("no_active_connected_accounts_for_requested_platforms")
                results = []
                service = MetaPublishingService(db)
                for account in accounts:
                    platform = "facebook" if account.account_type == "facebook_page" else "instagram"
                    if platform == "instagram" and not row.media_urls:
                        raise RuntimeError("instagram_requires_public_media_url")
                    result = service.publish_post(organization_id=row.organization_id, account_id=account.id,
                        text=row.content_text, image_url=(row.media_urls[0] if row.media_urls else None))
                    results.append({"platform": platform, "account_id": str(account.id), "result_id": result.get("result_id")})
                actual = {x["platform"] for x in results}
                missing = targets - actual
                if missing:
                    raise RuntimeError("no_connected_account_for_platforms:" + ",".join(sorted(missing)))
                row.status = "published"; row.published_at = now; row.error_message = None
                row.performance_metrics = {"likes": 0, "shares": 0, "comments": 0, "reach": 0, "provider_results": results, "metrics_status": "awaiting_provider_insights"}
                db.add(AuditLog(organization_id=row.organization_id, action="marketing.post_published", resource_type="marketing_post", resource_id=str(row.id), outcome="success", details={"targets": sorted(targets), "result_count": len(results)}))
                db.commit(); done += 1
            except Exception as exc:
                db.rollback()
                row = db.scalar(select(MarketingPost).where(MarketingPost.id == candidate.id).with_for_update())
                if row:
                    row.status = "failed"
                    # Avoid leaking provider response bodies or secrets into UI/logs.
                    row.error_message = str(exc)[:500] if type(exc) is RuntimeError else f"provider_publish_failed:{type(exc).__name__}"
                    db.add(AuditLog(organization_id=row.organization_id, action="marketing.post_publish_failed", resource_type="marketing_post", resource_id=str(row.id), outcome="failure", details={"error_type": type(exc).__name__}))
                    db.commit()
                failed += 1
        return {"published": done, "failed": failed}
    finally:
        db.close()

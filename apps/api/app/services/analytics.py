"""Meta Insights ingestion and publishing-time scoring.

Meta Insights endpoints generally return period aggregates, not the exact hour each
like happened. Peak-hour learning therefore uses timestamped comments/messages as
hourly activity signals and snapshots as reach/impression context; never fabricates
hourly likes from daily aggregates.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.crypto import decrypt_token
from app.db.models.analytics import AnalyticsSnapshot
from app.db.models.engagement import Conversation, Message, PostEngagement
from app.db.models.identity import OAuthCredential, Provider, SocialAccount
from app.integrations.meta.client import MetaGraphClient

METRICS = ("page_impressions", "page_post_engagements", "page_impressions_unique", "page_views_total")

def ingest_account_insights(db: Session, organization_id: UUID, account: SocialAccount, days: int = 30) -> int:
    cred = db.scalar(select(OAuthCredential).where(OAuthCredential.organization_id == organization_id,
        OAuthCredential.social_account_id == account.id, OAuthCredential.credential_kind == "access_token",
        OAuthCredential.revoked_at.is_(None)))
    if not cred: return 0
    token = decrypt_token(cred.encrypted_value)
    client = MetaGraphClient()
    now = datetime.now(timezone.utc); since = int((now - timedelta(days=days)).timestamp()); until = int(now.timestamp())
    source_rows: dict[str, dict] = {}
    if account.account_type == "facebook_page":
        try:
            result = client.get(f"{account.provider_account_id}/insights", token=token,
                params={"metric": ",".join(METRICS[:3]), "period": "day", "since": since, "until": until})
            for metric in result.get("data", []):
                for value in metric.get("values", []):
                    key = str(value.get("end_time", ""))[:10]
                    source_rows.setdefault(key, {})[metric.get("name", "")] = value.get("value", 0)
        except Exception:
            # Permissions/metric availability differ by Page and Graph API version.
            # Preserve successful account syncs and record supported metrics only.
            pass
    elif account.account_type == "instagram_business":
        for metric in ("reach", "views", "total_interactions", "likes", "comments"):
            try:
                result = client.get(f"{account.provider_account_id}/insights", token=token,
                    params={"metric": metric, "period": "day", "since": since, "until": until})
                for item in result.get("data", []):
                    for value in item.get("values", []):
                        key = str(value.get("end_time", ""))[:10]
                        source_rows.setdefault(key, {})[metric] = value.get("value", 0)
            except Exception:
                continue
    inserted = 0
    for day, raw in source_rows.items():
        try: period = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
        except ValueError: continue
        existing = db.scalar(select(AnalyticsSnapshot).where(AnalyticsSnapshot.social_account_id == account.id,
            AnalyticsSnapshot.period_start == period, AnalyticsSnapshot.metric_source == "meta_insights_daily"))
        vals = {k: int(v) if isinstance(v, (int, float)) else 0 for k,v in raw.items()}
        reach = vals.get("page_impressions_unique", vals.get("reach", 0))
        impressions = vals.get("page_impressions", vals.get("views", 0))
        likes = vals.get("likes", 0); comments = vals.get("comments", 0)
        engagements = vals.get("page_post_engagements", vals.get("total_interactions", likes + comments))
        if existing:
            existing.reach, existing.impressions, existing.likes, existing.comments = reach, impressions, likes, comments
            existing.engagements, existing.raw_metrics = engagements, raw
        else:
            db.add(AnalyticsSnapshot(organization_id=organization_id, social_account_id=account.id,
                platform="instagram" if account.account_type == "instagram_business" else "facebook",
                metric_source="meta_insights_daily", period_start=period, period_end=period + timedelta(days=1),
                reach=reach, impressions=impressions, likes=likes, comments=comments, engagements=engagements, raw_metrics=raw))
            inserted += 1
    db.commit()
    return inserted

def activity_matrix(db: Session, organization_id: UUID, account_ids: list[UUID], timezone_name: str, days: int = 90):
    tz = ZoneInfo(timezone_name)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    matrix = [[0.0 for _ in range(24)] for _ in range(7)]
    # Weight recent timestamped engagement; messages and comments are observable events.
    engagements = db.scalars(select(PostEngagement).where(PostEngagement.organization_id == organization_id,
        PostEngagement.social_account_id.in_(account_ids), PostEngagement.created_at >= since)).all() if account_ids else []
    messages = db.scalars(select(Message).join(Conversation, Message.conversation_id == Conversation.id).where(
        Conversation.organization_id == organization_id, Conversation.social_account_id.in_(account_ids),
        Message.created_at >= since, Message.direction == "inbound")).all() if account_ids else []
    counts = [[0 for _ in range(24)] for _ in range(7)]
    for event in list(engagements) + list(messages):
        dt = event.created_at
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        local = dt.astimezone(tz); counts[local.weekday()][local.hour] += 1
    maximum = max((n for row in counts for n in row), default=0)
    if maximum:
        for d in range(7):
            for h in range(24): matrix[d][h] = round(counts[d][h] / maximum * 100, 2)
    return {"timezone": timezone_name, "days_analysed": days, "sample_count": sum(map(sum, counts)),
            "scores": matrix, "counts": counts, "days": ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"],
            "hours": list(range(24)), "basis": "timestamped inbound messages and post comments; Meta Insights snapshots are period aggregates"}

def best_slots(matrix: dict, limit: int = 10):
    counts = matrix["counts"]
    ranked = sorted(((counts[d][h], d, h) for d in range(7) for h in range(24)), reverse=True)
    max_count = max((x[0] for x in ranked), default=0)
    return [{"day_of_week": d, "day": matrix["days"][d], "hour": h,
             "score": round((n / max_count) * 100, 2) if max_count else 0, "observations": n}
            for n,d,h in ranked[:limit]]

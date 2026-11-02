from sqlalchemy import select

from app.db.models.engagement import Conversation, Message, PostEngagement
from app.db.models.identity import MetaWebhookEvent, Organization, Provider, SocialAccount
from app.services.meta_normalizer import normalize_meta_event


def _account(db_session):
    org = Organization(name="Resort One", slug="resort-one")
    db_session.add(org)
    db_session.flush()
    account = SocialAccount(
        organization_id=org.id, provider=Provider.meta, provider_account_id="page-123",
        display_name="Resort Facebook Page", account_type="facebook_page", granted_scopes=[],
        metadata_json={}, is_active=True,
    )
    db_session.add(account)
    db_session.commit()
    return org, account


def test_normalize_inbound_messenger_message_is_idempotent(db_session):
    org, account = _account(db_session)
    payload = {"object": "page", "entry": [{"id": "page-123", "time": 1720000000000, "messaging": [{
        "sender": {"id": "guest-77"}, "recipient": {"id": "page-123"}, "timestamp": 1720000000000,
        "message": {"mid": "mid.1", "text": "What time do the thermal pools open?"},
    }]}]}
    event = MetaWebhookEvent(fingerprint="a" * 64, organization_id=org.id, provider_object="page", event_types=[], payload=payload, status="received")
    db_session.add(event)
    db_session.flush()
    messages, engagements = normalize_meta_event(db_session, event)
    db_session.commit()
    assert len(messages) == 1 and not engagements
    messages_again, _ = normalize_meta_event(db_session, event)
    db_session.commit()
    assert messages_again == messages
    assert db_session.scalar(select(Conversation).where(Conversation.organization_id == org.id)) is not None
    rows = db_session.scalars(select(Message).where(Message.organization_id == org.id)).all()
    assert len(rows) == 1
    assert rows[0].direction == "inbound"
    assert rows[0].status == "received"


def test_normalize_facebook_comment(db_session):
    org, _account_row = _account(db_session)
    payload = {"object": "page", "entry": [{"id": "page-123", "changes": [{"field": "feed", "value": {
        "item": "comment", "verb": "add", "comment_id": "comment-1", "post_id": "post-9",
        "from": {"id": "guest-2", "name": "Guest"}, "message": "Beautiful property!",
    }}]}]}
    event = MetaWebhookEvent(fingerprint="b" * 64, organization_id=org.id, provider_object="page", event_types=[], payload=payload, status="received")
    db_session.add(event)
    db_session.flush()
    messages, engagements = normalize_meta_event(db_session, event)
    db_session.commit()
    assert not messages and len(engagements) == 1
    row = db_session.scalar(select(PostEngagement).where(PostEngagement.organization_id == org.id))
    assert row is not None
    assert row.parent_post_id == "post-9"
    assert row.author_name == "Guest"

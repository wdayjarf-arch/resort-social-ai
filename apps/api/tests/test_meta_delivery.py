from fastapi import HTTPException
import pytest

from app.db.models.engagement import Conversation, Message, PostEngagement
from app.db.models.identity import Organization, Provider, SocialAccount
from app.integrations.meta import delivery
from app.integrations.meta.client import MetaAPIError


class FakeMetaPublishingService:
    direct_result = {"result_id": "dm-result-1"}
    comment_result = {"result_id": "comment-result-1"}
    direct_error = None
    comment_error = None

    def __init__(self, _db):
        pass

    def reply_to_direct_message(self, **_kwargs):
        if self.direct_error is not None:
            raise self.direct_error
        return self.direct_result

    def reply_to_comment(self, **_kwargs):
        if self.comment_error is not None:
            raise self.comment_error
        return self.comment_result


@pytest.fixture(autouse=True)
def fake_provider(monkeypatch):
    FakeMetaPublishingService.direct_result = {"result_id": "dm-result-1"}
    FakeMetaPublishingService.comment_result = {"result_id": "comment-result-1"}
    FakeMetaPublishingService.direct_error = None
    FakeMetaPublishingService.comment_error = None
    monkeypatch.setattr(delivery, "MetaPublishingService", FakeMetaPublishingService)


def _seed_message(db_session):
    org = Organization(name="Delivery Test", slug="delivery-test")
    db_session.add(org)
    db_session.flush()
    account = SocialAccount(
        organization_id=org.id,
        provider=Provider.meta,
        provider_account_id="page-delivery-test",
        display_name="Delivery Test Page",
        account_type="facebook_page",
        granted_scopes=[],
        metadata_json={},
        is_active=True,
    )
    db_session.add(account)
    db_session.flush()
    conversation = Conversation(
        organization_id=org.id,
        social_account_id=account.id,
        provider="meta",
        channel="messenger",
        external_thread_id="guest-delivery-test",
        participant_id="guest-delivery-test",
        status="open",
        unread_count=0,
    )
    db_session.add(conversation)
    db_session.flush()
    draft = Message(
        organization_id=org.id,
        conversation_id=conversation.id,
        social_account_id=account.id,
        direction="outbound",
        recipient_external_id=conversation.participant_id,
        text="Thank you for contacting our resort.",
        status="sending",
    )
    db_session.add(draft)
    db_session.commit()
    return draft, conversation


def _seed_comment(db_session):
    org = Organization(name="Comment Delivery Test", slug="comment-delivery-test")
    db_session.add(org)
    db_session.flush()
    account = SocialAccount(
        organization_id=org.id,
        provider=Provider.meta,
        provider_account_id="ig-delivery-test",
        display_name="Delivery Test Instagram",
        account_type="instagram_business",
        granted_scopes=[],
        metadata_json={},
        is_active=True,
    )
    db_session.add(account)
    db_session.flush()
    row = PostEngagement(
        organization_id=org.id,
        social_account_id=account.id,
        provider="meta",
        channel="instagram",
        provider_comment_id="comment-delivery-test",
        text="Lovely resort!",
        drafted_reply="Thank you for your kind words.",
        status="sending",
    )
    db_session.add(row)
    db_session.commit()
    return row


def test_direct_message_success_requires_and_stores_provider_id(db_session):
    draft, conversation = _seed_message(db_session)

    result = delivery.deliver_meta_direct_message(db_session, draft, conversation)

    db_session.refresh(draft)
    assert result["status"] == "sent"
    assert draft.status == "sent"
    assert draft.provider_message_id == "dm-result-1"
    assert draft.sent_at is not None
    assert draft.provider_error_code is None


def test_direct_message_known_not_sent_is_retryable_on_same_draft(db_session):
    draft, conversation = _seed_message(db_session)
    FakeMetaPublishingService.direct_error = MetaAPIError(
        "Connection not established", delivery_state="not_sent", retryable=True
    )

    with pytest.raises(MetaAPIError):
        delivery.deliver_meta_direct_message(db_session, draft, conversation)

    db_session.refresh(draft)
    assert draft.status == "failed"
    assert draft.provider_error_code == "meta:not_sent"
    assert delivery.is_retryable_message_failure(draft) is True


def test_direct_message_ambiguous_outcome_is_not_retryable(db_session):
    draft, conversation = _seed_message(db_session)
    FakeMetaPublishingService.direct_error = MetaAPIError(
        "Read timed out", delivery_state="unknown", retryable=False
    )

    result = delivery.deliver_meta_direct_message(db_session, draft, conversation)

    db_session.refresh(draft)
    assert result["status"] == "delivery_unknown"
    assert draft.status == "delivery_unknown"
    assert draft.provider_error_code.startswith("meta:unknown")
    assert delivery.is_retryable_message_failure(draft) is False


def test_direct_message_explicit_forbidden_is_not_retryable(db_session):
    draft, conversation = _seed_message(db_session)
    FakeMetaPublishingService.direct_error = MetaAPIError(
        "Permission denied", status_code=403, delivery_state="rejected", retryable=False
    )

    result = delivery.deliver_meta_direct_message(db_session, draft, conversation)

    db_session.refresh(draft)
    assert result["status"] == "failed"
    assert draft.status == "failed"
    assert draft.provider_error_code.startswith("meta:rejected")
    assert delivery.is_retryable_message_failure(draft) is False


def test_direct_message_preflight_error_is_known_not_sent_but_not_auto_retryable(db_session):
    draft, conversation = _seed_message(db_session)
    FakeMetaPublishingService.direct_error = HTTPException(status_code=409, detail="Token expired")

    result = delivery.deliver_meta_direct_message(db_session, draft, conversation)

    db_session.refresh(draft)
    assert result["status"] == "failed"
    assert draft.provider_error_code.startswith("meta:preflight")
    assert delivery.is_retryable_message_failure(draft) is False


def test_direct_message_missing_provider_id_is_ambiguous(db_session):
    draft, conversation = _seed_message(db_session)
    FakeMetaPublishingService.direct_result = {"result_id": None}

    result = delivery.deliver_meta_direct_message(db_session, draft, conversation)

    db_session.refresh(draft)
    assert result["status"] == "delivery_unknown"
    assert draft.status == "delivery_unknown"


def test_comment_success_requires_and_stores_provider_id(db_session):
    row = _seed_comment(db_session)

    result = delivery.deliver_meta_comment_reply(db_session, row)

    db_session.refresh(row)
    assert result["status"] == "replied"
    assert row.status == "replied"
    assert row.provider_reply_id == "comment-result-1"
    assert row.replied_at is not None
    assert row.last_error is None


def test_comment_known_not_sent_is_retryable_on_same_row(db_session):
    row = _seed_comment(db_session)
    FakeMetaPublishingService.comment_error = MetaAPIError(
        "Connection not established", delivery_state="not_sent", retryable=True
    )

    with pytest.raises(MetaAPIError):
        delivery.deliver_meta_comment_reply(db_session, row)

    db_session.refresh(row)
    assert row.status == "failed"
    assert row.last_error.startswith("meta_delivery|retryable=1|")
    assert delivery.is_retryable_comment_failure(row) is True


def test_comment_ambiguous_outcome_is_not_retryable(db_session):
    row = _seed_comment(db_session)
    FakeMetaPublishingService.comment_error = MetaAPIError(
        "Read timed out", delivery_state="unknown", retryable=False
    )

    result = delivery.deliver_meta_comment_reply(db_session, row)

    db_session.refresh(row)
    assert result["status"] == "delivery_unknown"
    assert row.status == "delivery_unknown"
    assert delivery.is_retryable_comment_failure(row) is False

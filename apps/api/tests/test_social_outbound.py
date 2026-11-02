from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.integrations import social_delivery


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeClient:
    calls = []
    response = None
    error = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, **kwargs):
        FakeClient.calls.append((url, kwargs))
        if FakeClient.error is not None:
            raise FakeClient.error
        return FakeClient.response


class FakeDB:
    def __init__(self, row, account, credential, source=None):
        self.row = row
        self.values = [account, credential]
        self.source = source
        self.commits = 0
        self.rollbacks = 0

    def scalar(self, _stmt):
        return self.values.pop(0)

    def get(self, model, _id):
        if model.__name__ == "Message" and self.source is not None and str(_id) == str(self.source.id):
            return self.source
        return self.row

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def setup_delivery(monkeypatch, channel="whatsapp", *, inbound_age_hours=1, reply_context=False):
    FakeClient.calls = []
    FakeClient.response = None
    FakeClient.error = None
    monkeypatch.setattr(social_delivery.httpx, "Client", FakeClient)
    monkeypatch.setattr(social_delivery, "decrypt_token", lambda _value: "TEST_TOKEN")
    monkeypatch.setattr(
        social_delivery,
        "get_settings",
        lambda: SimpleNamespace(meta_api_version="v25.0"),
    )

    org_id, account_id, conversation_id, inbound_id, draft_id = [uuid4() for _ in range(5)]
    provider = SimpleNamespace(value=channel)
    account = SimpleNamespace(
        id=account_id,
        organization_id=org_id,
        provider=provider,
        provider_account_id="phone-number-123" if channel == "whatsapp" else "telegram-bot-id",
        is_active=True,
    )
    credential = SimpleNamespace(encrypted_value="encrypted-test-token")
    inbound = SimpleNamespace(
        id=inbound_id,
        provider_message_id=f"telegram:-100123:{77}" if channel == "telegram" else "wamid.inbound",
    )
    draft = SimpleNamespace(
        id=draft_id,
        organization_id=org_id,
        social_account_id=account_id,
        conversation_id=conversation_id,
        direction="outbound",
        text="Thank you for contacting our resort.",
        status="sending",
        provider_error_code=None,
        provider_error=None,
        provider_message_id=None,
        sent_at=None,
        reply_to_message_id=inbound_id if reply_context else None,
    )
    conversation = SimpleNamespace(
        id=conversation_id,
        organization_id=org_id,
        social_account_id=account_id,
        channel=channel,
        participant_id="+967700000000" if channel == "whatsapp" else "-100123",
    )
    db = FakeDB(draft, account, credential, source=inbound if reply_context else None)
    recent = datetime.now(timezone.utc) - timedelta(hours=inbound_age_hours)
    monkeypatch.setattr(social_delivery, "_latest_inbound_at", lambda _db, _conversation: recent)
    return db, draft, conversation, account


def test_whatsapp_text_delivery_uses_account_token_and_persists_provider_id(monkeypatch):
    db, draft, conversation, _account = setup_delivery(monkeypatch, "whatsapp")
    FakeClient.response = FakeResponse(200, {"messages": [{"id": "wamid.outbound-1"}]})

    result = social_delivery.deliver_social_direct_message(db, draft, conversation)

    assert result["status"] == "sent"
    assert draft.provider_message_id == "wamid.outbound-1"
    assert draft.status == "sent"
    url, request = FakeClient.calls[0]
    assert url.endswith("/v25.0/phone-number-123/messages")
    assert request["headers"]["Authorization"] == "Bearer TEST_TOKEN"
    assert request["json"]["messaging_product"] == "whatsapp"
    assert request["json"]["to"] == "+967700000000"


def test_whatsapp_outside_24_hour_window_is_not_sent(monkeypatch):
    db, draft, conversation, _account = setup_delivery(monkeypatch, "whatsapp", inbound_age_hours=25)

    result = social_delivery.deliver_social_direct_message(db, draft, conversation)

    assert result["status"] == "failed"
    assert draft.provider_error_code == "whatsapp:service_window_closed"
    assert FakeClient.calls == []


def test_whatsapp_429_is_persisted_and_marked_retryable(monkeypatch):
    db, draft, conversation, _account = setup_delivery(monkeypatch, "whatsapp")
    FakeClient.response = FakeResponse(429, {"error": {"message": "rate limited"}})

    with pytest.raises(social_delivery.SocialDeliveryError):
        social_delivery.deliver_social_direct_message(db, draft, conversation)

    assert draft.status == "failed"
    assert draft.provider_error_code == "whatsapp:rejected:429"
    assert social_delivery.is_retryable_social_message_failure(draft)


def test_whatsapp_read_timeout_is_delivery_unknown_and_not_retryable(monkeypatch):
    db, draft, conversation, _account = setup_delivery(monkeypatch, "whatsapp")
    FakeClient.error = httpx.ReadTimeout("response timeout")

    result = social_delivery.deliver_social_direct_message(db, draft, conversation)

    assert result["status"] == "delivery_unknown"
    assert draft.provider_error_code == "whatsapp:unknown:transport"
    assert not social_delivery.is_retryable_social_message_failure(draft)


def test_whatsapp_connection_failure_is_known_not_sent_and_retryable(monkeypatch):
    db, draft, conversation, _account = setup_delivery(monkeypatch, "whatsapp")
    FakeClient.error = httpx.ConnectError("connection refused")

    with pytest.raises(social_delivery.SocialDeliveryError):
        social_delivery.deliver_social_direct_message(db, draft, conversation)

    assert draft.status == "failed"
    assert draft.provider_error_code == "whatsapp:not_sent:connect_failure"
    assert social_delivery.is_retryable_social_message_failure(draft)


def test_telegram_delivery_replies_to_original_message_and_persists_id(monkeypatch):
    db, draft, conversation, _account = setup_delivery(
        monkeypatch, "telegram", reply_context=True
    )
    FakeClient.response = FakeResponse(200, {"ok": True, "result": {"message_id": 88}})

    result = social_delivery.deliver_social_direct_message(db, draft, conversation)

    assert result["status"] == "sent"
    assert draft.provider_message_id == "88"
    url, request = FakeClient.calls[0]
    assert url == "https://api.telegram.org/botTEST_TOKEN/sendMessage"
    assert request["json"]["chat_id"] == "-100123"
    assert request["json"]["reply_parameters"] == {
        "message_id": 77,
        "allow_sending_without_reply": True,
    }


def test_telegram_403_is_rejected_and_not_retryable(monkeypatch):
    db, draft, conversation, _account = setup_delivery(monkeypatch, "telegram")
    FakeClient.response = FakeResponse(200, {"ok": False, "error_code": 403, "description": "bot blocked"})

    result = social_delivery.deliver_social_direct_message(db, draft, conversation)

    assert result["status"] == "failed"
    assert draft.provider_error_code == "telegram:rejected:http_403"
    assert not social_delivery.is_retryable_social_message_failure(draft)


def test_provider_mismatch_fails_closed_without_http_request(monkeypatch):
    db, draft, conversation, account = setup_delivery(monkeypatch, "whatsapp")
    account.provider = SimpleNamespace(value="telegram")

    result = social_delivery.deliver_social_direct_message(db, draft, conversation)

    assert result["status"] == "failed"
    assert FakeClient.calls == []


def test_telegram_response_without_message_id_is_delivery_unknown(monkeypatch):
    db, draft, conversation, _account = setup_delivery(monkeypatch, "telegram")
    FakeClient.response = FakeResponse(200, {"ok": True, "result": {"chat": {"id": -100123}}})

    result = social_delivery.deliver_social_direct_message(db, draft, conversation)

    assert result["status"] == "delivery_unknown"
    assert draft.provider_error_code == "telegram:unknown:missing_message_id"

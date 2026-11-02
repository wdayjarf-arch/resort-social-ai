from types import SimpleNamespace

import httpx
import pytest

from app.integrations.meta import client as meta_client


class FakeResponse:
    def __init__(self, status_code=200, payload=None, *, json_error=False):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"id": "result-1"}
        self._json_error = json_error
        self.is_error = status_code >= 400

    def json(self):
        if self._json_error:
            raise ValueError("not json")
        return self._payload


class FakeClient:
    response = FakeResponse()
    exception = None
    last_request = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def request(self, method, url, **kwargs):
        FakeClient.last_request = (method, url, kwargs)
        if FakeClient.exception is not None:
            raise FakeClient.exception
        return FakeClient.response


@pytest.fixture(autouse=True)
def fake_meta_client(monkeypatch):
    monkeypatch.setattr(
        meta_client,
        "get_settings",
        lambda: SimpleNamespace(meta_api_version="v25.0"),
    )
    monkeypatch.setattr(meta_client.httpx, "Client", FakeClient)
    FakeClient.response = FakeResponse()
    FakeClient.exception = None
    FakeClient.last_request = None


def test_success_returns_payload_without_logging_token():
    result = meta_client.MetaGraphClient().post("page-1/messages", token="secret-token", data={"text": "hello"})

    assert result == {"id": "result-1"}
    method, url, kwargs = FakeClient.last_request
    assert method == "POST"
    assert url == "https://graph.facebook.com/v25.0/page-1/messages"
    assert kwargs["headers"]["Authorization"] == "Bearer secret-token"
    assert "secret-token" not in url


def test_connect_error_is_known_not_sent_and_retryable():
    FakeClient.exception = httpx.ConnectError(
        "connection refused",
        request=httpx.Request("POST", "https://graph.facebook.com/v25.0/page-1/messages"),
    )

    with pytest.raises(meta_client.MetaAPIError) as caught:
        meta_client.MetaGraphClient().post("page-1/messages", token="secret-token", data={"text": "hello"})

    assert caught.value.delivery_state == "not_sent"
    assert caught.value.retryable is True
    assert caught.value.status_code is None


def test_read_timeout_is_unknown_and_not_retryable():
    FakeClient.exception = httpx.ReadTimeout(
        "read timed out",
        request=httpx.Request("POST", "https://graph.facebook.com/v25.0/page-1/messages"),
    )

    with pytest.raises(meta_client.MetaAPIError) as caught:
        meta_client.MetaGraphClient().post("page-1/messages", token="secret-token", data={"text": "hello"})

    assert caught.value.delivery_state == "unknown"
    assert caught.value.retryable is False


def test_rate_limit_is_explicit_rejection_and_retryable():
    FakeClient.response = FakeResponse(429, {"error": {"message": "Rate limited", "code": 4}})

    with pytest.raises(meta_client.MetaAPIError) as caught:
        meta_client.MetaGraphClient().post("page-1/messages", token="secret-token", data={"text": "hello"})

    assert caught.value.delivery_state == "rejected"
    assert caught.value.retryable is True
    assert caught.value.provider_code == "4"


def test_forbidden_response_is_rejected_and_not_retryable():
    FakeClient.response = FakeResponse(403, {"error": {"message": "Permission denied", "code": 10}})

    with pytest.raises(meta_client.MetaAPIError) as caught:
        meta_client.MetaGraphClient().post("page-1/messages", token="secret-token", data={"text": "hello"})

    assert caught.value.delivery_state == "rejected"
    assert caught.value.retryable is False
    assert caught.value.status_code == 403


def test_server_error_is_unknown_to_prevent_duplicate_send():
    FakeClient.response = FakeResponse(500, {"error": {"message": "Internal error", "code": 1}})

    with pytest.raises(meta_client.MetaAPIError) as caught:
        meta_client.MetaGraphClient().post("page-1/messages", token="secret-token", data={"text": "hello"})

    assert caught.value.delivery_state == "unknown"
    assert caught.value.retryable is False


def test_non_json_success_response_is_unknown():
    FakeClient.response = FakeResponse(200, json_error=True)

    with pytest.raises(meta_client.MetaAPIError) as caught:
        meta_client.MetaGraphClient().post("page-1/messages", token="secret-token", data={"text": "hello"})

    assert caught.value.delivery_state == "unknown"
    assert caught.value.status_code == 200


def test_rejects_absolute_urls_and_parent_paths():
    api = meta_client.MetaGraphClient()
    with pytest.raises(ValueError):
        api.get("https://example.com/steal")
    with pytest.raises(ValueError):
        api.get("page/../messages")

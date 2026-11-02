import hashlib
import hmac
import json


def test_meta_signature_uses_raw_body(monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("JWT_SECRET_KEY", "test-only-jwt-secret-that-is-at-least-32-bytes-long")
    monkeypatch.setenv("META_WEBHOOK_APP_SECRET", "test-meta-app-secret")
    from app.core.config import get_settings
    get_settings.cache_clear()
    from app.api.routes.meta_webhooks import _verify_signature

    raw = b'{"object":"page","entry":[{"id":"123","messaging":[{"message":{"text":"hello"}}]}]}'
    signature = "sha256=" + hmac.new(b"test-meta-app-secret", raw, hashlib.sha256).hexdigest()
    assert _verify_signature(raw, signature) is True
    assert _verify_signature(raw + b" ", signature) is False
    assert _verify_signature(raw, "sha256=" + "0" * 64) is False
    get_settings.cache_clear()


def test_classifies_messenger_instagram_and_comments():
    from app.workers.tasks import classify_meta_event_types

    messenger = {"object": "page", "entry": [{"id": "p1", "messaging": [{"message": {"text": "hello"}}]}]}
    instagram = {"object": "instagram", "entry": [{"id": "ig1", "messaging": [{"message": {"text": "hi"}}]}]}
    comment = {"object": "instagram", "entry": [{"id": "ig1", "changes": [{"field": "comments", "value": {"id": "c1"}}]}]}

    assert "messenger_dm" in classify_meta_event_types(messenger)
    assert "instagram_dm" in classify_meta_event_types(instagram)
    assert "instagram_comment" in classify_meta_event_types(comment)

from app.api.routes.social_webhooks import (
    extract_telegram_message,
    extract_whatsapp_messages,
    verify_whatsapp_signature,
)
import hashlib
import hmac


def test_whatsapp_signature_accepts_valid_hmac():
    body = b'{"object":"whatsapp_business_account"}'
    secret = "unit-test-app-secret"
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_whatsapp_signature(body, signature, secret)


def test_whatsapp_signature_rejects_wrong_body_or_missing_header():
    body = b'{"object":"whatsapp_business_account"}'
    secret = "unit-test-app-secret"
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert not verify_whatsapp_signature(body + b" ", signature, secret)
    assert not verify_whatsapp_signature(body, None, secret)
    assert not verify_whatsapp_signature(body, signature, None)


def test_extract_whatsapp_messages_reads_text_and_contact_name():
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "changes": [{
                "field": "messages",
                "value": {
                    "metadata": {"phone_number_id": "phone-1"},
                    "contacts": [{"wa_id": "967700000001", "profile": {"name": "Guest"}}],
                    "messages": [{"id": "wamid.1", "from": "967700000001", "type": "text", "text": {"body": "مرحبا"}}],
                },
            }],
        }],
    }
    rows = extract_whatsapp_messages(payload)
    assert len(rows) == 1
    assert rows[0][0] == "phone-1"
    assert rows[0][1]["id"] == "wamid.1"
    assert rows[0][2] == "Guest"


def test_extract_whatsapp_messages_ignores_status_events_and_bad_shapes():
    assert extract_whatsapp_messages({"entry": "bad"}) == []
    assert extract_whatsapp_messages({"entry": [{"changes": [{"field": "messages", "value": {"statuses": [{}]}}]}]}) == []


def test_extract_telegram_private_message():
    row = extract_telegram_message({"update_id": 12, "message": {
        "message_id": 44, "date": 1720000000,
        "chat": {"id": 777, "type": "private"},
        "from": {"id": 777, "is_bot": False},
        "text": "Hello resort",
    }})
    assert row is not None
    assert row["chat_id"] == "777"
    assert row["message_id"] == "44"
    assert row["text"] == "Hello resort"
    assert row["attachments"] == []


def test_extract_telegram_group_media_and_ignores_bot_messages():
    row = extract_telegram_message({"message": {
        "message_id": 9,
        "chat": {"id": -100123, "type": "supergroup"},
        "from": {"id": 99, "is_bot": False},
        "photo": [{"file_id": "photo-file"}],
        "caption": "Pool photo",
    }})
    assert row is not None
    assert row["chat_id"] == "-100123"
    assert row["text"] == "Pool photo"
    assert row["attachments"][0]["type"] == "photo"
    assert extract_telegram_message({"message": {
        "message_id": 10, "chat": {"id": 777}, "from": {"id": 1, "is_bot": True}, "text": "echo"
    }}) is None


def test_extract_telegram_ignores_non_message_updates():
    assert extract_telegram_message({"update_id": 1, "callback_query": {}}) is None
    assert extract_telegram_message({"message": {"chat": {"id": 1}}}) is None

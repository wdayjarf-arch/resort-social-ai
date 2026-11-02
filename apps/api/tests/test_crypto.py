import os

from cryptography.fernet import Fernet


def test_token_encryption_roundtrip(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    from app.core.config import get_settings
    from app.core import crypto

    get_settings.cache_clear()
    token = "sensitive-access-token"
    ciphertext = crypto.encrypt_token(token)

    assert ciphertext != token
    assert crypto.decrypt_token(ciphertext) == token
    get_settings.cache_clear()

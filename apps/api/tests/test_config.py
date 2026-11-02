from cryptography.fernet import Fernet

from app.core.config import Settings


def test_settings_parse_cors_and_defaults():
    settings = Settings(
        token_encryption_key=Fernet.generate_key().decode(),
        cors_origins="http://localhost:3000,https://dashboard.example.com",
    )
    assert settings.cors_origin_list == [
        "http://localhost:3000",
        "https://dashboard.example.com",
    ]
    assert settings.ai_auto_reply_enabled is False
    assert settings.ai_require_human_approval is True

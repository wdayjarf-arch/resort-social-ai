from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class TokenDecryptionError(RuntimeError):
    """Raised when a stored provider token cannot be decrypted."""


def _fernet() -> Fernet:
    raw_key = get_settings().token_encryption_key.get_secret_value().encode("utf-8")
    try:
        return Fernet(raw_key)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY must be a valid Fernet key") from exc


def encrypt_token(plaintext: str) -> str:
    if not plaintext:
        raise ValueError("Refusing to encrypt an empty token")
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_token(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeDecodeError) as exc:
        raise TokenDecryptionError("Token decryption failed; check key configuration/rotation") from exc

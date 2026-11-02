"""Password hashing helpers using Argon2id through pwdlib."""
from pwdlib import PasswordHash

_password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Password must contain at least 12 characters")
    if len(password) > 1024:
        raise ValueError("Password is too long")
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    # Do not disclose whether an account exists or uses external identity.
    if not password_hash:
        return False
    try:
        return _password_hash.verify(password, password_hash)
    except (ValueError, TypeError):
        return False

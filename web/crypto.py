from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from web.config import get_settings


def _fernet() -> Fernet:
    settings = get_settings()
    key = settings.token_fernet_key.strip()
    if not key:
        # Derive a stable-but-dev-only key from secret_key so local boot works.
        digest = hashlib.sha256(settings.secret_key.encode()).digest()
        key = base64.urlsafe_b64encode(digest).decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_secret(plain: str) -> str:
    if not plain:
        return ""
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Could not decrypt stored token") from exc

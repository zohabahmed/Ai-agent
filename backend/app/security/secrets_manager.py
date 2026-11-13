"""Secrets management, encryption, and log redaction."""

from __future__ import annotations

import base64
import hashlib
import re
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

_SECRET_FIELD_PATTERN = re.compile(
    r"(api[_-]?key|secret|password|token|passphrase|private[_-]?key|seed)",
    re.IGNORECASE,
)


class SecretsManager:
    """Handles symmetric encryption of sensitive values and safe log redaction."""

    def __init__(self, encryption_key: str | None = None) -> None:
        settings = get_settings()
        raw_key = encryption_key or settings.encryption_key.get_secret_value()
        if not raw_key:
            # Derive a deterministic Fernet key from secret_key for dev convenience
            digest = hashlib.sha256(settings.secret_key.get_secret_value().encode("utf-8")).digest()
            self._fernet = Fernet(base64.urlsafe_b64encode(digest))
        else:
            try:
                self._fernet = Fernet(raw_key.encode("utf-8"))
            except Exception:
                digest = hashlib.sha256(raw_key.encode("utf-8")).digest()
                self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    def encrypt(self, plaintext: str) -> str:
        if not plaintext:
            return ""
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        if not ciphertext:
            return ""
        try:
            return self._fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Unable to decrypt secret with current encryption key.") from exc

    @staticmethod
    def mask_secret(value: str) -> str:
        if not value:
            return ""
        if len(value) <= 6:
            return "***"
        return f"{value[:2]}***{value[-2:]}"

    @classmethod
    def redact_dict(cls, payload: dict[str, Any]) -> dict[str, Any]:
        sanitized: dict[str, Any] = {}
        for k, v in payload.items():
            if _SECRET_FIELD_PATTERN.search(k):
                sanitized[k] = "[REDACTED]"
            elif isinstance(v, dict):
                sanitized[k] = cls.redact_dict(v)
            elif isinstance(v, list):
                sanitized[k] = [
                    cls.redact_dict(item) if isinstance(item, dict) else item
                    for item in v
                ]
            else:
                sanitized[k] = v
        return sanitized

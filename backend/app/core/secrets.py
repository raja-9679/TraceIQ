"""Fernet encryption for ProjectSecret values.

The key is derived from SECRET_KEY, so rotating SECRET_KEY invalidates all
stored secrets (they must be re-entered). Plaintext leaves the backend only
inside dispatched job payloads.
"""
import base64
import hashlib

from cryptography.fernet import Fernet

from app.core.config import settings


def _fernet() -> Fernet:
    digest = hashlib.sha256(f"traceiq-project-secrets:{settings.SECRET_KEY}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()

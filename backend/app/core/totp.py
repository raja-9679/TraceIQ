"""RFC 6238 TOTP (time-based one-time passwords) — no external dependency.

Used for MFA. Secrets are base32 (Google Authenticator / Authy compatible).
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote


def generate_secret() -> str:
    """A fresh base32 secret (160-bit)."""
    return base64.b32encode(secrets.token_bytes(20)).decode("utf-8").rstrip("=")


def _hotp(secret_b32: str, counter: int) -> str:
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8))
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def now_code(secret_b32: str, t: float = None) -> str:
    """The current 6-digit code — handy for tests."""
    t = int(t if t is not None else time.time())
    return _hotp(secret_b32, t // 30)


def verify(secret_b32: str, code: str, window: int = 1, t: float = None) -> bool:
    """Verify a code, tolerating ±`window` 30s steps for clock skew."""
    if not code or not secret_b32:
        return False
    code = str(code).strip().replace(" ", "")
    if len(code) != 6 or not code.isdigit():
        return False
    t = int(t if t is not None else time.time())
    counter = t // 30
    for w in range(-window, window + 1):
        if hmac.compare_digest(_hotp(secret_b32, counter + w), code):
            return True
    return False


def provisioning_uri(secret_b32: str, account: str, issuer: str = "TraceIQ") -> str:
    """otpauth:// URI for QR-code enrollment."""
    label = quote(f"{issuer}:{account}")
    return (f"otpauth://totp/{label}?secret={secret_b32}"
            f"&issuer={quote(issuer)}&digits=6&period=30")

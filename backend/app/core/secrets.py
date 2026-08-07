"""Symmetric encryption for secrets at rest.

Covers `ProjectSecret.value_encrypted`, MFA TOTP secrets, LLM provider API
keys, issue-tracker credentials, instance settings marked `secret`, and stored
browser session state.

What this replaces
------------------
The previous implementation was an unsalted SHA-256 of
`"traceiq-project-secrets:" + SECRET_KEY`, base64'd into a Fernet key. Three
problems, all of which mattered in practice:

*Rotation was destructive.* Ciphertext carried no key identifier, so `decrypt`
could not distinguish old key material from new. Changing the key silently
turned every stored secret into an undecryptable blob —
`instance_settings._load_overrides_sync` already swallowed that failure and
reverted admin-saved SMTP/OIDC/LLM secrets to their env values.

*The key was welded to JWT signing.* `SECRET_KEY` signs sessions. You could not
rotate it without destroying every stored secret at the same moment, so neither
ever got rotated.

*There was nowhere to put a KMS.* Key custody was hardcoded.

What this does
--------------
Ciphertext is written with a version envelope (`v1:…`) and encrypted through a
`MultiFernet` keyring: the first key encrypts, any key decrypts. That gives a
rotation *overlap window* — add the new key at the front, re-encrypt in the
background, then drop the old one.

`SECRETS_KEY` is the key material, independent of `SECRET_KEY`.
`SECRETS_KEY_PREVIOUS` holds retired keys during an overlap.

Backward compatibility is the load-bearing part: existing deployments hold bare
(unenveloped) ciphertext written under the old derivation. `decrypt` still
reads it via `legacy_secret_key`, and `needs_rotation` flags it so a migration
can re-encrypt. `legacy_fernet_key` is pinned by test and must never change —
altering it makes every existing deployment's secrets unreadable.
"""
from __future__ import annotations

import base64
import json
import hashlib
from typing import Any, List, Optional, Sequence

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

#: Marks ciphertext written by this module. Bare ciphertext is pre-envelope.
ENVELOPE_PREFIX = "v1:"

#: Domain separator for passphrase derivation. Deliberately different from the
#: legacy label so a deployment that reuses its old SECRET_KEY string as
#: SECRETS_KEY does not land back on the legacy key.
_DERIVATION_LABEL = "traceiq-secrets-v1:"

_LEGACY_LABEL = "traceiq-project-secrets:"


def legacy_fernet_key(secret_key: str) -> bytes:
    """The pre-envelope key derivation. Pinned by test — never change this.

    Every deployment that predates the envelope has ciphertext encrypted under
    exactly this. Changing it makes those secrets permanently unreadable.
    """
    digest = hashlib.sha256(f"{_LEGACY_LABEL}{secret_key}".encode()).digest()
    return base64.urlsafe_b64encode(digest)


def derive_fernet_key(key_material: str) -> bytes:
    """Turn arbitrary key material into a Fernet key.

    A real 32-byte urlsafe-base64 Fernet key is used verbatim so operators can
    supply one from a KMS or `Fernet.generate_key()`. Anything else is treated
    as a passphrase and hashed.
    """
    candidate = key_material.strip()
    try:
        raw = base64.urlsafe_b64decode(candidate)
        if len(raw) == 32:
            return candidate.encode()
    except Exception:  # noqa: BLE001 — not base64; fall through to derivation
        pass
    digest = hashlib.sha256(f"{_DERIVATION_LABEL}{candidate}".encode()).digest()
    return base64.urlsafe_b64encode(digest)


class SecretBox:
    """A keyring that encrypts with one key and decrypts with several.

    Constructed explicitly (rather than reading settings) so it is testable
    without environment manipulation; `default_box()` wires it to settings.
    """

    def __init__(
        self,
        current_key: str,
        previous_keys: Sequence[str] = (),
        legacy_secret_key: Optional[str] = None,
    ) -> None:
        if not current_key or not str(current_key).strip():
            raise ValueError(
                "SecretBox requires key material; refusing to construct without it")

        self._current = Fernet(derive_fernet_key(str(current_key)))

        ring: List[Fernet] = [self._current]
        for key in previous_keys:
            if key and str(key).strip():
                ring.append(Fernet(derive_fernet_key(str(key))))
        self._ring = MultiFernet(ring)

        # Only used to read ciphertext written before the envelope existed.
        self._legacy = (
            Fernet(legacy_fernet_key(str(legacy_secret_key)))
            if legacy_secret_key else None
        )

    def encrypt(self, plaintext: str) -> str:
        token = self._current.encrypt(plaintext.encode()).decode()
        return f"{ENVELOPE_PREFIX}{token}"

    def decrypt(self, ciphertext: str) -> str:
        raw = str(ciphertext)

        if raw.startswith(ENVELOPE_PREFIX):
            return self._ring.decrypt(raw[len(ENVELOPE_PREFIX):].encode()).decode()

        # An envelope we don't recognise is a hard failure, not something to
        # guess at — reading it with the wrong scheme would be worse.
        if raw[:1] == "v" and ":" in raw[:5]:
            raise InvalidToken(f"unknown secret envelope version in {raw[:4]!r}")

        # Pre-envelope ciphertext: try the current ring first (a deployment may
        # have re-encrypted without adding the prefix), then the legacy key.
        try:
            return self._ring.decrypt(raw.encode()).decode()
        except InvalidToken:
            if self._legacy is None:
                raise
            return self._legacy.decrypt(raw.encode()).decode()

    def needs_rotation(self, ciphertext: str) -> bool:
        """True when `ciphertext` is readable but not written under the current
        key — i.e. re-encrypting it would retire old key material."""
        raw = str(ciphertext)
        if not raw.startswith(ENVELOPE_PREFIX):
            return True
        token = raw[len(ENVELOPE_PREFIX):].encode()
        try:
            self._current.decrypt(token)
            return False
        except InvalidToken:
            return True

    def rotate(self, ciphertext: str) -> str:
        """Re-encrypt under the current key. Raises if it cannot be read."""
        return self.encrypt(self.decrypt(ciphertext))


_box: Optional[SecretBox] = None


def default_box() -> SecretBox:
    """The process-wide keyring, built from settings on first use.

    Falls back to `SECRET_KEY` when `SECRETS_KEY` is unset so an existing
    deployment keeps working untouched — but in that mode the legacy
    derivation is also what reads old ciphertext, which is exactly the
    situation `SECRETS_KEY` exists to let operators leave.
    """
    global _box
    if _box is None:
        from app.core.config import settings
        current = (getattr(settings, "SECRETS_KEY", "") or "").strip() or settings.SECRET_KEY
        previous = [
            part.strip()
            for part in (getattr(settings, "SECRETS_KEY_PREVIOUS", "") or "").split(",")
            if part.strip()
        ]
        _box = SecretBox(current, previous_keys=previous,
                         legacy_secret_key=settings.SECRET_KEY)
    return _box


def reset_default_box() -> None:
    """Drop the cached keyring. For tests and for post-rotation reloads."""
    global _box
    _box = None


# --------------------------------------------------------------------------
# Encrypted JSON columns
# --------------------------------------------------------------------------

#: Marker key identifying an encrypted JSON envelope.
ENCRYPTED_JSON_KEY = "__enc__"


def is_encrypted_json(value: Any) -> bool:
    return isinstance(value, dict) and ENCRYPTED_JSON_KEY in value


def encrypt_json(value: Optional[dict], box: Optional[SecretBox] = None) -> Optional[dict]:
    """Wrap a JSON-serialisable dict in an encrypted envelope.

    The envelope is itself a dict so the value still fits an existing
    `Column(JSON)` — no schema migration, and a row written by an older build
    stays readable. That matters here because these columns are written by
    Celery tasks, which routinely lag a backend deploy.

    An already-encrypted value is returned unchanged: persona refresh re-saves
    state, and nesting an envelope inside an envelope would make it
    undecryptable by the single-unwrap read path.
    """
    if value is None or value == {}:
        return value
    if is_encrypted_json(value):
        return value
    box = box or default_box()
    return {ENCRYPTED_JSON_KEY: box.encrypt(json.dumps(value))}


def decrypt_json(value: Optional[dict], box: Optional[SecretBox] = None) -> Optional[dict]:
    """Unwrap an encrypted envelope, passing legacy plaintext through."""
    if not is_encrypted_json(value):
        return value
    box = box or default_box()
    return json.loads(box.decrypt(value[ENCRYPTED_JSON_KEY]))


def encrypt_secret(plaintext: str) -> str:
    return default_box().encrypt(plaintext)


def decrypt_secret(ciphertext: str) -> str:
    return default_box().decrypt(ciphertext)


def secret_needs_rotation(ciphertext: str) -> bool:
    return default_box().needs_rotation(ciphertext)

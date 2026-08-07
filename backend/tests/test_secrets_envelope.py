"""Secret encryption, key rotation, and backward compatibility.

The original module was 25 lines: an unsalted SHA-256 of
`"traceiq-project-secrets:" + SECRET_KEY`, b64'd into a Fernet key. Three
consequences, all of which this replaces:

1. Ciphertext carried no key identifier, so `decrypt` could not tell old key
   material from new. Rotation was destructive *by design* — the docstring said
   so — and `instance_settings` already had a path that silently dropped
   admin-saved SMTP/OIDC/LLM secrets back to their env values when it happened.
2. The key was welded to `SECRET_KEY`, which also signs JWTs. You could not
   rotate your JWT signing key without simultaneously destroying every stored
   secret, so in practice neither ever got rotated.
3. There was no way to hand key custody to a KMS.

Existing deployments hold ciphertext written by the old scheme, so the decrypt
path has to keep reading it. That compatibility is the most important thing
tested here: getting it wrong silently bricks every stored credential on
upgrade.
"""
import pytest
from cryptography.fernet import Fernet, InvalidToken

from app.core.secrets import (
    ENVELOPE_PREFIX,
    SecretBox,
    derive_fernet_key,
    legacy_fernet_key,
)

KEY_A = "key-alpha-not-a-real-deployment-key"
KEY_B = "key-bravo-not-a-real-deployment-key"
LEGACY_SECRET_KEY = "legacy-app-secret-key-from-before-the-split"


def box(current=KEY_A, previous=(), legacy=None) -> SecretBox:
    return SecretBox(current, previous_keys=previous, legacy_secret_key=legacy)


# --------------------------------------------------------------------------
# Round trip
# --------------------------------------------------------------------------

def test_round_trip():
    b = box()
    assert b.decrypt(b.encrypt("hunter2")) == "hunter2"


def test_ciphertext_is_not_the_plaintext():
    assert "hunter2" not in box().encrypt("hunter2")


def test_ciphertext_carries_a_version_envelope():
    # The whole point: ciphertext must say which scheme wrote it.
    assert box().encrypt("x").startswith(ENVELOPE_PREFIX)


def test_encryption_is_non_deterministic():
    # Fernet includes a random IV; identical plaintexts must not collide,
    # or an observer could match equal secrets across projects.
    b = box()
    assert b.encrypt("same") != b.encrypt("same")


def test_unicode_survives_the_round_trip():
    b = box()
    assert b.decrypt(b.encrypt("passwörd–ਪੰਜਾਬੀ")) == "passwörd–ਪੰਜਾਬੀ"


def test_empty_string_round_trips():
    b = box()
    assert b.decrypt(b.encrypt("")) == ""


# --------------------------------------------------------------------------
# Rotation
# --------------------------------------------------------------------------

def test_a_secret_written_under_the_previous_key_still_decrypts():
    old = box(current=KEY_B)
    rotated = box(current=KEY_A, previous=[KEY_B])
    assert rotated.decrypt(old.encrypt("hunter2")) == "hunter2"


def test_rotation_writes_under_the_new_key():
    rotated = box(current=KEY_A, previous=[KEY_B])
    # Something written after rotation must not need the old key any more.
    fresh = rotated.encrypt("hunter2")
    assert box(current=KEY_A).decrypt(fresh) == "hunter2"


def test_a_key_no_longer_in_the_ring_cannot_decrypt():
    # Once the overlap window closes, retired material is genuinely retired.
    old = box(current=KEY_B)
    ciphertext = old.encrypt("hunter2")
    with pytest.raises(InvalidToken):
        box(current=KEY_A).decrypt(ciphertext)


def test_needs_rotation_flags_ciphertext_not_written_under_the_current_key():
    old = box(current=KEY_B)
    rotated = box(current=KEY_A, previous=[KEY_B])
    assert rotated.needs_rotation(old.encrypt("x")) is True
    assert rotated.needs_rotation(rotated.encrypt("x")) is False


def test_several_previous_keys_are_supported():
    c_key = "key-charlie-not-a-real-deployment-key"
    oldest = box(current=c_key)
    ring = box(current=KEY_A, previous=[KEY_B, c_key])
    assert ring.decrypt(oldest.encrypt("hunter2")) == "hunter2"


# --------------------------------------------------------------------------
# Legacy compatibility — ciphertext written before the envelope existed
# --------------------------------------------------------------------------

def _legacy_ciphertext(plaintext: str, secret_key: str) -> str:
    """Byte-for-byte what the old 25-line module produced."""
    return Fernet(legacy_fernet_key(secret_key)).encrypt(plaintext.encode()).decode()


def test_legacy_bare_ciphertext_still_decrypts():
    # An upgrade must not brick every ProjectSecret in the database.
    legacy = _legacy_ciphertext("hunter2", LEGACY_SECRET_KEY)
    b = box(current=KEY_A, legacy=LEGACY_SECRET_KEY)
    assert b.decrypt(legacy) == "hunter2"


def test_legacy_ciphertext_is_flagged_for_rotation():
    legacy = _legacy_ciphertext("hunter2", LEGACY_SECRET_KEY)
    assert box(current=KEY_A, legacy=LEGACY_SECRET_KEY).needs_rotation(legacy) is True


def test_legacy_ciphertext_fails_cleanly_without_the_legacy_key():
    legacy = _legacy_ciphertext("hunter2", LEGACY_SECRET_KEY)
    with pytest.raises(InvalidToken):
        box(current=KEY_A).decrypt(legacy)


def test_legacy_derivation_is_unchanged():
    # Pin the old derivation exactly. If this changes, every deployment's
    # existing secrets become unreadable.
    import base64, hashlib
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(f"traceiq-project-secrets:{LEGACY_SECRET_KEY}".encode()).digest())
    assert legacy_fernet_key(LEGACY_SECRET_KEY) == expected


# --------------------------------------------------------------------------
# Key derivation
# --------------------------------------------------------------------------

def test_a_raw_fernet_key_is_used_as_is():
    raw = Fernet.generate_key().decode()
    b = SecretBox(raw)
    assert b.decrypt(b.encrypt("x")) == "x"


def test_a_passphrase_is_derived_into_a_valid_fernet_key():
    key = derive_fernet_key("some passphrase that is not base64")
    Fernet(key)  # must not raise


def test_derivation_is_stable():
    assert derive_fernet_key("abc") == derive_fernet_key("abc")


def test_different_passphrases_derive_different_keys():
    assert derive_fernet_key("abc") != derive_fernet_key("abd")


def test_derivation_is_domain_separated_from_the_legacy_scheme():
    # A deployment that sets SECRETS_KEY to the same string as its old
    # SECRET_KEY must not accidentally land on the legacy key.
    assert derive_fernet_key(LEGACY_SECRET_KEY) != legacy_fernet_key(LEGACY_SECRET_KEY)


# --------------------------------------------------------------------------
# Failure modes
# --------------------------------------------------------------------------

def test_garbage_ciphertext_raises_rather_than_returning_plaintext():
    with pytest.raises(InvalidToken):
        box().decrypt("not-a-token")


def test_envelope_with_an_unknown_version_raises():
    with pytest.raises(InvalidToken):
        box().decrypt("v99:whatever")


def test_a_box_with_no_key_material_refuses_to_construct():
    with pytest.raises(ValueError):
        SecretBox("")


def test_tampered_ciphertext_is_rejected():
    b = box()
    token = b.encrypt("hunter2")
    tampered = token[:-2] + ("AA" if not token.endswith("AA") else "BB")
    with pytest.raises(InvalidToken):
        b.decrypt(tampered)

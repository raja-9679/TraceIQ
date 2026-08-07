"""Encryption of stored browser session state.

`AuthSession.storage_state` and `Persona.session_state` hold live Playwright
storageState: session cookies and localStorage for a logged-in account on the
application under test. Anyone with read access to the database could resume
those sessions. They were stored as plaintext JSON and were not routed through
the secrets module at all.

The envelope is a dict (`{"__enc__": "v1:…"}`) rather than a bare string so it
still fits the existing `Column(JSON)` — no migration, and a row written by an
older build is still readable, which matters because these columns are written
by Celery tasks that may lag a backend deploy.
"""
import pytest
from cryptography.fernet import InvalidToken

from app.core.secrets import (
    SecretBox,
    decrypt_json,
    encrypt_json,
    is_encrypted_json,
)

STATE = {
    "cookies": [{"name": "sid", "value": "abc123", "domain": "app.example.com"}],
    "origins": [{"origin": "https://app.example.com",
                 "localStorage": [{"name": "token", "value": "eyJhbGciOi"}]}],
}

BOX = SecretBox("session-state-test-key-material-here")


def test_round_trip_preserves_the_state_exactly():
    assert decrypt_json(encrypt_json(STATE, BOX), BOX) == STATE


def test_the_stored_form_is_a_dict_so_it_still_fits_a_json_column():
    assert isinstance(encrypt_json(STATE, BOX), dict)


def test_the_cookie_value_is_not_present_in_the_stored_form():
    assert "abc123" not in str(encrypt_json(STATE, BOX))


def test_localstorage_tokens_are_not_present_in_the_stored_form():
    assert "eyJhbGciOi" not in str(encrypt_json(STATE, BOX))


def test_encrypted_state_is_recognisable():
    assert is_encrypted_json(encrypt_json(STATE, BOX)) is True


def test_plaintext_state_is_not_mistaken_for_encrypted():
    assert is_encrypted_json(STATE) is False
    assert is_encrypted_json({}) is False
    assert is_encrypted_json(None) is False


def test_legacy_plaintext_state_is_returned_as_is():
    # Rows written before this shipped must keep working, including rows
    # written by a Celery worker that has not yet been redeployed.
    assert decrypt_json(STATE, BOX) == STATE


def test_none_and_empty_pass_through_untouched():
    assert encrypt_json(None, BOX) is None
    assert decrypt_json(None, BOX) is None
    assert encrypt_json({}, BOX) == {}
    assert decrypt_json({}, BOX) == {}


def test_encryption_is_non_deterministic():
    assert encrypt_json(STATE, BOX) != encrypt_json(STATE, BOX)


def test_a_wrong_key_cannot_read_the_state():
    other = SecretBox("a-completely-different-key-value-x")
    with pytest.raises(InvalidToken):
        decrypt_json(encrypt_json(STATE, BOX), other)


def test_nested_structure_survives():
    nested = {"a": [1, 2, {"b": None, "c": True}], "d": {"e": 1.5}}
    assert decrypt_json(encrypt_json(nested, BOX), BOX) == nested


def test_double_encryption_is_avoided():
    # Persona refresh re-saves state; wrapping an envelope in an envelope would
    # make it undecryptable by the plain read path.
    once = encrypt_json(STATE, BOX)
    assert encrypt_json(once, BOX) == once

"""Tamper-evident audit chaining.

The audit log was a plain mutable table: no immutability, no DB constraint, and
`workspace_service.delete_workspace` actively rewrote existing rows. Anyone with
ORM or database access could edit or delete history, which is precisely what an
audit trail exists to prevent, and what SOC 2 CC7 and PCI DSS Requirement 10
ask about.

Full immutability needs a DB trigger (added in the migration alongside this).
The hash chain is the complementary half: a trigger stops the ordinary paths,
and the chain makes any edit that *does* land — by a superuser, a restore from
a doctored dump, a direct UPDATE — detectable afterwards. Neither alone is
enough; a trigger you can drop is not evidence, and a chain nobody verifies is
not protection.

Each row commits to its predecessor, so altering row N invalidates every row
after it. You cannot quietly rewrite one entry.
"""
from datetime import datetime

from app.services.audit import (
    ROOT_HASH,
    chain_hash,
    verify_chain,
)


def _entry(**overrides):
    base = dict(
        entity_type="case",
        entity_id=7,
        action="update",
        user_id=3,
        workspace_id=1,
        timestamp=datetime(2026, 8, 7, 12, 0, 0),
        changes={"name": "login journey"},
        prev_hash=ROOT_HASH,
    )
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# Hashing
# --------------------------------------------------------------------------

def test_hash_is_deterministic():
    assert chain_hash(**_entry()) == chain_hash(**_entry())


def test_hash_is_hex_and_fixed_length():
    digest = chain_hash(**_entry())
    assert len(digest) == 64
    int(digest, 16)  # must not raise


def test_changing_the_action_changes_the_hash():
    assert chain_hash(**_entry()) != chain_hash(**_entry(action="delete"))


def test_changing_the_actor_changes_the_hash():
    assert chain_hash(**_entry()) != chain_hash(**_entry(user_id=4))


def test_changing_the_entity_changes_the_hash():
    assert chain_hash(**_entry()) != chain_hash(**_entry(entity_id=8))


def test_changing_the_timestamp_changes_the_hash():
    assert chain_hash(**_entry()) != chain_hash(**_entry(timestamp=datetime(2026, 8, 7, 12, 0, 1)))


def test_changing_the_payload_changes_the_hash():
    assert chain_hash(**_entry()) != chain_hash(**_entry(changes={"name": "something else"}))


def test_changing_the_predecessor_changes_the_hash():
    # This is what makes it a chain rather than a set of independent digests.
    # (_entry defaults prev_hash to ROOT_HASH, so contrast with a real one.)
    assert chain_hash(**_entry()) != chain_hash(**_entry(prev_hash="a" * 64))


def test_changes_key_order_does_not_affect_the_hash():
    # JSON column round-trips do not preserve key order; a chain that broke on
    # re-serialisation would report tampering on every honest read.
    a = chain_hash(**_entry(changes={"a": 1, "b": 2}))
    b = chain_hash(**_entry(changes={"b": 2, "a": 1}))
    assert a == b


def test_a_null_payload_and_an_empty_payload_hash_the_same():
    # The ORM writes {} where callers pass None; both mean "nothing recorded".
    assert chain_hash(**_entry(changes=None)) == chain_hash(**_entry(changes={}))


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------

def _linked(count: int):
    """A well-formed chain of `count` rows."""
    rows = []
    prev = ROOT_HASH
    for i in range(count):
        entry = _entry(entity_id=i, prev_hash=prev)
        entry["row_hash"] = chain_hash(**entry)
        prev = entry["row_hash"]
        rows.append(entry)
    return rows


def test_an_intact_chain_verifies():
    ok, broken_at = verify_chain(_linked(5))
    assert ok is True
    assert broken_at is None


def test_an_empty_chain_verifies():
    assert verify_chain([]) == (True, None)


def test_a_single_row_chain_verifies():
    assert verify_chain(_linked(1))[0] is True


def test_editing_a_row_is_detected():
    rows = _linked(5)
    rows[2]["action"] = "delete"          # someone rewrote history
    ok, broken_at = verify_chain(rows)
    assert ok is False
    assert broken_at == 2


def test_deleting_a_row_is_detected():
    rows = _linked(5)
    del rows[2]
    ok, broken_at = verify_chain(rows)
    assert ok is False
    assert broken_at == 2


def test_reordering_rows_is_detected():
    rows = _linked(5)
    rows[1], rows[3] = rows[3], rows[1]
    assert verify_chain(rows)[0] is False


def test_a_forged_row_hash_is_detected():
    # Recomputing the hash of an edited row is not enough: the NEXT row still
    # commits to the original, so the break just moves along by one.
    rows = _linked(5)
    rows[2]["action"] = "delete"
    rows[2]["row_hash"] = chain_hash(**{k: v for k, v in rows[2].items() if k != "row_hash"})
    ok, broken_at = verify_chain(rows)
    assert ok is False
    assert broken_at == 3


def test_appending_a_correctly_linked_row_still_verifies():
    # Normal operation must not look like tampering.
    rows = _linked(3)
    nxt = _entry(entity_id=99, prev_hash=rows[-1]["row_hash"])
    nxt["row_hash"] = chain_hash(**nxt)
    assert verify_chain(rows + [nxt])[0] is True


def test_a_chain_not_starting_at_the_root_is_detected():
    rows = _linked(3)
    rows[0]["prev_hash"] = "f" * 64
    rows[0]["row_hash"] = chain_hash(**{k: v for k, v in rows[0].items() if k != "row_hash"})
    assert verify_chain(rows)[0] is False


def test_rows_without_hashes_are_treated_as_unverifiable_not_valid():
    # Rows written before chaining existed must not be reported as verified.
    legacy = [dict(_entry(), row_hash=None, prev_hash=None)]
    assert verify_chain(legacy)[0] is False

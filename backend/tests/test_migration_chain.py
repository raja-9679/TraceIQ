"""Shape of the Alembic chains — no database needed.

scripts/bootstrap_db.py relies on one structural fact: the LIVE chain's root
(`app/alembic/versions/`) has the same revision id as the LEGACY chain's head
(`app/alembic/versions_legacy/`). A database that finished the legacy chain is
therefore already at the live root, and one stamped inside legacy history can be
bridged by running the legacy chain to its head. Break that fact and every
pre-squash deployment stops upgrading.
"""
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND = Path(__file__).resolve().parents[1]
LIVE_ROOT = "e0f1a2b3c4d5"
LEGACY_STUB_ROOT = "1f266105057e"


def _script(legacy: bool = False) -> ScriptDirectory:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "app" / "alembic"))
    if legacy:
        cfg.set_main_option("version_locations",
                            str(BACKEND / "app" / "alembic" / "versions_legacy"))
    return ScriptDirectory.from_config(cfg)


@pytest.fixture(scope="module")
def live():
    return _script()


@pytest.fixture(scope="module")
def legacy():
    return _script(legacy=True)


def test_live_chain_is_linear_with_a_real_root(live):
    assert list(live.get_bases()) == [LIVE_ROOT]
    assert len(live.get_heads()) == 1
    root = live.get_revision(LIVE_ROOT)
    assert root.down_revision is None
    # The root must build the schema, not be another `pass` stub.
    source = Path(root.path).read_text()
    assert source.count("op.create_table(") > 40
    assert "traceiq_auditlog_append_only" in source


def test_legacy_head_is_the_live_root(live, legacy):
    assert list(legacy.get_heads()) == [LIVE_ROOT]
    assert list(legacy.get_bases()) == [LEGACY_STUB_ROOT]
    # Same id, two files: the legacy one *continues* d9e0f1a2b3c4, the live one
    # is a root. That is the bridge, and it must stay exactly so.
    assert legacy.get_revision(LIVE_ROOT).down_revision == "d9e0f1a2b3c4"
    assert live.get_revision(LIVE_ROOT).down_revision is None


def test_chains_share_only_the_root(live, legacy):
    live_ids = {r.revision for r in live.walk_revisions()}
    legacy_ids = {r.revision for r in legacy.walk_revisions()}
    assert live_ids & legacy_ids == {LIVE_ROOT}
    assert len(legacy_ids) == 49


def test_legacy_directory_is_off_the_default_locations(live):
    # If someone adds versions_legacy to version_locations, Alembic sees two
    # files for e0f1a2b3c4d5 and every command fails; keep it out.
    for rev in live.walk_revisions():
        assert "versions_legacy" not in rev.path

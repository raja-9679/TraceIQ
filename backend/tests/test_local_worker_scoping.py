"""Authorization predicates for the local-worker job bridge.

`GET /api/jobs/poll` hands a worker the *decrypted* project secrets baked into
the job payload. Two things must therefore be true before a job is released:

1. a project-scoped API key may only receive jobs for that one project — the
   `ApiKey.project_id` column existed but was never consulted here;
2. the caller must hold at least the editor role, because receiving secrets and
   executing tests is not a read operation.

These are pure functions so they can be tested without Postgres or Redis.
"""
from app.api.jobs import api_key_allows_project
from app.models import ApiKey


def _key(project_id=None) -> ApiKey:
    return ApiKey(
        workspace_id=7,
        project_id=project_id,
        name="ci",
        prefix="tiq_abcd",
        hashed_key="x",
        created_by_id=1,
    )


def test_workspace_scoped_key_may_receive_any_project_in_its_workspace():
    assert api_key_allows_project(_key(project_id=None), 42) is True


def test_project_scoped_key_may_receive_its_own_project():
    assert api_key_allows_project(_key(project_id=42), 42) is True


def test_project_scoped_key_is_refused_another_project():
    assert api_key_allows_project(_key(project_id=42), 43) is False


def test_project_scoped_key_is_refused_a_job_with_no_project():
    # A run with project_id NULL must not slip past a narrowed key.
    assert api_key_allows_project(_key(project_id=42), None) is False


def test_workspace_scoped_key_is_still_refused_a_job_with_no_project():
    # Unscoped runs have no workspace to check against, so they are never
    # releasable over the local bridge.
    assert api_key_allows_project(_key(project_id=None), None) is False

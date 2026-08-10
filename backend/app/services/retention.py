"""How long data is kept — workstream G2.

`Project.data_policy.retention_days` (workstream B) and
`Plan.limits.retention_days` existed as scaffolding nothing read. The only number
`purge_old_runs` looked at was the global `RUN_RETENTION_DAYS`, which is disabled
by default — so a project whose data-policy screen said "keep runs 30 days" kept
them forever, and the screen was lying to the buyer reading it.

The selection rule is deliberately conservative:

* A project's own retention wins over the global default.
* But the **shorter** window always wins. An instance-wide setting is a ceiling
  an operator imposes, not a suggestion a project can override upward.
* 0 / absent / unparseable means "keep forever *at that level*", never "delete
  now". Inverting that would wipe a customer's history on the first scheduled
  run, so `tests/test_retention_policy.py` pins every branch.
"""
from __future__ import annotations

from typing import Any, Optional


def _clean(value: Any) -> int:
    """Days as a non-negative int; anything unusable becomes 0 ("unset").

    A negative window would put the cutoff in the *future* and purge the whole
    table, so it is treated as unset rather than honoured.
    """
    try:
        days = int(value)
    except (TypeError, ValueError):
        return 0
    return days if days > 0 else 0


def effective_retention_days(*, project_days: Any, global_days: Any) -> Optional[int]:
    """The retention window to apply, or None to keep forever."""
    project = _clean(project_days)
    instance = _clean(global_days)
    candidates = [d for d in (project, instance) if d > 0]
    if not candidates:
        return None
    return min(candidates)


def project_retention_days(project: Any) -> Optional[int]:
    """The window for one project, combining its data policy with the global.

    Takes the Project row (not an id) so the retention task can resolve a whole
    batch of runs without a query per run.
    """
    from app.services.data_policy import resolve_for_project
    from app.services.instance_settings import effective

    policy = resolve_for_project(project) if project is not None else {}
    return effective_retention_days(
        project_days=(policy or {}).get("retention_days"),
        global_days=effective("RUN_RETENTION_DAYS"))

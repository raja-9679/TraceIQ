"""Extra demo datasets layered on the core demo history (user-guide gaps).

`app/services/demo_workspace.py` writes the projects, suites, cases and ~30
days of runs. The modules here add what the rest of the product's pages read —
members and keys, proposals, release sign-offs, incidents, security registers,
frontend-quality history and so on — so a fresh demo shows real-looking pages
instead of empty states.

The rules of the core demo apply unchanged:

* nothing is dispatched, delivered or fetched — rows only;
* nothing is dated after `ctx.now` (use `ctx.ago(...)`, never a time of day
  on "today", which can still be ahead of the clock);
* no real-looking secret: keys are revoked/expired, hosts are example.com /
  example.invalid / the hosts the core demo already uses;
* every row must be removed by `DELETE /demo-workspace` (the ordinary
  workspace purge, plus the demo's own member users) — the integration tests
  count every table before and after.

Each module exposes `async def seed(ctx: DemoContext) -> None` and draws its
randomness from its own `ctx.rng_for(<name>)`, so adding a dataset to one area
never reshuffles another.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple


@dataclass
class DemoContext:
    session: Any
    user: Any                                   # the demo's owner (workspace admin)
    ws: Any                                     # the demo Workspace row
    now: datetime                               # naive UTC; nothing may be dated after it
    seed: int
    plan: Dict[str, Any]                        # build_demo_plan output
    projects: Dict[str, Any]                    # "web" | "api" -> Project
    suites: Dict[str, Any]                      # "web_smoke" | "api_contract" -> TestSuite
    cases: Dict[str, Dict[str, Any]]            # project key -> case key -> TestCase
    envs: Dict[str, Any]                        # project key -> default ProjectEnvironment
    run_rows: List[Tuple[Any, Dict[str, Any]]]  # (TestRun, plan run) for suite runs
    mon_runs: List[Tuple[Any, Dict[str, Any]]]  # (TestRun, plan check) for the monitor
    result_rows: List[Tuple[Any, Any, Dict[str, Any]]]  # (TestCaseResult, TestRun, plan result)
    clusters: Dict[str, Any]                    # plan cluster key -> FailureCluster
    schedule: Any                               # the paused monitor TestSchedule
    deployments: List[Any]                      # Deployment rows (oldest first)
    flake_records: List[Any]
    members: Dict[str, Any] = field(default_factory=dict)   # filled by the setup module
    extra: Dict[str, Any] = field(default_factory=dict)     # cross-module hand-offs

    def ago(self, **kw: Any) -> datetime:
        """`now` minus a timedelta — the only way seeded rows get a time."""
        return self.now - timedelta(**kw)

    def past(self, at: datetime) -> datetime:
        """Clamp a derived timestamp so it is never after `now`."""
        return min(at, self.now - timedelta(minutes=1))

    def rng_for(self, name: str) -> random.Random:
        return random.Random(f"{self.seed}:{name}")

    def runs_of(self, project: str) -> List[Any]:
        """The project's suite runs that are not after `now`, oldest first."""
        return [run for run, r in self.run_rows
                if r["project"] == project and run.created_at <= self.now]


#: Run order. Order matters only where a module reads what an earlier one
#: made: setup makes the members that security's access reviews snapshot.
#: Tests narrow this (monkeypatch) to exercise one area on its own.
MODULES = ("setup", "releases_reports", "proposals_ai", "tests_quality", "security")


async def seed_all(ctx: DemoContext) -> None:
    import importlib
    for name in MODULES:
        module = importlib.import_module(f"app.services.demo_seed.{name}")
        await module.seed(ctx)
        await ctx.session.flush()

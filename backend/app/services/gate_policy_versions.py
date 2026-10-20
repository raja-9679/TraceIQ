"""Versioned quality-gate policies (WP15f L4).

A gate verdict is only reproducible if you know the policy that produced it.
The project row holds the CURRENT policy; this module keeps every policy that
was ever in force as an immutable, numbered `GatePolicyVersion`:

* `ensure_current` returns the version matching the policy a gate would use
  now, writing a new version when it differs from the newest stored one
  (identity = SHA-256 of the canonical JSON). PUT .../quality-gate/policy
  calls it in its own transaction; an evaluation calls it too, which covers
  projects that never saved a policy and built-in defaults changing in a new
  release — either way the verdict names a version that really existed.
* `policy_of` turns a stored version back into a QualityGatePolicy for a
  re-evaluation (GET .../quality-gate?policy_version=N).

Concurrent writers can race for the same version number; the unique
(project_id, version) constraint decides and the loser re-reads, inside a
SAVEPOINT so the caller's transaction survives.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.models import DEFAULT_QUALITY_GATE, GatePolicyVersion, Project, QualityGatePolicy


def canonical(policy: Dict[str, Any]) -> str:
    return json.dumps(policy, sort_keys=True, separators=(",", ":"), default=str)


def sha256_of(policy: Dict[str, Any]) -> str:
    return hashlib.sha256(canonical(policy).encode("utf-8")).hexdigest()


def effective_policy(project: Project) -> QualityGatePolicy:
    """Project policy merged over built-in defaults (what a gate uses now)."""
    if project.quality_gate_policy:
        return QualityGatePolicy(**{**DEFAULT_QUALITY_GATE.model_dump(), **project.quality_gate_policy})
    return QualityGatePolicy()


def policy_of(version: GatePolicyVersion) -> QualityGatePolicy:
    # Merged over today's defaults only for fields added AFTER the version
    # was written; every field it stored is used exactly as stored.
    return QualityGatePolicy(**{**DEFAULT_QUALITY_GATE.model_dump(), **(version.policy or {})})


async def latest(session, project_id: int) -> Optional[GatePolicyVersion]:
    return (await session.exec(
        select(GatePolicyVersion).where(GatePolicyVersion.project_id == project_id)
        .order_by(GatePolicyVersion.version.desc()).limit(1))).first()


async def get_version(session, project_id: int, version: int) -> Optional[GatePolicyVersion]:
    return (await session.exec(select(GatePolicyVersion).where(
        GatePolicyVersion.project_id == project_id, GatePolicyVersion.version == version))).first()


async def list_versions(session, project_id: int, limit: int = 100) -> List[GatePolicyVersion]:
    return list((await session.exec(
        select(GatePolicyVersion).where(GatePolicyVersion.project_id == project_id)
        .order_by(GatePolicyVersion.version.desc()).limit(limit))).all())


async def ensure_current(session, project: Project, policy: Optional[QualityGatePolicy] = None, *,
                         created_by_id: Optional[int] = None, commit: bool = True) -> GatePolicyVersion:
    """The stored version for `policy` (default: the project's effective one).

    Inserts version N+1 when the newest stored version differs. With
    commit=True (read paths) a new row is committed at once; the PUT path
    passes commit=False so the version commits with the policy change.
    """
    data = (policy or effective_policy(project)).model_dump()
    digest = sha256_of(data)
    for _ in range(3):
        newest = await latest(session, project.id)
        if newest is not None and newest.sha256 == digest:
            return newest
        row = GatePolicyVersion(project_id=project.id, version=(newest.version + 1) if newest else 1,
                                policy=data, sha256=digest, created_by_id=created_by_id)
        try:
            async with session.begin_nested():
                session.add(row)
            if commit:
                await session.commit()
            return row
        except IntegrityError:
            # Someone else took this number; look again.
            continue
    raise RuntimeError(f"could not record a gate policy version for project {project.id}")


def to_read(row: GatePolicyVersion, include_policy: bool = False) -> Dict[str, Any]:
    out = {"version": row.version, "sha256": row.sha256,
           "created_at": row.created_at.isoformat() if row.created_at else None,
           "created_by_id": row.created_by_id}
    if include_policy:
        out["policy"] = row.policy
    return out

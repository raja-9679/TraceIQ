"""Right-to-erasure for a user account — workstream G3.

`DELETE /api/auth/me` scrubbed the `users` row (email, name, password) and
revoked refresh tokens. Everything else the person left behind stayed:
credentials they minted, their MFA secrets, their notification preferences,
their pending account tokens. "We deleted your account" was true of one row.

Three categories, and the distinction is the whole design:

**Erased** — data whose only subject is that person: account tokens, MFA
recovery codes and secret, notification/user settings, refresh tokens, and the
API keys they created (a credential tied to a human who no longer exists is a
liability regardless of erasure law).

**Retained, de-identified** — authorship. `TestCase.created_by_id`,
`TestRun.user_id`, `TestSuite.updated_by_id` and friends are business records
belonging to the *workspace*, not to the individual. They keep pointing at the
scrubbed row, which no longer names anyone: the id survives, the identity does
not. Deleting them would destroy a customer's test history because an employee
left, and nulling them would break "who last edited this".

**Retained deliberately** — the audit trail. `auditlog` is append-only by
database trigger, has no foreign keys, and its `actor_label` holds the email as
it was at the time. It cannot be rewritten, and that is the point of an audit
trail: it is kept under the legal-obligation basis (SOC 2 CC7, PCI DSS Req 10)
and bounded by `AUDIT_RETENTION_DAYS`, not by an erasure request.

`erase_user` returns a report of all three, because what a data-protection
officer actually needs is a defensible statement of scope — not a promise that
nothing survived anywhere.
"""
from __future__ import annotations

import logging
import secrets as pysecrets
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List

logger = logging.getLogger(__name__)

# Tables holding data whose only subject is the erased user. Deleted outright.
PERSONAL_TABLES: Dict[str, str] = {
    "accounttoken": "user_id",
    "mfarecoverycode": "user_id",
    "user_settings": "user_id",
    "refreshtoken": "user_id",
}

# Authorship columns. Left pointing at the scrubbed row: these are the
# workspace's business records, and the row they point at no longer identifies
# anybody. Listed so the report can state the position rather than imply the
# data is gone.
RETAINED_AUTHORSHIP: List[str] = [
    "testcase.created_by_id", "testcase.updated_by_id",
    "testcase.last_human_reviewed_by_id", "testsuite.created_by_id",
    "testsuite.updated_by_id", "testrun.user_id", "testcaserevision.changed_by_id",
    "caseproposal.created_by_id", "caseproposal.decided_by_id",
    "selectorhealproposal.decided_by_id", "visualbaseline.created_by_id",
    "mobileappbuild.created_by_id", "securityscan.requested_by_id",
    "persona.created_by_id", "requirementlink.created_by_id",
    "reportschedule.created_by_id", "testschedule.created_by_id",
    "testschedule.updated_by_id", "issueticket.created_by_id",
    "issuetrackerconfig.created_by_id", "workspacewebhook.created_by_id",
    "failurecluster.assignee_id", "securityfinding.assignee_id",
    "teaminvitation.invited_by_id", "workspaceinvitation.invited_by_id",
    "instance_settings.updated_by_id", "llm_provider_config.updated_by_id",
]

RETAINED_WITH_REASON: Dict[str, str] = {
    "auditlog": "append-only by database trigger and cannot be rewritten; kept "
                "under the legal-obligation basis (SOC 2 CC7, PCI DSS Req 10) "
                "and bounded by AUDIT_RETENTION_DAYS, not by an erasure request. "
                "actor_label holds the email as it was at the time.",
    "tenant.owner_id": "a tenant must have an owner; erasing the account does not "
                       "dissolve the organisation. Transfer ownership first if the "
                       "tenant is to outlive the person.",
}


@dataclass
class ErasureReport:
    user_id: int
    erased: Dict[str, int] = field(default_factory=dict)
    api_keys_revoked: int = 0
    retained_authorship: List[str] = field(default_factory=lambda: list(RETAINED_AUTHORSHIP))
    retained_with_reason: Dict[str, str] = field(
        default_factory=lambda: dict(RETAINED_WITH_REASON))

    def as_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "erased": self.erased,
            "api_keys_revoked": self.api_keys_revoked,
            "retained_authorship_columns": self.retained_authorship,
            "retained_with_reason": self.retained_with_reason,
        }


async def erase_user(session, user, *, request=None) -> ErasureReport:
    """Scrub the account and delete everything personal to it. Caller commits.

    Soft delete by design: the row is referenced as owner/creator across
    tenants, suites, cases and runs, so a hard delete would either cascade into
    a customer's test history or leave dangling references. What matters for
    erasure is that the row no longer identifies anyone.
    """
    from sqlalchemy import text

    from app.core.auth import get_password_hash
    from app.models import ApiKey

    report = ErasureReport(user_id=user.id)
    now = datetime.utcnow()

    # Personal rows first: an audit entry is appended below and must not be
    # deleted by a sweep in this same call.
    for table, column in PERSONAL_TABLES.items():
        result = await session.exec(
            text(f"DELETE FROM {table} WHERE {column} = :user_id"),
            params={"user_id": user.id})
        count = getattr(result, "rowcount", 0) or 0
        if count:
            report.erased[table] = count

    # API keys the person minted. A live credential belonging to a deleted human
    # is a standing liability — nobody is left to rotate it. Revoked rather than
    # deleted so audit rows referencing the key prefix stay meaningful.
    keys = (await session.exec(
        text("SELECT id FROM apikey WHERE created_by_id = :user_id "
             "AND revoked_at IS NULL"),
        params={"user_id": user.id})).all()
    for (key_id,) in keys:
        api_key = await session.get(ApiKey, key_id)
        if api_key is not None:
            api_key.revoked_at = now
            session.add(api_key)
            report.api_keys_revoked += 1

    # Scrub the row itself. The placeholder email stays unique per user so the
    # unique index holds and nothing collides on re-erasure.
    user.email = f"deleted+{user.id}@deleted.traceiq.local"
    user.full_name = "Deleted User"
    user.hashed_password = get_password_hash(pysecrets.token_urlsafe(32))
    user.is_active = False
    user.mfa_enabled = False
    user.mfa_secret = None
    user.scim_external_id = None
    session.add(user)

    from app.services.audit import record as audit_record
    await audit_record(
        session,
        entity_type="user", entity_id=user.id, action="erase",
        user_id=user.id, request=request,
        # Deliberately no email here: this row is permanent, and writing the
        # address into the record of its own erasure would defeat the exercise.
        changes={"erased": report.erased,
                 "api_keys_revoked": report.api_keys_revoked,
                 "note": "authorship references retained de-identified; "
                         "audit history retained under legal obligation"},
    )
    logger.info("[erasure] user %s scrubbed: %s, %d api key(s) revoked",
                user.id, report.erased, report.api_keys_revoked)
    return report

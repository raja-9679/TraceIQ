"""Accessibility register (WP15d, H6).

The worker's `check-accessibility` step runs axe-core and ships one entry per
step as the job result's `accessibility_results` (like `page_budgets`). This
module turns those into rows, decides what is new, fixed and waived, and
evaluates the opt-in gate check.

Rules worth knowing before changing anything here:

* **Never page text.** A row holds the rule id, impact, a redacted target
  selector, node count and the rule's help URL. The list is rebuilt from a
  whitelist (`redaction.redact_accessibility_results`) — axe's `html` and
  `failureSummary` quote the page and are dropped even if a worker sends them.
* **Evidence vs. violations.** One AccessibilityCheck row per scanned step,
  clean or not. "No accessibility evidence" (the gate's missing data) means
  no check rows, never "no violation rows".
* **Replace per (run, test_name)** on ingestion, so a redelivered stream
  message or a re-processed result is counted once.
* **New** = a fingerprint (rule + page + target) not present in the PREVIOUS
  COMPARABLE run of the same case: the latest earlier run with a check for the
  same case (test_case_id, else test name) on the same browser and device. A
  case with no earlier comparable run has no baseline, so every violation is
  new — the gate fails closed; known issues are what waivers are for.
* **Waivers mirror flake quarantine** (app/services/flake_policy.py): every
  waiver expires (1-90 days); past expiry it excludes nothing and the
  violation counts again; it is never renewed except by an explicit (audited)
  PUT. The selector pattern is a `*` glob matched by a hand-written linear
  matcher — a pattern is author-supplied and the target comes from the site
  under test, so no regex engine sees either.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.services.page_budgets import page_key as _page_key
from app.services.redaction import redact_accessibility_results

IMPACTS: Tuple[str, ...] = ("minor", "moderate", "serious", "critical")
IMPACT_RANK = {name: i for i, name in enumerate(IMPACTS)}
GATE_MIN_IMPACT = "serious"

DEFAULT_WAIVER_DAYS = 30
MAX_WAIVER_DAYS = 90

MAX_ROWS_PER_CHECK = 500
MORE_TARGET = "(more elements)"

_RULE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")


# --------------------------------------------------------------------------
# Matching and waivers
# --------------------------------------------------------------------------

def glob_match(pattern: str, text: str) -> bool:
    """`*` matches any run of characters, everything else is literal. The
    classic two-pointer wildcard walk: linear in practice, no backtracking
    blow-up whatever the pattern or the text."""
    p = t = 0
    star = -1
    mark = 0
    while t < len(text):
        if p < len(pattern) and pattern[p] != "*" and pattern[p] == text[t]:
            p += 1
            t += 1
        elif p < len(pattern) and pattern[p] == "*":
            star = p
            mark = t
            p += 1
        elif star != -1:
            p = star + 1
            mark += 1
            t = mark
        else:
            return False
    while p < len(pattern) and pattern[p] == "*":
        p += 1
    return p == len(pattern)


def waiver_state(waiver, now: Optional[datetime] = None) -> str:
    """'active' | 'expired'."""
    now = now or datetime.utcnow()
    return "active" if waiver.expires_at > now else "expired"


def is_active(waiver, now: Optional[datetime] = None) -> bool:
    return waiver is not None and waiver_state(waiver, now) == "active"


def waiver_covers(waiver, rule_id: str, target: str, now: Optional[datetime] = None) -> bool:
    return (is_active(waiver, now) and waiver.rule_id == rule_id
            and glob_match(waiver.selector_pattern or "*", target or ""))


def covering_waiver(waivers: Sequence, rule_id: str, target: str,
                    now: Optional[datetime] = None):
    for w in waivers:
        if waiver_covers(w, rule_id, target, now):
            return w
    return None


def set_expiry(waiver, days: int, now: Optional[datetime] = None) -> None:
    """Start (or explicitly renew) a waiver for `days` from now."""
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_WAIVER_DAYS:
        raise ValueError(f"waiver length must be 1-{MAX_WAIVER_DAYS} days")
    now = now or datetime.utcnow()
    waiver.expires_at = now + timedelta(days=days)
    waiver.updated_at = now


def validate_rule_id(rule_id: str) -> str:
    rule_id = (rule_id or "").strip().lower()
    if not _RULE_RE.match(rule_id):
        raise ValueError("rule_id must be an axe rule id such as 'color-contrast'")
    return rule_id


def validate_pattern(pattern: Optional[str]) -> str:
    pattern = (pattern or "*").strip() or "*"
    if len(pattern) > 300:
        raise ValueError("selector pattern is limited to 300 characters")
    return pattern


# --------------------------------------------------------------------------
# Ingestion
# --------------------------------------------------------------------------

def fingerprint(rule_id: str, page_key: Optional[str], target: str) -> str:
    raw = f"{rule_id}|{page_key or ''}|{target or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def normalise_results(raw: Any) -> List[Dict[str, Any]]:
    """Worker `accessibility_results` → checks with their violation rows.
    Redacted again here whatever the worker did (a local worker is any API
    key holder; an old worker image may not redact at all)."""
    checks: List[Dict[str, Any]] = []
    for item in redact_accessibility_results(raw if isinstance(raw, list) else []) or []:
        url = item.get("url")
        pkey = _page_key(url) if url else None
        rows: Dict[str, Dict[str, Any]] = {}
        for v in item.get("violations") or []:
            rule = (v.get("id") or "").strip().lower()
            if not _RULE_RE.match(rule):
                continue
            impact = v.get("impact") if v.get("impact") in IMPACT_RANK else "minor"
            targets = list(v.get("targets") or [])
            listed = sum(t["nodes"] for t in targets)
            if v.get("node_count", 0) > listed:
                # The worker caps targets per rule; the rest stays countable.
                targets.append({"selector": MORE_TARGET, "nodes": v["node_count"] - listed})
            if not targets:
                targets = [{"selector": "", "nodes": max(1, v.get("node_count") or 0)}]
            for t in targets:
                fp = fingerprint(rule, pkey, t["selector"])
                if fp in rows:
                    rows[fp]["node_count"] += t["nodes"]
                    continue
                if len(rows) >= MAX_ROWS_PER_CHECK:
                    break
                rows[fp] = {"rule_id": rule, "impact": impact, "target": t["selector"],
                            "help_url": v.get("help_url"), "node_count": t["nodes"],
                            "fingerprint": fp}
        checks.append({"step_id": item.get("step_id"), "page_key": pkey,
                       "violations": list(rows.values())})
    return checks


def persist_results(session, run, test_case_id: Optional[int], test_name: Optional[str],
                    raw: Any) -> int:
    """Replace this (run, test) pair's accessibility rows with `raw`. Sync
    session (the aggregator). Returns the number of violation rows."""
    from sqlmodel import delete
    from app.models import AccessibilityCheck, AccessibilityViolation

    if not run or not run.project_id:
        return 0
    checks = normalise_results(raw)
    name_clause = (AccessibilityCheck.test_name == test_name) if test_name is not None \
        else AccessibilityCheck.test_name.is_(None)
    vname_clause = (AccessibilityViolation.test_name == test_name) if test_name is not None \
        else AccessibilityViolation.test_name.is_(None)
    session.exec(delete(AccessibilityViolation).where(
        AccessibilityViolation.test_run_id == run.id, vname_clause))
    session.exec(delete(AccessibilityCheck).where(
        AccessibilityCheck.test_run_id == run.id, name_clause))
    case_id = int(test_case_id) if test_case_id else None
    total = 0
    for c in checks:
        session.add(AccessibilityCheck(
            project_id=run.project_id, test_run_id=run.id, test_case_id=case_id,
            test_name=test_name, step_id=c["step_id"], page_key=c["page_key"],
            browser=run.browser or "chromium", device=run.device,
            violation_count=len(c["violations"])))
        for v in c["violations"]:
            session.add(AccessibilityViolation(
                project_id=run.project_id, test_run_id=run.id, test_case_id=case_id,
                test_name=test_name, step_id=c["step_id"], page_key=c["page_key"], **v))
            total += 1
    return total


# --------------------------------------------------------------------------
# Comparison (pure — the endpoints and the gate feed it rows)
# --------------------------------------------------------------------------

def comparable_key(test_case_id: Optional[int], test_name: Optional[str],
                   browser: Optional[str], device: Optional[str]) -> Tuple:
    """Which runs of a case compare with each other."""
    who = ("case", test_case_id) if test_case_id else ("name", test_name or "")
    return who + (browser or "chromium", device or "")


def at_or_above(impact: str, minimum: str = GATE_MIN_IMPACT) -> bool:
    return IMPACT_RANK.get(impact, 0) >= IMPACT_RANK[minimum]


def new_fingerprints(current: Iterable[str], previous: Optional[Iterable[str]]) -> set:
    """No previous comparable run → no baseline → everything is new."""
    cur = set(current)
    return cur if previous is None else cur - set(previous)


# --------------------------------------------------------------------------
# Queries (async session — the API and the gate)
# --------------------------------------------------------------------------

async def _waivers(session, project_id: int):
    from sqlmodel import select
    from app.models import AccessibilityWaiver
    return list((await session.exec(select(AccessibilityWaiver).where(
        AccessibilityWaiver.project_id == project_id))).all())


async def _previous_run_id(session, project_id: int, key: Tuple, before: datetime,
                           exclude: Iterable[int] = ()) -> Optional[int]:
    """Latest run before `before` with a check for this comparable key."""
    from sqlmodel import select
    from app.models import AccessibilityCheck, TestRun

    kind, who, browser, device = key
    stmt = (select(AccessibilityCheck.test_run_id)
            .join(TestRun, TestRun.id == AccessibilityCheck.test_run_id)
            .where(AccessibilityCheck.project_id == project_id,
                   AccessibilityCheck.browser == browser,
                   TestRun.created_at < before))
    stmt = stmt.where(AccessibilityCheck.test_case_id == who) if kind == "case" \
        else stmt.where(AccessibilityCheck.test_case_id.is_(None),
                        AccessibilityCheck.test_name == who)
    stmt = stmt.where(AccessibilityCheck.device == device) if device \
        else stmt.where(AccessibilityCheck.device.is_(None))
    excluded = list(exclude)
    if excluded:
        stmt = stmt.where(AccessibilityCheck.test_run_id.notin_(excluded))
    return (await session.exec(stmt.order_by(TestRun.created_at.desc()).limit(1))).first()


async def _fingerprints_for(session, run_id: int, key: Tuple) -> Dict[str, Any]:
    from sqlmodel import select
    from app.models import AccessibilityViolation

    kind, who, _browser, _device = key
    stmt = select(AccessibilityViolation).where(AccessibilityViolation.test_run_id == run_id)
    stmt = stmt.where(AccessibilityViolation.test_case_id == who) if kind == "case" \
        else stmt.where(AccessibilityViolation.test_case_id.is_(None),
                        AccessibilityViolation.test_name == who)
    return {v.fingerprint: v for v in (await session.exec(stmt)).all()}


async def annotated_run(session, project_id: int, run_id: int,
                        now: Optional[datetime] = None) -> Dict[str, Any]:
    """Every violation of one run with `new` and `waived` flags, plus which
    previous run each case was compared with. The caller has checked access
    and that the run is in `project_id`."""
    from sqlmodel import select
    from app.models import AccessibilityCheck, TestRun

    now = now or datetime.utcnow()
    run = await session.get(TestRun, run_id)
    if run is None or run.project_id != project_id:
        return {"run_id": run_id, "checks": 0, "violations": [], "compared_with": {}}
    checks = (await session.exec(select(AccessibilityCheck).where(
        AccessibilityCheck.test_run_id == run_id))).all()
    waivers = await _waivers(session, project_id)
    keys = {comparable_key(c.test_case_id, c.test_name, c.browser, c.device) for c in checks}
    items: List[Dict[str, Any]] = []
    compared: Dict[str, Optional[int]] = {}
    for key in sorted(keys, key=str):
        current = await _fingerprints_for(session, run_id, key)
        prev_id = await _previous_run_id(session, project_id, key, run.created_at, [run_id])
        prev = await _fingerprints_for(session, prev_id, key) if prev_id else None
        fresh = new_fingerprints(current, prev)
        compared[f"{key[0]}:{key[1]}"] = prev_id
        for fp, v in current.items():
            w = covering_waiver(waivers, v.rule_id, v.target, now)
            items.append({**_violation_dict(v), "new": fp in fresh,
                          "waived": w is not None, "waiver_id": w.id if w else None})
    items.sort(key=lambda i: (-IMPACT_RANK.get(i["impact"], 0), i["rule_id"], i["target"]))
    return {"run_id": run_id, "checks": len(checks), "violations": items,
            "compared_with": compared}


def _violation_dict(v) -> Dict[str, Any]:
    return {"id": v.id, "test_run_id": v.test_run_id, "test_case_id": v.test_case_id,
            "test_name": v.test_name, "step_id": v.step_id, "page_key": v.page_key,
            "rule_id": v.rule_id, "impact": v.impact, "target": v.target,
            "fingerprint": v.fingerprint, "node_count": v.node_count,
            "help_url": v.help_url}


async def gate_check(session, project_id: int, run_ids: List[int],
                     now: Optional[datetime] = None):
    """"No NEW violations at/above serious" over the evaluated runs."""
    from sqlmodel import select
    from app.models import AccessibilityCheck, QualityGateCheck, TestRun

    name, threshold = "accessibility_new_violations", f"0 new at/above {GATE_MIN_IMPACT}"
    checks = (await session.exec(select(AccessibilityCheck).where(
        AccessibilityCheck.test_run_id.in_(run_ids)))).all() if run_ids else []
    if not checks:
        return QualityGateCheck(
            name=name, passed=False, actual="no evidence", threshold=threshold,
            missing_data=True,
            detail="no accessibility checks ran in the evaluated runs")
    now = now or datetime.utcnow()
    waivers = await _waivers(session, project_id)
    runs = {r.id: r for r in (await session.exec(
        select(TestRun).where(TestRun.id.in_({c.test_run_id for c in checks})))).all()}
    pairs = {(c.test_run_id, comparable_key(c.test_case_id, c.test_name, c.browser, c.device))
             for c in checks}
    blocking: List[str] = []
    waived = 0
    for run_id, key in sorted(pairs, key=str):
        run = runs.get(run_id)
        if run is None:
            continue
        current = await _fingerprints_for(session, run_id, key)
        if not current:
            continue
        prev_id = await _previous_run_id(session, project_id, key, run.created_at, [run_id])
        prev = await _fingerprints_for(session, prev_id, key) if prev_id else None
        for fp in new_fingerprints(current, prev):
            v = current[fp]
            if not at_or_above(v.impact):
                continue
            if covering_waiver(waivers, v.rule_id, v.target, now):
                waived += 1
                continue
            blocking.append(f"{v.rule_id} ({v.impact}) on {v.test_name or 'case'}")
    detail = "; ".join(sorted(set(blocking))[:10]) or None
    if waived:
        detail = (detail + "; " if detail else "") + f"{waived} waived"
    return QualityGateCheck(name=name, passed=not blocking, actual=f"{len(blocking)} new",
                            threshold=threshold, detail=detail)


async def trend(session, project_id: int, days: int = 30,
                now: Optional[datetime] = None) -> Dict[str, Any]:
    """Per-day counts by impact, new / fixed between consecutive comparable
    runs, waived counts, and the current open list (latest run per case)."""
    from sqlmodel import select
    from app.models import AccessibilityCheck, AccessibilityViolation, TestRun

    now = now or datetime.utcnow()
    since = now - timedelta(days=days)
    checks = (await session.exec(
        select(AccessibilityCheck, TestRun.created_at)
        .join(TestRun, TestRun.id == AccessibilityCheck.test_run_id)
        .where(AccessibilityCheck.project_id == project_id, TestRun.created_at >= since)
        .order_by(TestRun.created_at).limit(20000))).all()
    run_ids = sorted({c.test_run_id for c, _ in checks})
    violations: List = []
    for i in range(0, len(run_ids), 500):
        violations.extend((await session.exec(select(AccessibilityViolation).where(
            AccessibilityViolation.test_run_id.in_(run_ids[i:i + 500])))).all())
    waivers = await _waivers(session, project_id)

    run_time: Dict[int, datetime] = {}
    groups: Dict[Tuple, List[int]] = defaultdict(list)
    for c, created in checks:
        run_time[c.test_run_id] = created
        key = comparable_key(c.test_case_id, c.test_name, c.browser, c.device)
        if c.test_run_id not in groups[key]:
            groups[key].append(c.test_run_id)
    by_run_key: Dict[Tuple[int, Tuple], Dict[str, Any]] = defaultdict(dict)
    key_of_run_case: Dict[Tuple[int, Any, Any], List[Tuple]] = defaultdict(list)
    for key in groups:
        for rid in groups[key]:
            key_of_run_case[(rid, key[0], key[1])].append(key)
    for v in violations:
        who = ("case", v.test_case_id) if v.test_case_id else ("name", v.test_name or "")
        for key in key_of_run_case.get((v.test_run_id,) + who, []):
            by_run_key[(v.test_run_id, key)][v.fingerprint] = v

    days_out: Dict[str, Dict[str, int]] = {}

    def bucket(when: datetime) -> Dict[str, int]:
        d = when.date().isoformat()
        return days_out.setdefault(d, {"date": d, **{i: 0 for i in IMPACTS},
                                       "new": 0, "fixed": 0, "waived": 0})

    open_items: List[Dict[str, Any]] = []
    for key, rids in groups.items():
        prev: Optional[Dict[str, Any]] = None
        for idx, rid in enumerate(rids):
            cur = by_run_key.get((rid, key), {})
            b = bucket(run_time[rid])
            for v in cur.values():
                b[v.impact if v.impact in IMPACT_RANK else "minor"] += 1
                if covering_waiver(waivers, v.rule_id, v.target, now):
                    b["waived"] += 1
            # The first run in the window has no in-window predecessor; its
            # new/fixed would compare against nothing, so they are not counted.
            if prev is not None:
                b["new"] += len(set(cur) - set(prev))
                b["fixed"] += len(set(prev) - set(cur))
            if idx == len(rids) - 1:
                for fp, v in cur.items():
                    w = covering_waiver(waivers, v.rule_id, v.target, now)
                    open_items.append({**_violation_dict(v),
                                       "new": prev is not None and fp not in prev,
                                       "waived": w is not None,
                                       "waiver_id": w.id if w else None,
                                       "last_seen": run_time[rid].isoformat() + "Z"})
            prev = cur

    by_rule: Dict[str, Dict[str, Any]] = {}
    for item in open_items:
        r = by_rule.setdefault(item["rule_id"], {"rule_id": item["rule_id"], "impact": item["impact"],
                                                 "help_url": item["help_url"], "open": 0,
                                                 "waived": 0, "nodes": 0})
        r["waived" if item["waived"] else "open"] += 1
        r["nodes"] += item["node_count"]
        if IMPACT_RANK.get(item["impact"], 0) > IMPACT_RANK.get(r["impact"], 0):
            r["impact"] = item["impact"]
    open_items.sort(key=lambda i: (i["waived"], -IMPACT_RANK.get(i["impact"], 0),
                                   i["rule_id"], i["target"]))
    unwaived = [i for i in open_items if not i["waived"]]
    return {
        "project_id": project_id, "days": days,
        "checks": len(checks), "runs": len(run_ids),
        "series": [days_out[d] for d in sorted(days_out)],
        "open": open_items[:1000],
        "totals": {**{i: sum(1 for o in unwaived if o["impact"] == i) for i in IMPACTS},
                   "waived": sum(1 for o in open_items if o["waived"]),
                   "new": sum(1 for o in unwaived if o["new"])},
        "rules": sorted(by_rule.values(),
                        key=lambda r: (-IMPACT_RANK.get(r["impact"], 0), -r["open"], r["rule_id"])),
    }

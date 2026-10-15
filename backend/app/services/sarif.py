"""SARIF 2.1.0 export of security findings (WP7).

SARIF is what GitHub code scanning, Azure DevOps, DefectDojo and most ASPM
tools ingest, so this is how TraceIQ's DAST/passive/leak findings reach the
place an application-security team already triages.

Mapping decisions:

* **ruleId** is `<scan_type>/<category>/<slug of title>` — stable across scans
  (titles come from fixed rule text, not from the target), readable in a UI.
* **location** is the finding's URL as `artifactLocation.uri`. SARIF was built
  for files, but the spec allows any URI and web findings have no file.
* **level**: critical/high → error, medium → warning, low/info → note. The
  numeric `security-severity` property is what GitHub actually sorts by.
* **partialFingerprints** carries TraceIQ's own fingerprint, so a consumer
  tracks the same finding across uploads exactly as TraceIQ does.
* `false_positive` / `accepted_risk` become SARIF **suppressions** rather than
  being dropped — an auditor wants to see what was waived and why.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

SARIF_VERSION = "2.1.0"
SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
FINGERPRINT_KEY = "traceiqFingerprint/v1"

LEVELS = {"critical": "error", "high": "error", "medium": "warning", "low": "note", "info": "note"}
SECURITY_SEVERITY = {"critical": "9.5", "high": "8.0", "medium": "5.5", "low": "3.0", "info": "0.0"}


def level_for(severity: Optional[str]) -> str:
    return LEVELS.get((severity or "").lower(), "warning")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:60] or "finding"


def rule_id(finding: Any) -> str:
    return f"{_get(finding, 'scan_type') or 'scan'}/{_get(finding, 'category') or 'other'}/{_slug(_get(finding, 'title'))}"


def _get(obj: Any, name: str) -> Any:
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat() + ("Z" if getattr(value, "tzinfo", None) is None else "") \
        if hasattr(value, "isoformat") else str(value)


def _suppressions(finding: Any) -> Optional[List[Dict[str, Any]]]:
    status = _get(finding, "status")
    if status == "false_positive":
        return [{"kind": "external", "status": "accepted",
                 "justification": "Marked false positive in TraceIQ"}]
    if status == "accepted_risk":
        expires = _iso(_get(finding, "risk_expires_at"))
        why = _get(finding, "risk_justification") or "Risk accepted in TraceIQ"
        return [{"kind": "external", "status": "accepted",
                 "justification": f"{why} (accepted until {expires})" if expires else why}]
    return None


def build_sarif(findings: Iterable[Any], *, tool_version: str = "1.0",
                automation_id: Optional[str] = None,
                information_uri: str = "https://github.com/raja-9679/TraceIQ") -> Dict[str, Any]:
    """One SARIF log with one run holding every finding passed in."""
    rules: List[Dict[str, Any]] = []
    rule_index: Dict[str, int] = {}
    results: List[Dict[str, Any]] = []

    for f in findings:
        rid = rule_id(f)
        severity = (_get(f, "severity") or "medium").lower()
        if rid not in rule_index:
            rule_index[rid] = len(rules)
            rules.append({
                "id": rid,
                "name": _slug(_get(f, "title")).replace("-", "_") or "finding",
                "shortDescription": {"text": _get(f, "title") or rid},
                "fullDescription": {"text": (_get(f, "description") or _get(f, "title") or rid)[:2000]},
                "defaultConfiguration": {"level": level_for(severity)},
                "properties": {
                    "tags": ["security", _get(f, "category") or "other", _get(f, "scan_type") or "scan"],
                    "security-severity": SECURITY_SEVERITY.get(severity, "5.5"),
                },
            })
        message = _get(f, "title") or rid
        evidence = _get(f, "evidence")
        if evidence:
            message = f"{message} — {str(evidence)[:500]}"
        result: Dict[str, Any] = {
            "ruleId": rid,
            "ruleIndex": rule_index[rid],
            "level": level_for(severity),
            "message": {"text": message},
            "locations": [{
                "physicalLocation": {
                    "artifactLocation": {"uri": _get(f, "target_url") or "urn:traceiq:unknown-location"},
                },
            }],
            "partialFingerprints": {},
            "properties": {
                "traceiq_finding_id": _get(f, "id"),
                "severity": severity,
                "status": _get(f, "status") or "open",
                "scan_type": _get(f, "scan_type"),
                "owner_id": _get(f, "assignee_id"),
                "due_at": _iso(_get(f, "due_at")),
                "first_seen": _iso(_get(f, "created_at")),
            },
        }
        fp = _get(f, "fingerprint")
        if fp:
            result["partialFingerprints"][FINGERPRINT_KEY] = fp
        location = _get(f, "location")
        if isinstance(location, dict) and location.get("field"):
            result["locations"][0]["logicalLocations"] = [
                {"name": location["field"], "kind": "member"}]
        suppressions = _suppressions(f)
        if suppressions:
            result["suppressions"] = suppressions
        results.append(result)

    run: Dict[str, Any] = {
        "tool": {"driver": {
            "name": "TraceIQ",
            "informationUri": information_uri,
            "version": tool_version,
            "rules": rules,
        }},
        "results": results,
        "columnKind": "unicodeCodePoints",
    }
    if automation_id:
        run["automationDetails"] = {"id": automation_id}
    return {"$schema": SARIF_SCHEMA, "version": SARIF_VERSION, "runs": [run]}

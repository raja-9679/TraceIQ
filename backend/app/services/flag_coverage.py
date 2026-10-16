"""A/B + feature-flag coverage (WP12c).

A run may declare the flags / experiment variants it exercised
(`POST /runs` body `flags: {name: str | bool}`), stored on `TestRun.flags`.
The project keeps an optional catalogue (`FlagCatalogEntry`: name + possible
values). The report answers, over a window: per flag and value, how many runs
and cases exercised it, when it was last seen and how those runs went — and
which catalogue values no run has exercised.

Values are compared as strings; booleans become "true"/"false" so a catalogue
entry `["true", "false"]` matches runs that sent JSON booleans.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

MAX_FLAGS = 50
MAX_FLAG_NAME = 64
MAX_FLAG_VALUE = 200
MAX_CATALOG_VALUES = 50
# Fixed pattern (not user-supplied): what {{flag.NAME}} can address.
_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


class FlagError(ValueError):
    pass


def flag_value_str(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def validate_flag_name(name: Any) -> str:
    s = str(name or "").strip()
    if not _NAME_RE.match(s):
        raise FlagError(
            f"flag name {s[:80]!r} must be 1-{MAX_FLAG_NAME} letters, digits, '_', '.' or '-'")
    return s


def validate_flags(flags: Any) -> Optional[Dict[str, Any]]:
    """Clean a run's declared flags; None when absent/empty."""
    if flags is None:
        return None
    if not isinstance(flags, dict):
        raise FlagError("flags must be an object of name -> string or boolean")
    if len(flags) > MAX_FLAGS:
        raise FlagError(f"at most {MAX_FLAGS} flags per run")
    out: Dict[str, Any] = {}
    for k, v in flags.items():
        name = validate_flag_name(k)
        if isinstance(v, bool):
            out[name] = v
        elif isinstance(v, (str, int, float)) and not isinstance(v, bool):
            s = str(v).strip()
            if len(s) > MAX_FLAG_VALUE:
                raise FlagError(f"flag {name!r}: value longer than {MAX_FLAG_VALUE} characters")
            out[name] = s
        else:
            raise FlagError(f"flag {name!r}: value must be a string or boolean")
    return out or None


def validate_catalog_values(values: Any) -> List[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise FlagError("values must be a list")
    out: List[str] = []
    for v in values:
        if not isinstance(v, (str, bool, int, float)):
            raise FlagError("each value must be a string or boolean")
        s = flag_value_str(v).strip()
        if not s or len(s) > MAX_FLAG_VALUE:
            raise FlagError(f"values must be 1-{MAX_FLAG_VALUE} characters")
        if s not in out:
            out.append(s)
    if len(out) > MAX_CATALOG_VALUES:
        raise FlagError(f"at most {MAX_CATALOG_VALUES} values per flag")
    return out


def _status(r: Dict[str, Any]) -> str:
    s = r.get("status")
    return str(getattr(s, "value", s) or "")


def aggregate(runs: Iterable[Dict[str, Any]], catalog: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """`runs`: [{id, flags, status, created_at, case_ids}] (case_ids = cases the
    run executed); `catalog`: [{name, values, description}].

    Returns {flags: [{name, in_catalog, description, values: [{value, runs,
    cases, passed_runs, failed_runs, last_seen, last_run_id, in_catalog}],
    never_tested: [...]}], runs_with_flags, uncatalogued: [...]}."""
    stats: Dict[str, Dict[str, Dict[str, Any]]] = {}
    n_runs = 0
    for run in runs:
        flags = run.get("flags") or {}
        if not isinstance(flags, dict) or not flags:
            continue
        n_runs += 1
        created = run.get("created_at")
        status = _status(run)
        for name, raw in flags.items():
            value = flag_value_str(raw)
            st = stats.setdefault(name, {}).setdefault(value, {
                "value": value, "runs": 0, "case_set": set(), "passed_runs": 0,
                "failed_runs": 0, "last_seen": None, "last_run_id": None})
            st["runs"] += 1
            st["case_set"].update(run.get("case_ids") or [])
            if status == "passed":
                st["passed_runs"] += 1
            elif status in ("failed", "error"):
                st["failed_runs"] += 1
            if created and (st["last_seen"] is None or created > st["last_seen"]):
                st["last_seen"], st["last_run_id"] = created, run.get("id")

    cat = {c["name"]: c for c in catalog}
    out: List[Dict[str, Any]] = []
    for name in sorted(set(stats) | set(cat)):
        entry = cat.get(name)
        known = list((entry or {}).get("values") or [])
        seen = stats.get(name, {})
        values = []
        for v in sorted(seen.values(), key=lambda x: -x["runs"]):
            last = v["last_seen"]
            values.append({
                "value": v["value"], "runs": v["runs"], "cases": len(v["case_set"]),
                "passed_runs": v["passed_runs"], "failed_runs": v["failed_runs"],
                "last_seen": last.isoformat() if isinstance(last, datetime) else last,
                "last_run_id": v["last_run_id"],
                "in_catalog": v["value"] in known if entry else False,
            })
        out.append({
            "name": name,
            "in_catalog": entry is not None,
            "catalog_id": (entry or {}).get("id"),
            "description": (entry or {}).get("description"),
            "catalog_values": known,
            "values": values,
            "never_tested": [v for v in known if v not in seen],
            "runs": sum(v["runs"] for v in seen.values()),
        })
    return {
        "runs_with_flags": n_runs,
        "flags": out,
        "uncatalogued": [f["name"] for f in out if not f["in_catalog"]],
        "never_tested_total": sum(len(f["never_tested"]) for f in out),
    }

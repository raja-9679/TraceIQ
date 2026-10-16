"""Integration test packs (WP13c, decision C7).

A pack is a versioned JSON file in app/packs/ describing ready-made suites for
a common integration (SSO, SCIM, outbound webhooks): the variables a user has
to fill, and suites -> cases -> steps built ONLY from step types in the
`/api/step-types` catalogue. Importing one creates a suite in a project; it
never contacts the target (C7) — nothing is dispatched until someone runs it.

Variables map onto what steps can already reference:

* non-secret -> a key of a ProjectEnvironment's `variables` ({{env.NAME}})
* secret     -> a ProjectSecret, encrypted at rest ({{secret.NAME}}); a secret
                never has a default and its value is never echoed back.

`validate_pack` is what CI runs over every shipped pack
(tests/test_test_packs.py): an unknown step type, an undeclared variable, a
secret referenced as {{env.X}} (or the reverse), or a template inside an
assertion value (assertions are compared verbatim by the worker) fails it.
"""
from __future__ import annotations

import copy
import json
import re
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

PACK_DIR = Path(__file__).resolve().parent.parent / "packs"
PACK_FORMAT = "traceiq-test-pack"
PACK_FORMAT_VERSION = 1

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_VAR_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_REF_RE = re.compile(r"\{\{\s*(env|secret)\.([A-Za-z0-9_]+)\s*\}\}")
MAX_VALUE_LEN = 4096


class PackError(ValueError):
    pass


def catalogue_step_types() -> Set[str]:
    from app.api.agent_reference import _STEP_TYPES
    return {entry["type"] for entry in _STEP_TYPES}


def references(obj: Any) -> Dict[str, Set[str]]:
    """{{env.X}} / {{secret.X}} names used anywhere inside `obj`."""
    found: Dict[str, Set[str]] = {"env": set(), "secret": set()}

    def visit(v: Any) -> None:
        if isinstance(v, str):
            for kind, name in _REF_RE.findall(v):
                found[kind].add(name)
        elif isinstance(v, dict):
            for x in v.values():
                visit(x)
        elif isinstance(v, list):
            for x in v:
                visit(x)
    visit(obj)
    return found


def _iter_cases(pack: Dict[str, Any]) -> Iterable[Tuple[str, Dict[str, Any]]]:
    for suite in pack.get("suites") or []:
        for case in suite.get("cases") or []:
            yield suite.get("name", "?"), case


def validate_pack(pack: Any, step_types: Optional[Set[str]] = None) -> List[str]:
    """Every problem with `pack`, as readable strings ([] = valid)."""
    errors: List[str] = []
    if not isinstance(pack, dict):
        return ["pack must be a JSON object"]
    step_types = step_types if step_types is not None else catalogue_step_types()
    if pack.get("format") != PACK_FORMAT:
        errors.append(f"format must be '{PACK_FORMAT}'")
    if pack.get("format_version") != PACK_FORMAT_VERSION:
        errors.append(f"format_version must be {PACK_FORMAT_VERSION}")
    if not isinstance(pack.get("id"), str) or not _ID_RE.match(pack["id"]):
        errors.append("id must be a lowercase slug")
    if not isinstance(pack.get("version"), str) or not _VERSION_RE.match(pack["version"]):
        errors.append("version must be MAJOR.MINOR.PATCH")
    for key in ("title", "description"):
        if not isinstance(pack.get(key), str) or not pack[key].strip():
            errors.append(f"{key} is required")

    declared: Dict[str, Dict[str, Any]] = {}
    for var in pack.get("variables") or []:
        name = var.get("name") if isinstance(var, dict) else None
        if not isinstance(name, str) or not _VAR_RE.match(name):
            errors.append(f"variable name {name!r} must be UPPER_SNAKE_CASE")
            continue
        if name in declared:
            errors.append(f"variable {name} is declared twice")
        if not isinstance(var.get("description"), str) or not var["description"].strip():
            errors.append(f"variable {name} needs a description")
        if not isinstance(var.get("secret", False), bool):
            errors.append(f"variable {name}: secret must be true/false")
        if var.get("secret") and var.get("default") not in (None, ""):
            errors.append(f"secret variable {name} must not have a default")
        if var.get("default") is not None and not isinstance(var["default"], str):
            errors.append(f"variable {name}: default must be a string")
        declared[name] = var

    suites = pack.get("suites")
    if not isinstance(suites, list) or not suites:
        errors.append("suites must be a non-empty list")
        suites = []
    case_names: Set[str] = set()
    for suite in suites:
        if not isinstance(suite, dict) or not suite.get("name"):
            errors.append("every suite needs a name")
            continue
        cases = suite.get("cases")
        if not isinstance(cases, list) or not cases:
            errors.append(f"suite {suite['name']!r} has no cases")
            continue
        for case in cases:
            cname = case.get("name") if isinstance(case, dict) else None
            if not cname:
                errors.append(f"suite {suite['name']!r}: a case has no name")
                continue
            if cname in case_names:
                errors.append(f"case name {cname!r} is used twice")
            case_names.add(cname)
            if not isinstance(case.get("description"), str) or not case["description"].strip():
                errors.append(f"case {cname!r} needs a description")
            tags = case.get("tags", [])
            if not isinstance(tags, list) or not all(isinstance(t, str) and t for t in tags):
                errors.append(f"case {cname!r}: tags must be a list of strings")
            steps = case.get("steps")
            if not isinstance(steps, list) or not steps:
                errors.append(f"case {cname!r} has no steps")
                continue
            for i, step in enumerate(steps, 1):
                where = f"case {cname!r} step {i}"
                if not isinstance(step, dict):
                    errors.append(f"{where} must be an object")
                    continue
                if step.get("type") not in step_types:
                    errors.append(f"{where}: unknown step type {step.get('type')!r} (not in /api/step-types)")
                for a in ((step.get("params") or {}).get("assertions") or []):
                    if isinstance(a, dict) and "{{" in json.dumps(a.get("value", "")):
                        errors.append(f"{where}: assertion values are compared verbatim — "
                                      f"templates are not resolved there")

    used = references(suites)
    for kind, names in used.items():
        for name in sorted(names):
            var = declared.get(name)
            if var is None:
                errors.append(f"{{{{{kind}.{name}}}}} is used but not declared in variables")
            elif bool(var.get("secret")) != (kind == "secret"):
                want = "{{secret.%s}}" % name if var.get("secret") else "{{env.%s}}" % name
                errors.append(f"{{{{{kind}.{name}}}}} must be referenced as {want}")
    for name in declared:
        if name not in used["env"] and name not in used["secret"]:
            errors.append(f"variable {name} is declared but never used")
    return errors


@lru_cache(maxsize=1)
def _load_all() -> Tuple[Dict[str, Any], ...]:
    packs = []
    for path in sorted(PACK_DIR.glob("*.json")):
        with open(path, encoding="utf-8") as fh:
            packs.append(json.load(fh))
    return tuple(packs)


def list_packs() -> List[Dict[str, Any]]:
    return [copy.deepcopy(p) for p in _load_all()]


def get_pack(pack_id: str) -> Optional[Dict[str, Any]]:
    for p in _load_all():
        if p.get("id") == pack_id:
            return copy.deepcopy(p)
    return None


def pack_summary(pack: Dict[str, Any]) -> Dict[str, Any]:
    """What the gallery shows: metadata, variables (no values) and the cases."""
    return {
        "id": pack["id"], "version": pack["version"], "title": pack["title"],
        "category": pack.get("category"), "description": pack["description"],
        "suite_name": suite_name(pack),
        "variables": [{"name": v["name"], "description": v["description"],
                       "required": bool(v.get("required")), "secret": bool(v.get("secret")),
                       "default": None if v.get("secret") else v.get("default")}
                      for v in pack.get("variables") or []],
        "suites": [{"name": s["name"], "description": s.get("description"),
                    "cases": [{"name": c["name"], "description": c.get("description"),
                               "tags": c.get("tags") or [],
                               "step_types": [st.get("type") for st in c.get("steps") or []]}
                              for c in s.get("cases") or []]}
                   for s in pack.get("suites") or []],
        "case_count": sum(len(s.get("cases") or []) for s in pack.get("suites") or []),
    }


def suite_name(pack: Dict[str, Any]) -> str:
    return f"{pack['title']} pack v{pack['version']}"


def variable_declarations(pack: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Declarations as stored on the imported suite (never values)."""
    return [{k: v[k] for k in ("name", "description", "required", "secret", "default") if k in v}
            for v in pack.get("variables") or []]


def split_values(pack: Dict[str, Any], values: Dict[str, Any]) -> Dict[str, Any]:
    """Sort submitted values into env / secret, apply defaults, list what is
    missing. Raises PackError for unknown names or bad values."""
    declared = {v["name"]: v for v in pack.get("variables") or []}
    unknown = sorted(set(values) - set(declared))
    if unknown:
        raise PackError(f"unknown variable(s): {', '.join(unknown)}")
    env: Dict[str, str] = {}
    secret: Dict[str, str] = {}
    defaults_used: List[str] = []
    missing: List[str] = []
    for name, var in declared.items():
        raw = values.get(name)
        if raw is not None and not isinstance(raw, str):
            raise PackError(f"{name} must be a string")
        if raw is not None and len(raw) > MAX_VALUE_LEN:
            raise PackError(f"{name} is longer than {MAX_VALUE_LEN} characters")
        given = raw if raw not in (None, "") else None
        if var.get("secret"):
            if given is not None:
                secret[name] = given
            elif var.get("required"):
                missing.append(name)
            continue
        if given is not None:
            env[name] = given
        elif var.get("default") not in (None, ""):
            env[name] = var["default"]
            defaults_used.append(name)
        elif var.get("required"):
            missing.append(name)
    return {"env": env, "secret": secret, "defaults_used": defaults_used, "missing": missing}


def suite_import_data(pack: Dict[str, Any]) -> Dict[str, Any]:
    """The pack as the suite-import shape (create_suite_from_data), with fresh
    step ids so two imports never share them. One pack suite -> a suite with
    cases; several -> a parent suite with one sub-module each."""
    def cases(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
        out = []
        for c in spec.get("cases") or []:
            steps = []
            for st in c.get("steps") or []:
                step = copy.deepcopy(st)
                step["id"] = str(uuid.uuid4())
                steps.append(step)
            out.append({"name": c["name"], "description": c.get("description"),
                        "tags": list(c.get("tags") or []), "steps": steps})
        return out
    provenance = {"id": pack["id"], "version": pack["version"],
                  "variables": variable_declarations(pack)}
    suites = pack.get("suites") or []
    settings = {"headers": {}, "params": {}, "test_pack": provenance}
    if len(suites) == 1:
        return {"name": suite_name(pack), "description": suites[0].get("description") or pack["description"],
                "execution_mode": "separate", "settings": settings, "test_cases": cases(suites[0])}
    return {"name": suite_name(pack), "description": pack["description"], "execution_mode": "separate",
            "settings": settings,
            "sub_modules": [{"name": s["name"], "description": s.get("description"),
                             "execution_mode": "separate", "test_cases": cases(s)} for s in suites]}


def export_variables(suite_tree: Dict[str, Any]) -> List[Dict[str, Any]]:
    """`variables` block for a suite export: the pack declarations when the
    suite came from a pack, else names derived from {{env.X}} / {{secret.X}}.
    Never values of secrets."""
    pack = ((suite_tree.get("settings") or {}).get("test_pack") or {}) if isinstance(
        suite_tree.get("settings"), dict) else {}
    declared = {v["name"]: v for v in pack.get("variables") or [] if isinstance(v, dict) and v.get("name")}
    used = references({"cases": suite_tree.get("test_cases"), "subs": suite_tree.get("sub_modules"),
                       "settings": {k: v for k, v in (suite_tree.get("settings") or {}).items()
                                    if k != "test_pack"}})
    out: Dict[str, Dict[str, Any]] = {}
    for name, var in declared.items():
        entry = {k: var[k] for k in ("name", "description", "required", "secret", "default") if k in var}
        if entry.get("secret"):
            entry.pop("default", None)
        out[name] = entry
    for kind, names in used.items():
        for name in names:
            out.setdefault(name, {"name": name, "secret": kind == "secret"})
    return [out[k] for k in sorted(out)]

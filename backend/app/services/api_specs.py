"""Project OpenAPI specs and `expect_schema` resolution (WP11 item 5).

An `http-request` step may carry

    params.expect_schema = {"schema": {...JSON Schema...}}
                         | {"openapi": {"operationId": "getPet"}, "status": "200"}
                         | {"openapi": {"method": "GET", "path": "/pets/{id}"}}

The worker validates the response body against an inline JSON Schema with
ajv. `openapi` references are turned into an inline schema HERE, at dispatch,
from the project's stored spec (`ProjectApiSpec`), so a worker never needs the
database and a spec edit takes effect on the next run. A reference that cannot
be resolved is not dropped: it becomes `{"error": ...}` and the worker fails the
step with that message — a schema check that silently stops checking is worse
than a red step.
"""
from __future__ import annotations

import copy
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
# Cap on how much of a spec one resolved schema may drag along through $refs.
MAX_REFS = 500


class SchemaRefError(ValueError):
    pass


def spec_summary(spec: Dict[str, Any]) -> Dict[str, Any]:
    info = spec.get("info") if isinstance(spec.get("info"), dict) else {}
    ops = []
    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if method.lower() in HTTP_METHODS and isinstance(op, dict):
                ops.append({"method": method.upper(), "path": path,
                            "operationId": op.get("operationId")})
    return {"title": info.get("title"), "version": info.get("version"),
            "openapi": spec.get("openapi") or spec.get("swagger"), "operations": ops}


def validate_spec(spec: Any) -> None:
    if not isinstance(spec, dict) or not isinstance(spec.get("paths"), dict):
        raise SchemaRefError("not an OpenAPI document: needs a `paths` object")
    if not (spec.get("openapi") or spec.get("swagger")):
        raise SchemaRefError("not an OpenAPI document: needs `openapi` or `swagger`")


def _find_operation(spec: Dict[str, Any], ref: Dict[str, Any]) -> Tuple[str, str, Dict[str, Any]]:
    paths = spec.get("paths") or {}
    op_id = ref.get("operationId") or ref.get("operation_id")
    if op_id:
        for path, item in paths.items():
            if not isinstance(item, dict):
                continue
            for method, op in item.items():
                if method.lower() in HTTP_METHODS and isinstance(op, dict) and op.get("operationId") == op_id:
                    return method.upper(), path, op
        raise SchemaRefError(f"operationId '{op_id}' is not in the project's OpenAPI spec")
    method = str(ref.get("method") or "").lower()
    path = ref.get("path")
    if not method or not path:
        raise SchemaRefError("openapi reference needs operationId, or method + path")
    item = paths.get(path)
    if not isinstance(item, dict) or not isinstance(item.get(method), dict):
        raise SchemaRefError(f"{method.upper()} {path} is not in the project's OpenAPI spec")
    return method.upper(), path, item[method]


def _response_schema(spec: Dict[str, Any], op: Dict[str, Any], status: Optional[str]) -> Tuple[str, Any]:
    responses = op.get("responses") or {}
    if not isinstance(responses, dict) or not responses:
        raise SchemaRefError("operation declares no responses")
    keys = [str(k) for k in responses]
    if status is not None:
        chosen = str(status)
        if chosen not in keys:
            raise SchemaRefError(f"operation has no '{chosen}' response (has {', '.join(keys)})")
    else:
        two_xx = sorted(k for k in keys if k.startswith("2"))
        chosen = two_xx[0] if two_xx else ("default" if "default" in keys else keys[0])
    resp = responses.get(chosen)
    if resp is None and chosen.isdigit():  # YAML-loaded specs key statuses as ints
        resp = responses.get(int(chosen))
    resp = _deref(spec, resp) if isinstance(resp, dict) else resp
    if not isinstance(resp, dict):
        raise SchemaRefError(f"response '{chosen}' is malformed")
    if "schema" in resp:  # Swagger 2
        return chosen, resp["schema"]
    content = resp.get("content") or {}
    for ctype, media in content.items():
        if "json" in ctype.lower() and isinstance(media, dict) and "schema" in media:
            return chosen, media["schema"]
    raise SchemaRefError(f"response '{chosen}' has no JSON schema")


def _pointer(spec: Dict[str, Any], ref: str) -> Any:
    if not ref.startswith("#/"):
        raise SchemaRefError(f"external $ref '{ref}' is not supported; inline it in the spec")
    node: Any = spec
    for raw in ref[2:].split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            raise SchemaRefError(f"$ref '{ref}' does not resolve")
        node = node[part]
    return node


def _deref(spec: Dict[str, Any], node: Dict[str, Any]) -> Any:
    seen = 0
    while isinstance(node, dict) and isinstance(node.get("$ref"), str):
        node = _pointer(spec, node["$ref"])
        seen += 1
        if seen > 20:
            raise SchemaRefError("$ref chain too deep")
    return node


def _collect_refs(spec: Dict[str, Any], schema: Any) -> Dict[str, Any]:
    """Every local `$ref` target reachable from `schema`, by pointer.

    Recursive schemas are fine — each pointer is visited once — and the
    pruned copy keeps the spec's own paths, so the `$ref` strings in the
    schema resolve unchanged when ajv compiles it."""
    found: Dict[str, Any] = {}
    stack = [schema]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref not in found:
                if len(found) >= MAX_REFS:
                    raise SchemaRefError("schema references too many components")
                target = _pointer(spec, ref)
                found[ref] = target
                stack.append(target)
            stack.extend(v for k, v in node.items() if k != "$ref")
        elif isinstance(node, list):
            stack.extend(node)
    return found


def _place(root: Dict[str, Any], ref: str, value: Any) -> None:
    parts = [p.replace("~1", "/").replace("~0", "~") for p in ref[2:].split("/")]
    node = root
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def resolve_openapi_schema(spec: Dict[str, Any], ref: Dict[str, Any],
                           status: Optional[str] = None) -> Dict[str, Any]:
    """A self-contained JSON Schema for one operation's response body."""
    validate_spec(spec)
    method, path, op = _find_operation(spec, ref)
    chosen, schema = _response_schema(spec, op, status)
    out: Dict[str, Any] = {"allOf": [copy.deepcopy(schema)]}
    for pointer, target in _collect_refs(spec, schema).items():
        _place(out, pointer, copy.deepcopy(target))
    return {"schema": out, "source": f"openapi {method} {path} {chosen}"}


def has_openapi_refs(steps: Any) -> bool:
    return any(
        isinstance(s, dict) and isinstance((s.get("params") or {}).get("expect_schema"), dict)
        and "openapi" in s["params"]["expect_schema"]
        for s in (steps or []))


_CACHE: Dict[int, Tuple[float, Optional[Dict[str, Any]]]] = {}
_TTL = 10.0


def resolve_step_schemas(steps: List[Any], project_id: Optional[int],
                         load_spec: Callable[[int], Optional[Dict[str, Any]]]) -> List[Any]:
    """Return `steps` with every `expect_schema.openapi` reference resolved.

    `load_spec(project_id)` fetches the stored spec; it is called at most once
    per project per few seconds (a SEPARATE run dispatches one payload per
    case). Steps without a reference are returned untouched."""
    if not has_openapi_refs(steps):
        return steps
    spec: Optional[Dict[str, Any]] = None
    if project_id is not None:
        hit = _CACHE.get(project_id)
        if hit and time.monotonic() - hit[0] < _TTL:
            spec = hit[1]
        else:
            try:
                spec = load_spec(project_id)
                _CACHE[project_id] = (time.monotonic(), spec)
            except Exception as exc:  # noqa: BLE001 — never block dispatch; fail the step instead
                print(f"[api_specs] could not load OpenAPI spec for project {project_id}: {exc}")
                spec = None
    out = []
    for step in steps:
        es = (step.get("params") or {}).get("expect_schema") if isinstance(step, dict) else None
        if not (isinstance(es, dict) and "openapi" in es):
            out.append(step)
            continue
        new_es = {k: v for k, v in es.items() if k not in ("schema", "error")}
        try:
            if not spec:
                raise SchemaRefError("expect_schema references an OpenAPI operation, but this project has no stored spec")
            ref = es.get("openapi") if isinstance(es.get("openapi"), dict) else {}
            new_es.update(resolve_openapi_schema(spec, ref, es.get("status")))
        except SchemaRefError as exc:
            new_es["error"] = str(exc)
        out.append({**step, "params": {**step["params"], "expect_schema": new_es}})
    return out


def load_spec_sync(project_id: int) -> Optional[Dict[str, Any]]:
    """Loader for the Celery dispatch path."""
    from sqlmodel import Session, select
    from app.models import ProjectApiSpec
    from app.worker import sync_engine
    with Session(sync_engine) as session:
        row = session.exec(select(ProjectApiSpec).where(ProjectApiSpec.project_id == project_id)).first()
        return row.spec if row else None

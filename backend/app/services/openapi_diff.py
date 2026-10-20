"""OpenAPI breaking-change diff (WP16b, decision V2).

Pure logic: two OpenAPI documents in, a list of classified changes out. No
database, no network. `app/api/developer.py` feeds it stored spec versions and
`link_cases` attaches the test cases whose `expect_schema` references each
changed operation.

The one question every rule answers is "can a client that works against the
OLD document stop working against the NEW one?". That makes the rules
direction-dependent:

* **request** schemas (parameters, request bodies) are what the client SENDS.
  Narrowing breaks (a value that used to be accepted is refused): a new
  required field, a removed enum value, a stricter type/format/limit.
  Widening is safe.
* **response** schemas are what the client RECEIVES. Widening breaks (the
  client gets something it was never told about): a removed property, a
  property that is no longer required, a broader type, a response that may
  now be null. Narrowing is safe.

Decisions worth knowing (also in CLAUDE.md):

* A response enum gaining a value is NON-breaking. OpenAPI does not say
  enums are closed for consumers, and treating every new order status as a
  break makes the gate useless. Clients that decode into closed enums
  (strict codegen) can still break, so the change is reported, with that
  caveat in its message.
* "nullable -> non-nullable" breaks only in a REQUEST (a client sending null
  is now refused). In a response it is narrowing and safe; the breaking
  response change is the reverse, "non-nullable -> nullable".
* A removed 2xx/3xx/default response is breaking. A removed 4xx/5xx is not
  (clients handle errors generically) unless a test pins that status — that
  promotion happens in `link_cases`, because "a status clients use" is only
  knowable from the tests.
* A removed request property/parameter is non-breaking (a server ignores
  what it no longer reads) unless the new object schema sets
  `additionalProperties: false`, which makes sending it an error.
* Only LOCAL `$ref`s (`#/...`) are followed; an external ref is compared as
  an opaque string. Refs are resolved before comparing, so renaming a
  component whose shape is unchanged is not a change.
* `type`-less schemas are inferred from `properties` (object) / `items`
  (array); a schema that is still typeless on either side reports no type
  change — loosely written specs omit `type` and that is not a break.
* Work is bounded (node budget, depth, change cap) so a 5 MB spec cannot pin
  a worker; a diff that hit a bound says `truncated: true`.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")

BREAKING = "breaking"
NON_BREAKING = "non_breaking"

MAX_DEPTH = 40
MAX_NODES = 50_000
MAX_CHANGES = 2_000

# rule -> one-line explanation, used by SARIF rule metadata and the UI legend.
RULES: Dict[str, str] = {
    "operation_removed": "An operation was removed.",
    "operation_added": "An operation was added.",
    "operation_deprecated": "An operation was marked deprecated.",
    "operation_id_changed": "An operation's operationId changed (the wire contract is unchanged).",
    "description_changed": "A summary or description changed.",
    "required_parameter_added": "A new required parameter was added.",
    "optional_parameter_added": "A new optional parameter was added.",
    "parameter_removed": "A parameter was removed.",
    "parameter_became_required": "An optional parameter became required.",
    "parameter_became_optional": "A required parameter became optional.",
    "request_body_added_required": "A required request body was added.",
    "request_body_added_optional": "An optional request body was added.",
    "request_body_became_required": "The request body became required.",
    "request_body_removed": "The request body was removed.",
    "request_media_type_removed": "A request media type is no longer accepted.",
    "request_media_type_added": "A request media type was added.",
    "response_status_removed": "A response status was removed.",
    "response_status_added": "A response status was added.",
    "response_media_type_removed": "A response media type was removed.",
    "response_media_type_added": "A response media type was added.",
    "request_property_required_added": "A new required request property was added.",
    "request_property_added": "A new optional request property was added.",
    "request_property_removed": "A request property was removed.",
    "request_property_became_required": "A request property became required.",
    "request_property_became_optional": "A request property became optional.",
    "response_property_removed": "A response property was removed.",
    "response_property_added": "A response property was added.",
    "response_property_became_optional": "A response property is no longer required.",
    "response_property_became_required": "A response property became required.",
    "type_changed": "A schema's type changed.",
    "type_widened": "A schema's type was widened.",
    "type_narrowed": "A schema's type was narrowed.",
    "format_changed": "A schema's format changed.",
    "enum_value_removed": "An enum value was removed.",
    "enum_value_added": "An enum value was added.",
    "enum_added": "An enum restriction was added.",
    "enum_removed": "An enum restriction was removed.",
    "became_nullable": "A schema now allows null.",
    "became_non_nullable": "A schema no longer allows null.",
    "constraint_narrowed": "A length/range/size limit got stricter.",
    "constraint_widened": "A length/range/size limit got looser.",
    "additional_properties_disallowed": "The object no longer accepts unknown properties.",
    "variant_added": "A oneOf/anyOf variant was added.",
    "variant_removed": "A oneOf/anyOf variant was removed.",
    "unresolved_ref": "A $ref could not be resolved, so that part was not compared.",
}

# Formats where one value is a strict superset of another (narrow -> wide).
_FORMAT_WIDENING = {("int32", "int64"), ("float", "double")}
# Lower bounds (a bigger value is stricter) and upper bounds (a smaller one is).
_LOWER_BOUNDS = ("minLength", "minimum", "minItems", "minProperties", "exclusiveMinimum")
_UPPER_BOUNDS = ("maxLength", "maximum", "maxItems", "maxProperties", "exclusiveMaximum")
_DOC_KEYS = ("description", "title", "example", "examples", "summary", "externalDocs",
             "deprecated", "xml", "$comment")


def normalise_path(path: str) -> str:
    """`/pets/{petId}` and `/pets/{id}` are the same endpoint."""
    return re.sub(r"\{[^}/]*\}", "{}", str(path or "")).rstrip("/") or "/"


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------

@dataclass
class Change:
    severity: str
    rule: str
    method: str
    path: str
    location: str
    message: str
    operation_id: Optional[str] = None
    old: Any = None
    new: Any = None
    status: Optional[str] = None
    affected_cases: List[Dict[str, Any]] = field(default_factory=list)
    promoted_reason: Optional[str] = None

    @property
    def id(self) -> str:
        raw = "|".join((self.rule, self.method, normalise_path(self.path), self.location))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "severity": self.severity, "rule": self.rule,
            "method": self.method, "path": self.path, "operation_id": self.operation_id,
            "location": self.location, "message": self.message, "status": self.status,
            "old": _short(self.old), "new": _short(self.new),
            "affected_cases": self.affected_cases, "promoted_reason": self.promoted_reason,
        }


def _short(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = value if isinstance(value, str) else canonical_json(value)
    return text if len(text) <= 200 else text[:197] + "..."


class _Budget(Exception):
    pass


# ---------------------------------------------------------------------------
# $ref resolution
# ---------------------------------------------------------------------------

def _pointer(spec: Dict[str, Any], ref: str) -> Any:
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return None
    node: Any = spec
    for raw in ref[2:].split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return None
    return node


def deref(spec: Dict[str, Any], node: Any) -> Tuple[Any, Optional[str], bool]:
    """(target, last ref followed, resolved?) — follows ref chains, cycle-safe."""
    seen: Set[str] = set()
    last = None
    while isinstance(node, dict) and isinstance(node.get("$ref"), str):
        ref = node["$ref"]
        if ref in seen or len(seen) > 20:
            return {}, ref, False
        seen.add(ref)
        last = ref
        target = _pointer(spec, ref)
        if target is None:
            return node, ref, False
        node = target
    return node, last, True


# ---------------------------------------------------------------------------
# Schema normalisation
# ---------------------------------------------------------------------------

def _types(schema: Dict[str, Any]) -> Optional[Set[str]]:
    """Declared types as a set, `null` included when nullable (3.0) or listed
    (3.1). None = no type and none inferable."""
    t = schema.get("type")
    out: Set[str]
    if isinstance(t, list):
        out = {str(x) for x in t}
    elif isinstance(t, str):
        out = {t}
    elif isinstance(schema.get("properties"), dict) or "additionalProperties" in schema:
        out = {"object"}
    elif "items" in schema:
        out = {"array"}
    else:
        return {"null"} if schema.get("nullable") is True else None
    if schema.get("nullable") is True:
        out.add("null")
    enum = schema.get("enum")
    if isinstance(enum, list) and any(v is None for v in enum):
        out.add("null")
    return out


def _merge_allof(spec: Dict[str, Any], schema: Dict[str, Any], depth: int = 0) -> Dict[str, Any]:
    """Fold `allOf` into one schema: properties and required are unions, the
    first declared type/format wins. Good enough for the composition style
    specs actually use (base + extension); not a full JSON Schema intersection."""
    parts = schema.get("allOf")
    if not isinstance(parts, list) or depth > 10:
        return schema
    merged: Dict[str, Any] = {k: v for k, v in schema.items() if k != "allOf"}
    props: Dict[str, Any] = dict(merged.get("properties") or {}) if isinstance(merged.get("properties"), dict) else {}
    required: List[str] = list(merged.get("required") or []) if isinstance(merged.get("required"), list) else []
    for raw in parts:
        sub, _, ok = deref(spec, raw)
        if not isinstance(sub, dict) or not ok:
            continue
        sub = _merge_allof(spec, sub, depth + 1)
        for k, v in sub.items():
            if k == "properties" and isinstance(v, dict):
                for name, ps in v.items():
                    props[name] = {"allOf": [props[name], ps]} if name in props else ps
            elif k == "required" and isinstance(v, list):
                required.extend(r for r in v if r not in required)
            elif k not in merged:
                merged[k] = v
    if props:
        merged["properties"] = props
    if required:
        merged["required"] = required
    return merged


def shape(spec: Dict[str, Any], node: Any, depth: int = 0, active: Optional[Set[str]] = None) -> Any:
    """A canonical, documentation-free form of a schema with local refs
    inlined (a recursive ref becomes a marker). Used to match oneOf/anyOf
    variants regardless of order or component names."""
    active = active or set()
    target, ref, ok = deref(spec, node)
    if not ok:
        return {"$unresolved": ref}
    if ref and ref in active:
        return {"$recursive": True}
    if depth > 12 or not isinstance(target, (dict, list)):
        return target if not isinstance(target, (dict, list)) else {"$deep": True}
    if isinstance(target, list):
        return [shape(spec, x, depth + 1, active) for x in target]
    target = _merge_allof(spec, target)
    nxt = active | ({ref} if ref else set())
    out: Dict[str, Any] = {}
    for k, v in target.items():
        if k in _DOC_KEYS:
            continue
        if k == "enum" and isinstance(v, list):
            out[k] = sorted(canonical_json(x) for x in v)
        elif k == "required" and isinstance(v, list):
            out[k] = sorted(str(x) for x in v)
        elif k == "type" or k == "nullable":
            continue
        elif isinstance(v, (dict, list)):
            out[k] = shape(spec, v, depth + 1, nxt)
        else:
            out[k] = v
    types = _types(target)
    if types is not None:
        out["type"] = sorted(types)
    return out


def _fingerprint(spec: Dict[str, Any], node: Any) -> str:
    return canonical_json(shape(spec, node))


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

@dataclass
class _Op:
    method: str
    path: str
    operation_id: Optional[str]


class _Differ:
    def __init__(self, old: Dict[str, Any], new: Dict[str, Any]):
        self.old = old
        self.new = new
        self.changes: List[Change] = []
        self.nodes = 0
        self.truncated = False
        self.op: Optional[_Op] = None
        self.status: Optional[str] = None
        # (old target id, new target id, direction) pairs on the CURRENT
        # comparison path, so recursive schemas terminate. A component used
        # in two places is compared (and reported) at each place; MAX_NODES
        # bounds the total work.
        self._pairs: Set[Tuple[int, int, str]] = set()

    # -- recording -----------------------------------------------------------

    def add(self, severity: str, rule: str, location: str, message: str,
            old: Any = None, new: Any = None) -> None:
        if len(self.changes) >= MAX_CHANGES:
            self.truncated = True
            raise _Budget()
        op = self.op or _Op("", "", None)
        self.changes.append(Change(severity=severity, rule=rule, method=op.method, path=op.path,
                                   operation_id=op.operation_id, location=location,
                                   message=message, old=old, new=new, status=self.status))

    def tick(self) -> None:
        self.nodes += 1
        if self.nodes > MAX_NODES:
            self.truncated = True
            raise _Budget()

    # -- operations ----------------------------------------------------------

    def operations(self, spec: Dict[str, Any]) -> Dict[Tuple[str, str], Tuple[str, Dict[str, Any], Dict[str, Any]]]:
        """(METHOD, normalised path) -> (path as written, path item, operation)."""
        out = {}
        paths = spec.get("paths") if isinstance(spec.get("paths"), dict) else {}
        for path, item in paths.items():
            item, _, _ = deref(spec, item)
            if not isinstance(item, dict):
                continue
            for method, op in item.items():
                if method.lower() in HTTP_METHODS and isinstance(op, dict):
                    out[(method.upper(), normalise_path(path))] = (str(path), item, op)
        return out

    def run(self) -> None:
        old_ops = self.operations(self.old)
        new_ops = self.operations(self.new)
        try:
            for key in sorted(set(old_ops) | set(new_ops)):
                method = key[0]
                if key not in new_ops:
                    path, _, op = old_ops[key]
                    self.op = _Op(method, path, op.get("operationId"))
                    self.status = None
                    self.add(BREAKING, "operation_removed", "operation",
                             f"{method} {path} was removed")
                    continue
                if key not in old_ops:
                    path, _, op = new_ops[key]
                    self.op = _Op(method, path, op.get("operationId"))
                    self.status = None
                    self.add(NON_BREAKING, "operation_added", "operation",
                             f"{method} {path} was added")
                    continue
                self.compare_operation(old_ops[key], new_ops[key], method)
        except _Budget:
            pass

    def compare_operation(self, old_t, new_t, method: str) -> None:
        old_path, old_item, old_op = old_t
        new_path, new_item, new_op = new_t
        self.op = _Op(method, new_path, new_op.get("operationId") or old_op.get("operationId"))
        self.status = None
        self._pairs = set()
        if old_op.get("operationId") != new_op.get("operationId") and old_op.get("operationId"):
            self.add(NON_BREAKING, "operation_id_changed", "operationId",
                     f"operationId changed from '{old_op.get('operationId')}' to "
                     f"'{new_op.get('operationId')}'",
                     old_op.get("operationId"), new_op.get("operationId"))
        if not old_op.get("deprecated") and new_op.get("deprecated"):
            self.add(NON_BREAKING, "operation_deprecated", "operation", f"{method} {new_path} is now deprecated")
        for key in ("summary", "description"):
            if (old_op.get(key) or "") != (new_op.get(key) or ""):
                self.add(NON_BREAKING, "description_changed", key, f"operation {key} changed")
        self.compare_parameters(old_path, old_item, old_op, new_path, new_item, new_op)
        self.compare_request_body(old_op, new_op)
        self.compare_responses(old_op, new_op)

    # -- parameters ----------------------------------------------------------

    def _params(self, spec, path: str, item, op) -> Dict[Tuple[str, str], Tuple[str, Dict[str, Any]]]:
        """(in, key) -> (display name, parameter). Path parameters are keyed by
        their POSITION in the template: renaming {id} to {petId} changes
        nothing on the wire. Header names are case-insensitive."""
        placeholders = re.findall(r"\{([^}/]*)\}", path)
        out: Dict[Tuple[str, str], Tuple[str, Dict[str, Any]]] = {}
        for source in (item.get("parameters"), op.get("parameters")):  # op-level overrides
            for raw in source or []:
                p, _, ok = deref(spec, raw)
                if not isinstance(p, dict) or not ok:
                    continue
                where = str(p.get("in") or "")
                name = str(p.get("name") or "")
                if where == "body":  # Swagger 2 bodies are compared as request bodies
                    continue
                if where == "path" and name in placeholders:
                    key = f"#{placeholders.index(name)}"
                elif where == "header":
                    key = name.lower()
                else:
                    key = name
                out[(where, key)] = (name, p)
        return out

    def compare_parameters(self, old_path, old_item, old_op, new_path, new_item, new_op) -> None:
        olds = self._params(self.old, old_path, old_item, old_op)
        news = self._params(self.new, new_path, new_item, new_op)
        for key, (name, p) in news.items():
            loc = f"parameters/{key[0]}/{name}"
            required = bool(p.get("required")) or key[0] == "path"
            if key not in olds:
                if required:
                    self.add(BREAKING, "required_parameter_added", loc,
                             f"new required {key[0]} parameter '{name}'")
                else:
                    self.add(NON_BREAKING, "optional_parameter_added", loc,
                             f"new optional {key[0]} parameter '{name}'")
                continue
            _, op_ = olds[key]
            was_required = bool(op_.get("required")) or key[0] == "path"
            if required and not was_required:
                self.add(BREAKING, "parameter_became_required", loc,
                         f"{key[0]} parameter '{name}' became required")
            elif was_required and not required:
                self.add(NON_BREAKING, "parameter_became_optional", loc,
                         f"{key[0]} parameter '{name}' became optional")
            self.compare_schema(op_.get("schema", _swagger2_param_schema(op_)),
                                p.get("schema", _swagger2_param_schema(p)), "request", f"{loc}/schema")
        for key, (name, _) in olds.items():
            if key not in news:
                self.add(NON_BREAKING, "parameter_removed", f"parameters/{key[0]}/{name}",
                         f"{key[0]} parameter '{name}' was removed (clients still sending it "
                         "may find it ignored)")

    # -- request bodies ------------------------------------------------------

    def _request_body(self, spec, op) -> Tuple[Optional[Dict[str, Any]], bool]:
        """(media type -> schema, required). Swagger 2 `in: body` → one json entry."""
        rb = op.get("requestBody")
        if rb is not None:
            rb, _, ok = deref(spec, rb)
            if not isinstance(rb, dict) or not ok:
                return None, False
            content = rb.get("content") if isinstance(rb.get("content"), dict) else {}
            return ({str(k).lower(): (v or {}).get("schema") for k, v in content.items()
                     if isinstance(v, dict) or v is None}, bool(rb.get("required")))
        for raw in op.get("parameters") or []:
            p, _, _ = deref(spec, raw)
            if isinstance(p, dict) and p.get("in") == "body":
                return {"application/json": p.get("schema")}, bool(p.get("required"))
        return None, False

    def compare_request_body(self, old_op, new_op) -> None:
        old_c, old_req = self._request_body(self.old, old_op)
        new_c, new_req = self._request_body(self.new, new_op)
        if old_c is None and new_c is None:
            return
        if old_c is None:
            if new_req:
                self.add(BREAKING, "request_body_added_required", "requestBody", "a required request body was added")
            else:
                self.add(NON_BREAKING, "request_body_added_optional", "requestBody", "an optional request body was added")
            return
        if new_c is None:
            self.add(NON_BREAKING, "request_body_removed", "requestBody", "the request body was removed")
            return
        if new_req and not old_req:
            self.add(BREAKING, "request_body_became_required", "requestBody", "the request body became required")
        for ctype in sorted(old_c):
            if ctype not in new_c:
                self.add(BREAKING, "request_media_type_removed", f"requestBody/{ctype}",
                         f"request media type {ctype} is no longer accepted")
            else:
                self.compare_schema(old_c[ctype], new_c[ctype], "request", f"requestBody/{ctype}")
        for ctype in sorted(set(new_c) - set(old_c)):
            self.add(NON_BREAKING, "request_media_type_added", f"requestBody/{ctype}",
                     f"request media type {ctype} was added")

    # -- responses -----------------------------------------------------------

    def _responses(self, spec, op) -> Dict[str, Dict[str, Any]]:
        out = {}
        raw = op.get("responses") if isinstance(op.get("responses"), dict) else {}
        for status, resp in raw.items():
            resp, _, ok = deref(spec, resp)
            if isinstance(resp, dict) and ok:
                out[str(status).upper() if str(status).lower().endswith("xx") else str(status)] = resp
        return out

    @staticmethod
    def _content(resp: Dict[str, Any]) -> Dict[str, Any]:
        if "schema" in resp:  # Swagger 2
            return {"application/json": resp.get("schema")}
        content = resp.get("content") if isinstance(resp.get("content"), dict) else {}
        return {str(k).lower(): (v or {}).get("schema") for k, v in content.items()
                if isinstance(v, dict) or v is None}

    def compare_responses(self, old_op, new_op) -> None:
        olds = self._responses(self.old, old_op)
        news = self._responses(self.new, new_op)
        for status in sorted(olds):
            self.status = status
            loc = f"responses/{status}"
            if status not in news:
                success = status == "default" or status[:1] in ("2", "3")
                self.add(BREAKING if success else NON_BREAKING, "response_status_removed", loc,
                         f"response {status} was removed"
                         + ("" if success else " (an error response; breaking only for tests that expect it)"))
                continue
            old_c, new_c = self._content(olds[status]), self._content(news[status])
            for ctype in sorted(old_c):
                if ctype not in new_c:
                    self.add(BREAKING, "response_media_type_removed", f"{loc}/{ctype}",
                             f"response {status} no longer returns {ctype}")
                else:
                    self.compare_schema(old_c[ctype], new_c[ctype], "response", f"{loc}/{ctype}")
            for ctype in sorted(set(new_c) - set(old_c)):
                self.add(NON_BREAKING, "response_media_type_added", f"{loc}/{ctype}",
                         f"response {status} can also return {ctype}")
        for status in sorted(set(news) - set(olds)):
            self.status = status
            self.add(NON_BREAKING, "response_status_added", f"responses/{status}",
                     f"response {status} was added")
        self.status = None

    # -- schemas -------------------------------------------------------------

    def compare_schema(self, old_raw: Any, new_raw: Any, direction: str, loc: str, depth: int = 0) -> None:
        self.tick()
        if depth > MAX_DEPTH:
            self.truncated = True
            return
        if old_raw is None or new_raw is None:
            return
        old, old_ref, ok_old = deref(self.old, old_raw)
        new, new_ref, ok_new = deref(self.new, new_raw)
        if not ok_old or not ok_new:
            # An external or broken ref: compare the ref strings only.
            if canonical_json(old_raw) != canonical_json(new_raw):
                self.add(NON_BREAKING, "unresolved_ref", loc,
                         "a $ref could not be resolved locally; that schema was not compared",
                         old_ref, new_ref)
            return
        if not isinstance(old, dict) or not isinstance(new, dict):
            return
        pair = (id(old), id(new), direction)
        if pair in self._pairs:
            return  # recursive schema: already being compared further up
        self._pairs.add(pair)
        try:
            self._compare_resolved(old, new, direction, loc, depth)
        finally:
            self._pairs.discard(pair)

    def _compare_resolved(self, old, new, direction, loc, depth) -> None:
        old = _merge_allof(self.old, old)
        new = _merge_allof(self.new, new)
        if (old.get("description") or "") != (new.get("description") or "") and depth > 0:
            self.add(NON_BREAKING, "description_changed", loc, "schema description changed")

        if "oneOf" in old or "anyOf" in old or "oneOf" in new or "anyOf" in new:
            # The variants carry the structure; comparing the outer objects
            # too would report every property of a schema that became one
            # variant of a oneOf as removed.
            self.compare_variants(old, new, direction, loc, depth)
            return

        self.compare_types(old, new, direction, loc)
        self.compare_format(old, new, direction, loc)
        self.compare_enum(old, new, direction, loc)
        self.compare_constraints(old, new, direction, loc)
        self.compare_properties(old, new, direction, loc, depth)
        if isinstance(old.get("items"), dict) and isinstance(new.get("items"), dict):
            self.compare_schema(old["items"], new["items"], direction, f"{loc}[]", depth + 1)
        oap, nap = old.get("additionalProperties"), new.get("additionalProperties")
        if isinstance(oap, dict) and isinstance(nap, dict):
            self.compare_schema(oap, nap, direction, f"{loc}{{*}}", depth + 1)

    def compare_variants(self, old, new, direction, loc, depth) -> None:
        def variants(spec, s):
            for key in ("oneOf", "anyOf"):
                if isinstance(s.get(key), list):
                    return s[key]
            return [s]
        ov, nv = variants(self.old, old), variants(self.new, new)
        old_fp = {_fingerprint(self.old, v): v for v in ov}
        new_fp = {_fingerprint(self.new, v): v for v in nv}
        removed = [fp for fp in old_fp if fp not in new_fp]
        added = [fp for fp in new_fp if fp not in old_fp]
        # One changed variant on each side: compare them field by field
        # rather than reporting a removal plus an addition.
        if len(removed) == 1 and len(added) == 1:
            self.compare_schema(old_fp[removed[0]], new_fp[added[0]], direction, f"{loc}<variant>", depth + 1)
            return
        for _ in removed:
            self.add(BREAKING if direction == "request" else NON_BREAKING, "variant_removed", loc,
                     "a oneOf/anyOf variant was removed"
                     + (" (requests in that shape are now refused)" if direction == "request" else ""))
        for _ in added:
            self.add(BREAKING if direction == "response" else NON_BREAKING, "variant_added", loc,
                     "a oneOf/anyOf variant was added"
                     + (" (clients may receive a shape they do not know)" if direction == "response" else ""))

    def compare_types(self, old, new, direction, loc) -> None:
        ot, nt = _types(old), _types(new)
        if ot is None or nt is None or ot == nt:
            return
        o_null, n_null = "null" in ot, "null" in nt
        if o_null != n_null:
            if n_null:
                self.add(BREAKING if direction == "response" else NON_BREAKING, "became_nullable", loc,
                         "the value may now be null"
                         + (" (clients may receive null)" if direction == "response" else ""))
            else:
                self.add(BREAKING if direction == "request" else NON_BREAKING, "became_non_nullable", loc,
                         "the value may no longer be null"
                         + (" (clients sending null are refused)" if direction == "request" else ""))
        ot, nt = ot - {"null"}, nt - {"null"}
        if not ot or not nt or ot == nt:
            return

        def covers(wide: Set[str], narrow: Set[str]) -> bool:
            return all(t in wide or (t == "integer" and "number" in wide) for t in narrow)
        if covers(nt, ot):        # new accepts everything old did: widened
            sev = BREAKING if direction == "response" else NON_BREAKING
            self.add(sev, "type_widened", loc, f"type widened from {_tstr(ot)} to {_tstr(nt)}",
                     _tstr(ot), _tstr(nt))
        elif covers(ot, nt):      # narrowed
            sev = BREAKING if direction == "request" else NON_BREAKING
            self.add(sev, "type_narrowed", loc, f"type narrowed from {_tstr(ot)} to {_tstr(nt)}",
                     _tstr(ot), _tstr(nt))
        else:
            self.add(BREAKING, "type_changed", loc, f"type changed from {_tstr(ot)} to {_tstr(nt)}",
                     _tstr(ot), _tstr(nt))

    def compare_format(self, old, new, direction, loc) -> None:
        of, nf = old.get("format"), new.get("format")
        if of == nf:
            return
        if of and nf and (of, nf) in _FORMAT_WIDENING:
            widened = True
        elif of and nf and (nf, of) in _FORMAT_WIDENING:
            widened = False
        elif of and nf:
            self.add(BREAKING, "format_changed", loc, f"format changed from {of} to {nf}", of, nf)
            return
        else:
            widened = not nf  # format removed = any string is fine now
        if widened:
            sev = BREAKING if direction == "response" else NON_BREAKING
        else:
            sev = BREAKING if direction == "request" else NON_BREAKING
        self.add(sev, "format_changed", loc,
                 f"format {'widened' if widened else 'narrowed'} from {of or 'none'} to {nf or 'none'}", of, nf)

    def compare_enum(self, old, new, direction, loc) -> None:
        oe, ne = old.get("enum"), new.get("enum")
        if not isinstance(oe, list) and not isinstance(ne, list):
            return
        if not isinstance(oe, list):
            self.add(BREAKING if direction == "request" else NON_BREAKING, "enum_added", loc,
                     "the value is now restricted to an enum")
            return
        if not isinstance(ne, list):
            self.add(BREAKING if direction == "response" else NON_BREAKING, "enum_removed", loc,
                     "the enum restriction was removed")
            return
        os_ = {canonical_json(v): v for v in oe}
        ns_ = {canonical_json(v): v for v in ne}
        for k in sorted(set(os_) - set(ns_)):
            self.add(BREAKING if direction == "request" else NON_BREAKING, "enum_value_removed", loc,
                     f"enum value {k} was removed", os_[k], None)
        for k in sorted(set(ns_) - set(os_)):
            self.add(NON_BREAKING, "enum_value_added", loc,
                     f"enum value {k} was added"
                     + (" (clients that decode into a closed enum may reject it)" if direction == "response" else ""),
                     None, ns_[k])

    def compare_constraints(self, old, new, direction, loc) -> None:
        for key in _LOWER_BOUNDS + _UPPER_BOUNDS:
            ov, nv = old.get(key), new.get(key)
            if ov == nv or isinstance(ov, bool) or isinstance(nv, bool):
                continue  # 3.0 boolean exclusiveMinimum is a modifier, not a bound
            if ov is not None and not isinstance(ov, (int, float)):
                continue
            if nv is not None and not isinstance(nv, (int, float)):
                continue
            lower = key in _LOWER_BOUNDS
            if ov is None:
                stricter = True
            elif nv is None:
                stricter = False
            else:
                stricter = nv > ov if lower else nv < ov
            sev = BREAKING if (direction == "request" and stricter) else NON_BREAKING
            self.add(sev, "constraint_narrowed" if stricter else "constraint_widened", f"{loc}@{key}",
                     f"{key} changed from {ov} to {nv}", ov, nv)

    def compare_properties(self, old, new, direction, loc, depth) -> None:
        op_ = old.get("properties") if isinstance(old.get("properties"), dict) else {}
        np_ = new.get("properties") if isinstance(new.get("properties"), dict) else {}
        if not op_ and not np_:
            return
        oreq = set(old.get("required") or []) if isinstance(old.get("required"), list) else set()
        nreq = set(new.get("required") or []) if isinstance(new.get("required"), list) else set()
        # A client never sends readOnly properties and never receives
        # writeOnly ones, so those do not count in that direction.
        skip = "readOnly" if direction == "request" else "writeOnly"

        def flag(spec, ps) -> bool:
            target, _, _ = deref(spec, ps)
            return isinstance(target, dict) and target.get(skip) is True

        closed = new.get("additionalProperties") is False
        for name in sorted(set(op_) | set(np_)):
            ploc = f"{loc}.{name}"
            in_old, in_new = name in op_, name in np_
            if in_new and flag(self.new, np_[name]) or (in_old and not in_new and flag(self.old, op_[name])):
                continue
            if direction == "request":
                if in_new and not in_old:
                    if name in nreq:
                        self.add(BREAKING, "request_property_required_added", ploc,
                                 f"new required request property '{name}'")
                    else:
                        self.add(NON_BREAKING, "request_property_added", ploc,
                                 f"new optional request property '{name}'")
                    continue
                if in_old and not in_new:
                    self.add(BREAKING if closed else NON_BREAKING, "request_property_removed", ploc,
                             f"request property '{name}' was removed"
                             + (" and additionalProperties is false, so sending it is now an error" if closed else ""))
                    continue
                if name in nreq and name not in oreq:
                    self.add(BREAKING, "request_property_became_required", ploc,
                             f"request property '{name}' became required")
                elif name in oreq and name not in nreq:
                    self.add(NON_BREAKING, "request_property_became_optional", ploc,
                             f"request property '{name}' became optional")
            else:
                if in_old and not in_new:
                    self.add(BREAKING, "response_property_removed", ploc,
                             f"response property '{name}' was removed")
                    continue
                if in_new and not in_old:
                    self.add(NON_BREAKING, "response_property_added", ploc,
                             f"response property '{name}' was added")
                    continue
                if name in oreq and name not in nreq:
                    self.add(BREAKING, "response_property_became_optional", ploc,
                             f"response property '{name}' is no longer required (clients may not receive it)")
                elif name in nreq and name not in oreq:
                    self.add(NON_BREAKING, "response_property_became_required", ploc,
                             f"response property '{name}' became required")
            self.compare_schema(op_[name], np_[name], direction, ploc, depth + 1)
        if direction == "request" and closed and old.get("additionalProperties") is not False:
            self.add(BREAKING, "additional_properties_disallowed", loc,
                     "the object no longer accepts properties it does not list")


def _swagger2_param_schema(p: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Swagger 2 non-body parameters carry type/format/enum inline."""
    keys = ("type", "format", "enum", "items") + _LOWER_BOUNDS + _UPPER_BOUNDS
    picked = {k: p[k] for k in keys if k in p}
    return picked or None


def _tstr(types: Iterable[str]) -> str:
    return "|".join(sorted(types))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def diff_specs(old: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    """Classified changes from `old` to `new`, breaking first."""
    d = _Differ(old if isinstance(old, dict) else {}, new if isinstance(new, dict) else {})
    d.run()
    changes = sorted(d.changes, key=lambda c: (c.severity != BREAKING, c.method == "", c.path, c.method, c.location))
    return _summary(changes, d.truncated)


def _summary(changes: List[Change], truncated: bool) -> Dict[str, Any]:
    breaking = [c for c in changes if c.severity == BREAKING]
    ops = sorted({(c.method, c.path) for c in breaking})
    return {
        "breaking_count": len(breaking),
        "non_breaking_count": len(changes) - len(breaking),
        "breaking_operations": [{"method": m, "path": p} for m, p in ops],
        "truncated": truncated,
        "changes": changes,
    }


# ---------------------------------------------------------------------------
# Linking changes to the tests that reference the operations
# ---------------------------------------------------------------------------

def _find_op_key(spec: Dict[str, Any], ref: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    paths = spec.get("paths") if isinstance(spec.get("paths"), dict) else {}
    op_id = ref.get("operationId") or ref.get("operation_id")
    if op_id:
        for path, item in paths.items():
            if isinstance(item, dict):
                for method, op in item.items():
                    if method.lower() in HTTP_METHODS and isinstance(op, dict) and op.get("operationId") == op_id:
                        return method.upper(), normalise_path(path)
        return None
    method, path = str(ref.get("method") or "").upper(), ref.get("path")
    return (method, normalise_path(path)) if method and path else None


def case_references(steps: Any) -> List[Dict[str, Any]]:
    """Every `expect_schema.openapi` reference in a case's steps."""
    out = []
    for i, step in enumerate(steps or []):
        params = step.get("params") if isinstance(step, dict) else None
        es = params.get("expect_schema") if isinstance(params, dict) else None
        if isinstance(es, dict) and isinstance(es.get("openapi"), dict):
            out.append({"step": i, "ref": es["openapi"],
                        "status": str(es["status"]) if es.get("status") is not None else None})
    return out


def link_cases(result: Dict[str, Any], old: Dict[str, Any], new: Dict[str, Any],
               cases: Iterable[Any]) -> Dict[str, Any]:
    """Attach affected test cases to each change, in place, and promote the
    non-breaking changes that break a test:

    * a removed error status that a test pins with `expect_schema.status`;
    * an `operationId` change, when a test references the OLD operationId
      (its expect_schema no longer resolves, so the step will fail).

    `cases` = objects/dicts with id, name, test_suite_id, steps.
    """
    by_op: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for case in cases:
        get = (lambda k: case.get(k)) if isinstance(case, dict) else (lambda k: getattr(case, k, None))
        for r in case_references(get("steps")):
            key = _find_op_key(old, r["ref"]) or _find_op_key(new, r["ref"])
            if key is None:
                continue
            by_op.setdefault(key, []).append({
                "case_id": get("id"), "name": get("name"), "test_suite_id": get("test_suite_id"),
                "step": r["step"], "status": r["status"],
                "operation_id": r["ref"].get("operationId") or r["ref"].get("operation_id"),
            })
    affected: Dict[Any, Dict[str, Any]] = {}
    changes: List[Change] = result["changes"]
    for c in changes:
        refs = by_op.get((c.method, normalise_path(c.path)), [])
        if not refs:
            continue
        hits = refs
        if c.rule == "response_status_removed" and c.severity == NON_BREAKING:
            pinned = [r for r in refs if r["status"] == c.status]
            if pinned:
                c.severity = BREAKING
                c.promoted_reason = f"a test expects status {c.status}"
            hits = pinned or refs
        elif c.rule == "operation_id_changed":
            pinned = [r for r in refs if r["operation_id"] and r["operation_id"] == c.old]
            if pinned:
                c.severity = BREAKING
                c.promoted_reason = "a test references the old operationId, which no longer resolves"
            hits = pinned or refs
        elif c.status is not None and c.location.startswith("responses/"):
            # Response-schema changes: a test pinned to ANOTHER status is not
            # affected; one with no pin checks the first 2xx, so count it.
            hits = [r for r in refs if r["status"] in (None, c.status)] or []
        seen: Set[Any] = set()
        c.affected_cases = []
        for r in hits:
            if r["case_id"] in seen:
                continue
            seen.add(r["case_id"])
            c.affected_cases.append({"case_id": r["case_id"], "name": r["name"],
                                     "test_suite_id": r["test_suite_id"]})
        if c.severity == BREAKING:
            for r in hits:
                entry = affected.setdefault(r["case_id"], {
                    "case_id": r["case_id"], "name": r["name"], "test_suite_id": r["test_suite_id"],
                    "operations": [], "change_ids": []})
                op = f"{c.method} {c.path}"
                if op not in entry["operations"]:
                    entry["operations"].append(op)
                if c.id not in entry["change_ids"]:
                    entry["change_ids"].append(c.id)
    changes.sort(key=lambda c: (c.severity != BREAKING, c.method == "", c.path, c.method, c.location))
    out = _summary(changes, result.get("truncated", False))
    out["affected_tests"] = sorted(affected.values(), key=lambda e: (e["name"] or "", e["case_id"] or 0))
    return out


def to_json(result: Dict[str, Any]) -> Dict[str, Any]:
    return {**result, "changes": [c.to_dict() for c in result["changes"]]}

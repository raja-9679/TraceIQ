"""`run-test` steps: one test case running another test case's steps.

A step ``{"type": "run-test", "params": {"case_id": 42, "overrides": {...}}}``
is replaced at dispatch by case 42's CURRENT steps (a link, not a copy), so
the worker never sees `run-test` and needs no support for it: the steps run
in the same browser, in place, with the caller's settings, environment,
secrets and data row.

``overrides`` maps a template token to the value it takes inside that one
call — ``{"env.LOGIN_USER": "sub@example.com",
"secret.LOGIN_PASSWORD": "{{secret.SUB_PASSWORD}}"}``. Keys are
``env.NAME``, ``secret.NAME``, ``data.NAME`` or a bare runtime variable
``NAME``; the substitution is textual and single-pass, so a value that is
itself a template resolves on the worker like any other. In nested calls the
innermost override wins (its tokens are already gone when an outer call's
overrides apply).

Rules, checked when a case is saved (`check_calls`) and again at dispatch
(`expand_steps` — the authority, since a called case can change or be
deleted after its caller was saved):

* the called case must be in the caller's project;
* same kind of worker (web ↔ web, mobile ↔ mobile); manual and raw-script
  cases can't be called;
* no loops, at most ``MAX_CALL_DEPTH`` levels, at most ``MAX_EXPANDED_STEPS``
  steps after expansion.

At dispatch a call that breaks a rule becomes a `run-test` step carrying
``params.error``: the worker fails it with that message (an older worker
fails it as an unknown step type), so the case goes red at that point rather
than silently skipping the steps.

Each inlined step carries ``from = {case_id, case_name, call_id, depth}``
(innermost call); the worker copies it into step evidence so the run page can
group the steps under the test they came from.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

RUN_TEST = "run-test"
MAX_CALL_DEPTH = 5
MAX_EXPANDED_STEPS = 1000
MAX_OVERRIDES = 50
MAX_OVERRIDE_VALUE = 2000

_KEY = re.compile(r"^(?:(?:env|secret|data)\.)?[A-Za-z0-9_-]{1,100}$")
_WEB = {None, "ui_playwright", "api"}
_MOBILE = {"mobile_appium"}
_CACHE_KEY = "traceiq.case_calls"


class CaseCallError(ValueError):
    """A `run-test` step that cannot run. The message is shown to users."""


def _executor(case: Any) -> Optional[str]:
    ex = getattr(case, "executor", None)
    return getattr(ex, "value", ex)


def _family(executor: Optional[str]) -> Optional[str]:
    if executor in _WEB:
        return "web"
    if executor in _MOBILE:
        return "mobile"
    return None


def _target(step: Any) -> Any:
    params = step.get("params") if isinstance(step, dict) else None
    return params.get("case_id") if isinstance(params, dict) else None


def _target_id(step: Any) -> Optional[int]:
    raw = _target(step)
    if isinstance(raw, bool):
        return None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def is_call(step: Any) -> bool:
    return isinstance(step, dict) and step.get("type") == RUN_TEST


def call_targets(steps: Iterable[Any]) -> List[int]:
    """Case ids named by the `run-test` steps in `steps`, in order."""
    out = []
    for s in steps or []:
        if is_call(s):
            t = _target_id(s)
            if t is not None:
                out.append(t)
    return out


def validate_overrides(raw: Any) -> Dict[str, str]:
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict):
        raise CaseCallError("overrides must be an object of token → value")
    if len(raw) > MAX_OVERRIDES:
        raise CaseCallError(f"at most {MAX_OVERRIDES} overrides per step")
    out: Dict[str, str] = {}
    for key, value in raw.items():
        k = str(key).strip()
        if k.startswith("{{") and k.endswith("}}"):
            k = k[2:-2].strip()
        if not _KEY.match(k):
            raise CaseCallError(
                f"override '{key}' must look like env.NAME, secret.NAME, data.NAME or NAME")
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise CaseCallError(f"override '{k}' must be text")
        v = str(value)
        if len(v) > MAX_OVERRIDE_VALUE:
            raise CaseCallError(f"override '{k}' is longer than {MAX_OVERRIDE_VALUE} characters")
        out[k] = v
    return out


def _substitute(val: Any, pattern: Optional[re.Pattern], overrides: Dict[str, str]) -> Any:
    if pattern is None:
        return val
    if isinstance(val, str):
        return pattern.sub(lambda m: overrides[m.group(1)], val)
    if isinstance(val, list):
        return [_substitute(v, pattern, overrides) for v in val]
    if isinstance(val, dict):
        return {k: (v if k in ("id", "type", "from") else _substitute(v, pattern, overrides))
                for k, v in val.items()}
    return val


def _override_pattern(overrides: Dict[str, str]) -> Optional[re.Pattern]:
    if not overrides:
        return None
    alts = "|".join(re.escape(k) for k in sorted(overrides, key=len, reverse=True))
    return re.compile(r"\{\{\s*(" + alts + r")\s*\}\}")


# ---------------------------------------------------------------------------
# Save-time check
# ---------------------------------------------------------------------------

def _graph_problem(case_id: Optional[int], project_id: Optional[int], executor: Optional[str],
                   steps: List[Any], cases: Dict[int, Any]) -> Optional[str]:
    """First rule `steps` break, given every reachable case in `cases`."""
    family = _family(executor)
    for s in steps or []:
        if is_call(s):
            if _target_id(s) is None:
                return "Pick the test to run in every 'Run Test' step"
            try:
                validate_overrides((s.get("params") or {}).get("overrides"))
            except CaseCallError as exc:
                return str(exc)
    if call_targets(steps) and family is None:
        return "Only web and mobile tests can run another test"

    def walk(cid: int, path: Tuple[int, ...]) -> Optional[str]:
        target = cases.get(cid)
        if target is None or getattr(target, "project_id", None) != project_id:
            return f"Test #{cid} does not exist in this project"
        name = getattr(target, "name", f"#{cid}")
        if cid in path:
            return f"'{name}' (test #{cid}) would end up running itself"
        if len(path) > MAX_CALL_DEPTH:
            return f"Tests can run other tests at most {MAX_CALL_DEPTH} levels deep"
        if _family(_executor(target)) != family:
            return (f"'{name}' (test #{cid}) runs on a different kind of worker "
                    f"({_executor(target) or 'ui_playwright'}) and can't be used here")
        for t in call_targets(getattr(target, "steps", None) or []):
            problem = walk(t, path + (cid,))
            if problem:
                return problem
        return None

    root: Tuple[int, ...] = (case_id,) if case_id else ()
    for t in call_targets(steps):
        problem = walk(t, root)
        if problem:
            return problem
    return None


async def check_calls_async(session, case_id: Optional[int], project_id: Optional[int],
                            executor: Optional[str], steps: List[Any]) -> None:
    """Raise CaseCallError if saving `steps` on this case would break a rule."""
    if not call_targets(steps):
        for s in steps or []:
            if is_call(s):
                raise CaseCallError("Pick the test to run in every 'Run Test' step")
        return
    from app.models import TestCase
    cases: Dict[int, Any] = {}
    todo = set(call_targets(steps))
    for _ in range(MAX_CALL_DEPTH + 1):
        todo -= set(cases)
        if not todo:
            break
        for cid in list(todo):
            c = await session.get(TestCase, cid)
            if c is not None:
                cases[cid] = c
        todo = {t for c in cases.values() for t in call_targets(c.steps or [])}
    problem = _graph_problem(case_id, project_id, executor, steps, cases)
    if problem:
        raise CaseCallError(problem)


# ---------------------------------------------------------------------------
# Dispatch-time expansion
# ---------------------------------------------------------------------------

def _failed_call(step: Dict[str, Any], message: str, origin: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    out = {"id": step.get("id"), "type": RUN_TEST,
           "params": {"case_id": _target(step), "error": message}}
    if origin:
        out["from"] = origin
    return out


def expand_steps(steps: List[Any], *, case_id: Optional[int], project_id: Optional[int],
                 executor: Optional[str], load: Callable[[int], Any]) -> List[Any]:
    """`steps` with every `run-test` step replaced by the called case's steps.

    `load(case_id)` returns the case or None. Steps without calls come back
    unchanged (same list object), so the common case costs one scan."""
    if not any(is_call(s) for s in steps or []):
        return steps
    family = _family(executor)
    budget = [MAX_EXPANDED_STEPS]

    def expand(items: List[Any], path: Tuple[int, ...], origin: Optional[Dict[str, Any]]) -> List[Any]:
        out: List[Any] = []
        for step in items:
            if budget[0] <= 0:
                break
            if not is_call(step):
                out.append(step)
                budget[0] -= 1
                continue
            out.extend(expand_call(step, path, origin))
        return out

    def expand_call(step: Dict[str, Any], path: Tuple[int, ...], origin) -> List[Any]:
        cid = _target_id(step)
        if cid is None:
            budget[0] -= 1
            return [_failed_call(step, "No test picked in this 'Run Test' step", origin)]
        target = load(cid)
        fail = None
        if target is None or getattr(target, "project_id", None) != project_id:
            fail = f"Test #{cid} no longer exists in this project"
        elif cid in path:
            fail = f"'{target.name}' (test #{cid}) would run itself — remove the loop"
        elif len(path) > MAX_CALL_DEPTH:
            fail = f"Tests can run other tests at most {MAX_CALL_DEPTH} levels deep"
        elif family is None or _family(_executor(target)) != family:
            fail = f"'{target.name}' (test #{cid}) runs on a different kind of worker"
        if fail is None:
            try:
                overrides = validate_overrides((step.get("params") or {}).get("overrides"))
            except CaseCallError as exc:
                fail = str(exc)
        if fail is not None:
            budget[0] -= 1
            return [_failed_call(step, fail, origin)]
        here = {"case_id": cid, "case_name": str(target.name)[:200],
                "call_id": str(step.get("id") or "")[:100], "depth": len(path)}
        inner = expand([copy.deepcopy(s) for s in (target.steps or []) if isinstance(s, dict)],
                       path + (cid,), here)
        pattern = _override_pattern(overrides)
        result = []
        for s in inner:
            s = _substitute(s, pattern, overrides)
            s.setdefault("from", here)
            result.append(s)
        if not inner:
            budget[0] -= 1
            result.append(_failed_call(step, f"'{target.name}' (test #{cid}) has no steps", origin))
        return result

    root: Tuple[int, ...] = (case_id,) if case_id else ()
    out = expand(list(steps), root, None)
    if budget[0] <= 0:
        out.append({"id": "run-test-limit", "type": RUN_TEST,
                    "params": {"error": f"More than {MAX_EXPANDED_STEPS} steps after running other tests"}})
    return out


def loader_sync(session) -> Callable[[int], Any]:
    """`load` for `expand_steps`, cached on the session for one dispatch."""
    from app.models import TestCase
    cache = session.info.setdefault(_CACHE_KEY, {})

    def load(cid: int):
        if cid not in cache:
            cache[cid] = session.get(TestCase, cid)
        return cache[cid]
    return load


async def loader_async(session, steps: List[Any]) -> Callable[[int], Any]:
    """`load` for `expand_steps` from an async session: every case reachable
    from `steps` is fetched first (bounded by the depth limit)."""
    from app.models import TestCase
    cases: Dict[int, Any] = {}
    todo = set(call_targets(steps))
    for _ in range(MAX_CALL_DEPTH + 2):
        todo -= set(cases)
        if not todo:
            break
        for cid in sorted(todo):
            cases[cid] = await session.get(TestCase, cid)
        todo = {t for c in cases.values() if c is not None for t in call_targets(c.steps or [])}
    return cases.get


def owner_of_step(session, case: Any, step_id: str) -> Optional[int]:
    """The case whose own steps hold `step_id`: `case` itself, else a case it
    runs (directly or nested). Heal proposals must target that case — the
    caller has no such step to fix."""
    if not step_id or case is None:
        return getattr(case, "id", None)
    load = loader_sync(session)
    seen = set()
    todo = [case]
    while todo:
        c = todo.pop(0)
        if c is None or c.id in seen or len(seen) > 50:
            continue
        seen.add(c.id)
        steps = c.steps or []
        if any(isinstance(s, dict) and s.get("id") == step_id for s in steps):
            return c.id
        for t in call_targets(steps):
            target = load(t)
            if target is not None and target.project_id == case.project_id:
                todo.append(target)
    return case.id


def callers_of(cases: Iterable[Any], case_id: int) -> List[Any]:
    """Cases among `cases` with a `run-test` step naming `case_id` directly."""
    return [c for c in cases if case_id in call_targets(getattr(c, "steps", None) or [])]

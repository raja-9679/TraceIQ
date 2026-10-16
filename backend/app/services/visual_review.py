"""Visual review (WP14e): element keys, stored comparisons, change grouping.

* `element_key` — twin of execution-engine/src/core/visual-element-key.ts.
  An element baseline (selector screenshot) is keyed apart from the
  full-page baseline of the same step; '' is the full-page key.
* `normalise_results` / `persist_comparisons` — the worker reports each
  `expect-visual-match` outcome on the job result as `visual_results`; the
  aggregator stores one VisualComparison per entry. Like page budgets, the
  list can come from a local worker (any API-key holder), so it is rebuilt
  field by field, capped and redacted — never stored as sent. Candidate and
  diff keys are taken only from the job's own uploaded screenshots.
* `group_comparisons` — related changes within a run: a shared header edit
  shows up in many cases' diffs with the same size and the same crop hashes.
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence

from app.services.redaction import redact_text

ELEMENT_KEY_RE = re.compile(r"^el-[0-9a-f]{12}$")
_HASH_RE = re.compile(r"^[0-9a-f]{16}$")
_FILE_RE = re.compile(r"^visual-[A-Za-z0-9._-]{1,200}\.png$")
_STATUSES = {"passed", "failed", "capture_only"}
MAX_RESULTS_PER_CASE = 50
MAX_REGIONS = 20


# ---------------------------------------------------------------------------
# Element key
# ---------------------------------------------------------------------------

def normalise_selector(selector: Optional[str]) -> str:
    """Collapse whitespace and drop it around `>` / `,` — outside quotes only.
    Must match normaliseSelector() in visual-element-key.ts exactly."""
    src = (selector or "").strip()
    out: List[str] = []
    quote: Optional[str] = None
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(src[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch.isspace():
            while i + 1 < n and src[i + 1].isspace():
                i += 1
            nxt = src[i + 1] if i + 1 < n else ""
            prev = out[-1] if out else ""
            if nxt in (">", ",") or prev in (">", ","):
                i += 1
                continue
            out.append(" ")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def element_key(selector: Optional[str]) -> str:
    norm = normalise_selector(selector)
    if not norm:
        return ""
    return "el-" + hashlib.sha256(norm.encode("utf-8")).hexdigest()[:12]


def valid_element_key(key: Optional[str]) -> bool:
    return key == "" or bool(key and ELEMENT_KEY_RE.match(key))


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

def _int(v: Any, lo: int = 0, hi: int = 10**9) -> Optional[int]:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return max(lo, min(hi, int(v)))


def _ratio(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return round(max(0.0, min(1.0, float(v))), 6)


def _uploaded_key(file_name: Any, keys: Sequence[str], run_id: int) -> Optional[str]:
    """The uploaded object for a local file name: workers upload screenshots
    as runs/{run}/screenshots/{job_id}-{file}. Only this run's keys count."""
    if not isinstance(file_name, str) or not _FILE_RE.match(file_name):
        return None
    prefix = f"runs/{run_id}/screenshots/"
    for key in keys or []:
        if (isinstance(key, str) and key.startswith(prefix)
                and (key.endswith("-" + file_name) or key == prefix + file_name)):
            return key
    return None


def _regions(raw: Any) -> List[Dict[str, Any]]:
    out = []
    for r in (raw if isinstance(raw, list) else [])[:MAX_REGIONS]:
        if not isinstance(r, dict):
            continue
        box = {k: _int(r.get(k)) for k in ("x", "y", "width", "height", "pixels")}
        if any(v is None for v in box.values()) or not box["width"] or not box["height"]:
            continue
        for h in ("hash_candidate", "hash_baseline"):
            v = r.get(h)
            if isinstance(v, str) and _HASH_RE.match(v):
                box[h] = v
        out.append(box)
    return out


def normalise_results(raw: Any, screenshot_keys: Sequence[str], run_id: int) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw[:MAX_RESULTS_PER_CASE]:
        if not isinstance(item, dict) or not isinstance(item.get("step_id"), str) or not item["step_id"]:
            continue
        key = item.get("element_key") or ""
        if not valid_element_key(key):
            # Not knowing which slot a candidate belongs to is worse than not
            # storing it: it could be promoted over the wrong baseline.
            continue
        cand_file = item.get("candidate_file")
        # The file name must be the one the worker writes for the key it
        # claims (visual-<step>.png / visual-<step>-<key>.png).
        step = item["step_id"]
        if cand_file != (f"visual-{step}-{key}.png" if key else f"visual-{step}.png"):
            continue
        status = item.get("status") if item.get("status") in _STATUSES else "capture_only"
        selector = item.get("selector") if key else None
        device = item.get("device")
        out.append({
            "step_id": item["step_id"][:200],
            "browser": str(item.get("browser") or "")[:50] or None,
            "device": str(device)[:200] if isinstance(device, str) and device else None,
            "element_key": key,
            "selector": redact_text(str(selector)[:500]) if selector else None,
            "status": status,
            "baseline_id": _int(item.get("baseline_id"), 1),
            "candidate_key": _uploaded_key(cand_file, screenshot_keys, run_id),
            "diff_key": _uploaded_key(item.get("diff_file"), screenshot_keys, run_id),
            "diff_ratio": _ratio(item.get("diff_ratio")),
            "diff_pixels": _int(item.get("diff_pixels")),
            "total_pixels": _int(item.get("total_pixels")),
            "width": _int(item.get("width")),
            "height": _int(item.get("height")),
            "size_mismatch": bool(item.get("size_mismatch")),
            "tolerance": _ratio(item.get("tolerance")),
            "regions": _regions(item.get("regions")) if status != "capture_only" else [],
        })
    return out


def persist_comparisons(session, run, test_case_id: Optional[int], test_name: Optional[str],
                        raw: Any, screenshot_keys: Sequence[str]) -> int:
    """Replace this (run, test) pair's comparisons (sync session; caller
    commits). Replace, not append: a redelivered result must count once.
    Comments on replaced rows would dangle, so rows that carry comments are
    kept and only updated in place when the slot matches."""
    from sqlmodel import select
    from app.models import VisualComparison, VisualReviewComment

    if not run or not run.project_id:
        return 0
    entries = normalise_results(raw, screenshot_keys, run.id)
    existing = session.exec(select(VisualComparison).where(
        VisualComparison.test_run_id == run.id,
        VisualComparison.test_name == test_name)).all()
    commented = set(session.exec(select(VisualReviewComment.comparison_id).where(
        VisualReviewComment.comparison_id.in_([c.id for c in existing] or [-1]))).all())
    by_slot = {(c.step_id, c.element_key): c for c in existing}
    kept = set()
    for e in entries:
        # The slot the worker resolved against; older entries fall back to
        # the run's own browser/device.
        browser = e.pop("browser") or run.browser or "chromium"
        device = e.pop("device") or run.device
        row = by_slot.get((e["step_id"], e["element_key"]))
        if row is None:
            row = VisualComparison(project_id=run.project_id, test_run_id=run.id,
                                   test_case_id=int(test_case_id) if test_case_id else None,
                                   test_name=test_name, browser=browser, device=device,
                                   step_id=e["step_id"])
        for k, v in e.items():
            setattr(row, k, v)
        row.browser, row.device = browser, device
        session.add(row)
        if row.id:
            kept.add(row.id)
    for c in existing:
        if c.id not in kept and c.id not in commented:
            session.delete(c)
    return len(entries)


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------

HASH_DISTANCE = 10        # of 64 bits
SIZE_SLACK = 1.25         # each side within 25 % (+8 px)
POSITION_SLACK = 24       # px


def _hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def _degenerate(h: Optional[str]) -> bool:
    """A flat crop (solid colour change) hashes to almost all 0s or 1s, and
    then any two flat crops look alike — position has to agree too."""
    if not h:
        return True
    ones = bin(int(h, 16)).count("1")
    return ones <= 3 or ones >= 61


def _size_close(a: dict, b: dict) -> bool:
    for dim in ("width", "height"):
        lo, hi = sorted((a[dim], b[dim]))
        if hi > lo * SIZE_SLACK + 8:
            return False
    return True


def _position_close(a: dict, b: dict) -> bool:
    return abs(a["x"] - b["x"]) <= POSITION_SLACK and abs(a["y"] - b["y"]) <= POSITION_SLACK


def regions_match(a: dict, b: dict) -> bool:
    """Same underlying change: same size, and the crops look the same before
    AND after (both hashes close). Without hashes, or for flat crops, the
    boxes must also sit in the same place."""
    if not _size_close(a, b):
        return False
    hashes = all(r.get(h) for r in (a, b) for h in ("hash_candidate", "hash_baseline"))
    if not hashes:
        return _position_close(a, b)
    if (_hamming(a["hash_candidate"], b["hash_candidate"]) > HASH_DISTANCE
            or _hamming(a["hash_baseline"], b["hash_baseline"]) > HASH_DISTANCE):
        return False
    if _degenerate(a["hash_candidate"]) and _degenerate(a["hash_baseline"]):
        return _position_close(a, b)
    return True


def is_change(item: dict) -> bool:
    return bool(item.get("regions")) and (item.get("diff_pixels") or 0) > 0


def _related(a: dict, b: dict) -> bool:
    ra, rb = a["regions"], b["regions"]
    # The dominant change of one shows up somewhere in the other.
    return any(regions_match(ra[0], r) for r in rb) or any(regions_match(rb[0], r) for r in ra)


def group_key_for(anchor_id: int) -> str:
    return f"g{anchor_id}"


def group_comparisons(items: Iterable[dict]) -> List[Dict[str, Any]]:
    """Union-find over changed items. Each group is keyed by its lowest
    comparison id (`g<id>`) so a group's comments stay attached as long as
    that comparison exists. Unchanged / capture-only items are not grouped.
    Groups come largest first, then by key."""
    changed = sorted((i for i in items if is_change(i)), key=lambda i: i["id"])
    parent = {i["id"]: i["id"] for i in changed}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for idx, a in enumerate(changed):
        for b in changed[idx + 1:]:
            if find(a["id"]) != find(b["id"]) and _related(a, b):
                ra, rb = find(a["id"]), find(b["id"])
                parent[max(ra, rb)] = min(ra, rb)

    groups: Dict[int, List[dict]] = {}
    for i in changed:
        groups.setdefault(find(i["id"]), []).append(i)
    out = []
    for anchor, members in groups.items():
        anchor_id = min(m["id"] for m in members)
        first = next(m for m in members if m["id"] == anchor_id)
        out.append({
            "key": group_key_for(anchor_id),
            "item_ids": [m["id"] for m in members],
            "size": len(members),
            "case_count": len({m.get("test_case_id") for m in members}),
            "region": {k: first["regions"][0][k] for k in ("x", "y", "width", "height")},
            "max_diff_ratio": max((m.get("diff_ratio") or 0) for m in members),
        })
    out.sort(key=lambda g: (-g["size"], int(g["key"][1:])))
    return out

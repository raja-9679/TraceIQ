"""Component (Storybook) targets (WP10).

Reads a Storybook's story index — `index.json` (Storybook 7+, `entries`) or
the older `stories.json` (6.x, `stories`) — and turns picked stories into
ordinary test cases: open the story's isolated iframe
(`iframe.html?id=<story>&viewMode=story`), wait for it to render, then a
visual match and/or an axe accessibility check. The visual step's id is
derived from the story id, so the component baseline is "the baseline of
story X" (VisualBaseline is keyed by case + step id) and survives renaming
the case.

Pure; fetching happens in the API through net_guard.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlsplit, urlunsplit

MAX_STORIES = 2000
MAX_CASES_PER_REQUEST = 200
_STORY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]*--[a-z0-9][a-z0-9_\-]*$", re.I)


class StorybookError(ValueError):
    pass


def base_url(url: str) -> str:
    """The Storybook root for a URL a user pasted: strip index.html,
    iframe.html, ?path=/story/... and a trailing slash."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise StorybookError("Storybook URL must be an absolute http(s) URL")
    if parts.username or parts.password:
        raise StorybookError("Storybook URL must not embed credentials")
    path = re.sub(r"/(index|iframe)\.html$", "", parts.path or "").rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def index_urls(root: str) -> List[str]:
    return [f"{root}/index.json", f"{root}/stories.json"]


def parse_index(data: Any) -> List[Dict[str, Any]]:
    """Stories (not docs pages) from an index.json / stories.json document."""
    if not isinstance(data, dict):
        raise StorybookError("story index is not a JSON object")
    raw = data.get("entries") if isinstance(data.get("entries"), dict) else data.get("stories")
    if not isinstance(raw, dict):
        raise StorybookError("story index has neither 'entries' (v7+) nor 'stories' (v6)")
    out = []
    for key, entry in list(raw.items())[:MAX_STORIES]:
        if not isinstance(entry, dict):
            continue
        sid = str(entry.get("id") or key)
        if not _STORY_ID_RE.match(sid):
            continue
        kind = entry.get("type") or "story"
        if kind != "story":
            continue
        params = entry.get("parameters") if isinstance(entry.get("parameters"), dict) else {}
        if params.get("docsOnly"):
            continue
        title = str(entry.get("title") or entry.get("kind") or "")[:200]
        name = str(entry.get("name") or entry.get("story") or sid)[:200]
        tags = [str(t)[:40] for t in (entry.get("tags") or []) if isinstance(t, str)][:10]
        out.append({"id": sid, "title": title, "name": name, "tags": tags})
    out.sort(key=lambda s: (s["title"], s["name"]))
    return out


def story_url(root: str, story_id: str) -> str:
    return f"{root}/iframe.html?id={quote(story_id, safe='-_')}&viewMode=story"


def visual_step_id(story_id: str) -> str:
    return f"story-{story_id}"[:100]


def case_name(story: Dict[str, Any]) -> str:
    return f"Story: {story['title']} / {story['name']}"[:250]


def build_steps(root: str, story_id: str, *, visual: bool, a11y: bool,
                a11y_impact: str = "serious") -> List[Dict[str, Any]]:
    if not _STORY_ID_RE.match(story_id):
        raise StorybookError(f"invalid story id {story_id!r}")
    if not (visual or a11y):
        raise StorybookError("pick at least one check (visual or accessibility)")
    sid = re.sub(r"[^a-z0-9_-]", "-", story_id.lower())
    steps: List[Dict[str, Any]] = [
        {"id": f"{sid}-open", "type": "goto", "value": story_url(root, story_id)},
        # SB7+ renders into #storybook-root, SB6 into #root.
        {"id": f"{sid}-ready", "type": "wait-for-selector",
         "selector": "#storybook-root > *, #root > *"},
    ]
    if visual:
        steps.append({"id": visual_step_id(story_id), "type": "expect-visual-match"})
    if a11y:
        impact = a11y_impact if a11y_impact in ("minor", "moderate", "serious", "critical") else "serious"
        steps.append({"id": f"{sid}-a11y", "type": "check-accessibility",
                      "params": {"impact": impact}})
    return steps


def pick(stories: List[Dict[str, Any]], story_ids: List[str]) -> List[Dict[str, Any]]:
    by_id = {s["id"]: s for s in stories}
    missing = [sid for sid in story_ids if sid not in by_id]
    if missing:
        raise StorybookError(f"not in this Storybook's index: {missing[:10]}")
    if len(story_ids) > MAX_CASES_PER_REQUEST:
        raise StorybookError(f"at most {MAX_CASES_PER_REQUEST} stories per request")
    seen, out = set(), []
    for sid in story_ids:
        if sid not in seen:
            seen.add(sid)
            out.append(by_id[sid])
    return out


def find_existing(cases: List[Any], story_id: str) -> Optional[Any]:
    """A case in the suite already generated for this story (its open step
    points at the story's iframe)."""
    needle = f"iframe.html?id={quote(story_id, safe='-_')}&"
    for case in cases:
        for step in (case.steps or [])[:1]:
            if isinstance(step, dict) and step.get("type") == "goto" and needle in str(step.get("value") or ""):
                return case
    return None

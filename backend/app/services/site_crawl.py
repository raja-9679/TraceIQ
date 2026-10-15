"""Broken-link & asset crawler (WP10): config validation, job, result ingestion.

Flow: `POST /api/site-crawls/{id}/run` (or the beat sweep for scheduled
crawls) creates a SiteCrawlRun and `dispatch_site_crawl` pushes ONE
`job_type=site_crawl` side job onto `jobs:pending`. The worker
(execution-engine/src/core/frontend-quality/crawler.ts) crawls, checks every
link/asset and parks its report under `site_crawl:result:{run_id}`;
`collect_site_crawl` folds it into SiteCrawlIssue rows.

Scope rules, enforced here and again in the worker:
- the start URL's host must be one the project already declares — an
  environment base URL, a suite `allowed_domains` entry or the security
  allowlist. The crawler is for the customer's own site; without this gate any
  editor could aim a polite-but-persistent crawler at a third party.
- crawling stays on the start origin plus `extra_hosts` (each also a known
  host); other links are only *checked* (one request, status recorded, body
  never kept) and only when `check_external` is on.
- robots.txt is honoured by default and `rate_limit_ms` spaces requests per host.

Pure functions only; the DB/Redis side lives in app/tasks/frontend_quality_tasks.py.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urlsplit

from app.services.redaction import redact_text

RESULT_KEY = "site_crawl:result:{}"
RESULT_TTL_SECONDS = 3600

LIMITS = {
    "max_depth": (0, 10),
    "max_pages": (1, 1000),
    "rate_limit_ms": (50, 10_000),
    "slow_threshold_ms": (100, 60_000),
}
# Wall clock the worker gives one crawl; the collector's deadline is longer.
MAX_DURATION_MS = 15 * 60 * 1000
MAX_ISSUES_STORED = 5000
MAX_PAGES_STORED = 1000
MAX_EXTRA_HOSTS = 10
# One project cannot hold more crawls than this, and a schedule fires at most
# hourly: each run can occupy a worker for MAX_DURATION_MS, so neither may be
# left to the request (security review: resource-cap bypass).
MAX_CRAWLS_PER_PROJECT = 20
# Crawl runs in flight per project. Each can hold a shared worker for up to
# MAX_DURATION_MS, so 20 crawls started at once would starve other tenants.
MAX_ACTIVE_RUNS_PER_PROJECT = 3
# Report entries examined at ingestion. Invalid entries are skipped, so the
# stored cap alone would not bound the work a junk-filled report causes.
MAX_ISSUES_EXAMINED = MAX_ISSUES_STORED * 2
MIN_SCHEDULE_GAP_SECONDS = 3600

CATEGORIES = ("broken", "network", "slow", "redirect_loop", "mixed_content", "redirect_chain", "not_checked")
ELEMENTS = ("a", "img", "script", "link", "css", "page")


class CrawlConfigError(ValueError):
    pass


def _host_of(entry: Any) -> Optional[str]:
    """Host from an allowlist entry: 'example.com', '*.example.com',
    'https://example.com:8443/x' or {'domain': ...}."""
    if isinstance(entry, dict):
        entry = entry.get("domain")
    if not isinstance(entry, str) or not entry.strip():
        return None
    value = entry.strip().lower()
    if "://" in value:
        value = urlsplit(value).hostname or ""
    else:
        value = value.split("/")[0].split(":")[0]
    return value or None


def known_hosts_from(base_urls: Iterable[Optional[str]], suite_settings: Iterable[Optional[dict]],
                     security_allowlist: Iterable[Any]) -> Set[str]:
    """Every host the project has declared it tests."""
    hosts: Set[str] = set()
    for url in base_urls:
        h = _host_of(url) if url else None
        if h:
            hosts.add(h)
    for settings in suite_settings:
        for entry in (settings or {}).get("allowed_domains") or []:
            h = _host_of(entry)
            if h:
                hosts.add(h)
    for entry in security_allowlist or []:
        h = _host_of(entry)
        if h:
            hosts.add(h)
    return hosts


def host_allowed(host: Optional[str], known: Iterable[str]) -> bool:
    if not host:
        return False
    host = host.lower()
    for entry in known:
        if entry.startswith("*.") or entry.startswith("."):
            suffix = entry.lstrip("*")
            if host.endswith(suffix) or host == suffix.lstrip("."):
                return True
        elif host == entry:
            return True
    return False


def validate_config(data: Dict[str, Any], known: Set[str]) -> Dict[str, Any]:
    """Clamp/validate the editable fields. Raises CrawlConfigError."""
    out: Dict[str, Any] = {}
    if "name" in data:
        name = str(data.get("name") or "").strip()
        if not name or len(name) > 200:
            raise CrawlConfigError("name must be 1-200 characters")
        out["name"] = name
    if "start_url" in data:
        url = str(data.get("start_url") or "").strip()
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname or len(url) > 2000:
            raise CrawlConfigError("start_url must be an absolute http(s) URL")
        if parts.username or parts.password:
            raise CrawlConfigError("start_url must not embed credentials")
        if not host_allowed(parts.hostname, known):
            raise CrawlConfigError(
                f"{parts.hostname} is not one of this project's hosts. Add it as an environment "
                "base URL, a suite allowed domain or a security allowlist entry first.")
        out["start_url"] = url
    if "extra_hosts" in data:
        raw = data.get("extra_hosts") or []
        if not isinstance(raw, list) or len(raw) > MAX_EXTRA_HOSTS:
            raise CrawlConfigError(f"extra_hosts must be a list of at most {MAX_EXTRA_HOSTS} hosts")
        hosts = []
        for item in raw:
            h = _host_of(item)
            if not h or not host_allowed(h, known):
                raise CrawlConfigError(f"extra host {item!r} is not one of this project's hosts")
            if h not in hosts:
                hosts.append(h)
        out["extra_hosts"] = hosts
    for key, (lo, hi) in LIMITS.items():
        if key in data and data[key] is not None:
            value = data[key]
            if not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi:
                raise CrawlConfigError(f"{key} must be an integer {lo}-{hi}")
            out[key] = value
    for key in ("respect_robots", "check_external", "is_active"):
        if key in data and data[key] is not None:
            out[key] = bool(data[key])
    if "cron_expression" in data:
        cron = (data.get("cron_expression") or "").strip() or None
        if cron:
            from croniter import croniter
            if not croniter.is_valid(cron):
                raise CrawlConfigError("cron_expression is not a valid cron expression")
            from datetime import datetime
            it = croniter(cron, datetime(2026, 1, 1))
            first, second = it.get_next(datetime), it.get_next(datetime)
            if (second - first).total_seconds() < MIN_SCHEDULE_GAP_SECONDS:
                raise CrawlConfigError("scheduled crawls may run at most hourly")
        out["cron_expression"] = cron
    return out


_DEFAULTS = {"max_depth": 3, "max_pages": 100, "rate_limit_ms": 250, "slow_threshold_ms": 3000}


def _clamp_limit(key: str, value: Any) -> int:
    lo, hi = LIMITS[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _DEFAULTS[key]
    return int(max(lo, min(hi, value)))


def snapshot(crawl) -> Dict[str, Any]:
    """The config one run executes with (stored on the run, sent to the worker)."""
    start_host = urlsplit(crawl.start_url).hostname
    hosts = [start_host] + [h for h in (crawl.extra_hosts or []) if h != start_host]
    return {
        "start_url": crawl.start_url,
        "crawl_hosts": hosts[:MAX_EXTRA_HOSTS + 1],
        # Clamped again here, not only at validation: a row written before a
        # limit existed (or edited in the database) must not reach a worker.
        "max_depth": _clamp_limit("max_depth", crawl.max_depth),
        "max_pages": _clamp_limit("max_pages", crawl.max_pages),
        "respect_robots": crawl.respect_robots,
        "rate_limit_ms": _clamp_limit("rate_limit_ms", crawl.rate_limit_ms),
        "slow_threshold_ms": _clamp_limit("slow_threshold_ms", crawl.slow_threshold_ms),
        "check_external": crawl.check_external,
        "max_duration_ms": MAX_DURATION_MS,
    }


def build_job(run_id: int, config: Dict[str, Any],
              data_policy: Optional[Dict[str, Any]] = None,
              allow_private: Optional[bool] = None) -> Dict[str, Any]:
    if allow_private is None:
        from app.services.instance_settings import effective
        allow_private = bool(effective("ALLOW_PRIVATE_NETWORK_TARGETS"))
    # The worker checks every URL it finds in page content (and every redirect
    # hop) before requesting it: link-local/metadata never, private/loopback
    # only on the crawl's own hosts unless the instance allows private targets.
    config = {**config, "allow_private": bool(allow_private)}
    return {
        # The project's redaction rules, so the worker scrubs URLs with them.
        "data_policy": data_policy or {},
        "job_id": str(uuid.uuid4()),
        "job_type": "site_crawl",
        "run_id": 0,
        "crawl_run_id": run_id,
        "result_key": RESULT_KEY.format(run_id),
        "browser": "chromium",
        "config": config,
    }


def _int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value < 0:  # NaN / negative
        return None
    return int(value)


def _url(value: Any, limit: int = 2000) -> Optional[str]:
    if not isinstance(value, str) or not value:
        return None
    return redact_text(value[:limit])


def normalise_result(raw: Any) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """(run fields, issue rows) from a worker report. Nothing is stored as
    sent: the report crosses Redis from a worker, so it is re-typed, capped
    and redacted (URLs carry tokens — signed asset URLs, reset links)."""
    if not isinstance(raw, dict):
        return {"status": "error", "error": "malformed crawl result"}, []
    fields: Dict[str, Any] = {
        "status": "completed" if raw.get("status") == "ok" else "error",
        "error": redact_text(str(raw.get("error"))[:1000]) if raw.get("error") else None,
        "truncated": bool(raw.get("truncated")),
        "links_checked": _int(raw.get("links_checked")) or 0,
    }
    pages = []
    for p in (raw.get("pages") or [])[:MAX_PAGES_STORED] if isinstance(raw.get("pages"), list) else []:
        if not isinstance(p, dict) or not _url(p.get("url")):
            continue
        pages.append({
            "url": _url(p.get("url")),
            "depth": _int(p.get("depth")) or 0,
            "status": _int(p.get("status")),
            "load_ms": _int(p.get("load_ms")),
            "links": _int(p.get("links")) or 0,
            "issues": _int(p.get("issues")) or 0,
            "error": redact_text(str(p["error"])[:300]) if p.get("error") else None,
        })
    fields["pages"] = pages
    fields["pages_crawled"] = len(pages)
    robots = raw.get("robots") if isinstance(raw.get("robots"), dict) else {}
    fields["robots"] = {
        "respected": bool(robots.get("respected")),
        "fetched": bool(robots.get("fetched")),
        "skipped": _int(robots.get("skipped")) or 0,
        "crawl_delay_ms": _int(robots.get("crawl_delay_ms")),
    }
    issues: List[Dict[str, Any]] = []
    raw_issues = raw.get("issues") if isinstance(raw.get("issues"), list) else []
    if len(raw_issues) > MAX_ISSUES_EXAMINED:
        fields["truncated"] = True
    for item in raw_issues[:MAX_ISSUES_EXAMINED]:
        if len(issues) >= MAX_ISSUES_STORED:
            fields["truncated"] = True
            break
        if not isinstance(item, dict):
            continue
        category, element = item.get("category"), item.get("element")
        page_url, target = _url(item.get("page_url")), _url(item.get("target_url"))
        if category not in CATEGORIES or element not in ELEMENTS or not page_url or not target:
            continue
        chain = []
        for hop in (item.get("redirect_chain") or [])[:12] if isinstance(item.get("redirect_chain"), list) else []:
            if isinstance(hop, dict) and _url(hop.get("url")):
                chain.append({"url": _url(hop.get("url")), "status": _int(hop.get("status"))})
        issues.append({
            "page_url": page_url, "target_url": target,
            "element": element, "category": category,
            "status_code": _int(item.get("status_code")),
            "duration_ms": _int(item.get("duration_ms")),
            "redirect_chain": chain or None,
            "detail": redact_text(str(item["detail"])[:500]) if item.get("detail") else None,
        })
    counts: Dict[str, int] = {c: 0 for c in CATEGORIES}
    for i in issues:
        counts[i["category"]] += 1
    fields["issue_counts"] = counts
    return fields, issues

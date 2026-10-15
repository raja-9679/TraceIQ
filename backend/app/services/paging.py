"""On-call paging: PagerDuty Events API v2 and Opsgenie Alerts API (WP8).

Two sources page:

* **Monitors** — on the same DOWN / RECOVERY transitions that already drive
  the Slack/Teams/email alerts (monitor_tasks), per region for multi-region
  monitors. Dedup key `traceiq:monitor:<schedule>[:<region>]`.
* **Run failures** — opt-in per channel (`on_run_failure`). A failed run
  triggers `traceiq:run:<project>:<suite>[:<case>]`; the next passing run of
  the same target resolves it. Without the resolve half, every failure would
  leave a page open that a human has to close by hand.

Dedup keys are the whole point of both APIs (PagerDuty `dedup_key`, Opsgenie
`alias`): a monitor that stays down re-triggers into the SAME incident, and
the recovery closes exactly that incident.

**Maintenance windows suppress, they do not drop.** A suppressed decision is
still written to `PagingEvent` (outcome `suppressed`), so "why did nobody get
paged at 02:00" has an answer. A resolve is only sent for a key whose last
*sent* action was a trigger: resolving something that was never opened
(because it was suppressed) would be harmless noise to PagerDuty but shows up
as a stray "closed" alert in Opsgenie.

Vendor endpoints are fixed hosts, not user input, so they do not go through
net_guard. The routing key / API key never appears in a log line or a
PagingEvent row.
"""
from __future__ import annotations

import logging
from datetime import datetime, time as dtime, timedelta
from typing import Any, Dict, Iterable, List, Optional

import requests

logger = logging.getLogger(__name__)

PAGERDUTY_EVENTS_URL = "https://events.pagerduty.com/v2/enqueue"
OPSGENIE_ALERTS_URL = {"us": "https://api.opsgenie.com/v2/alerts",
                       "eu": "https://api.eu.opsgenie.com/v2/alerts"}

KINDS = ("pagerduty", "opsgenie")
SEVERITIES = ("critical", "error", "warning", "info")
_OPSGENIE_PRIORITY = {"critical": "P1", "error": "P2", "warning": "P3", "info": "P4"}

TIMEOUT_SECONDS = 10


# --------------------------------------------------------------------------
# Maintenance windows
# --------------------------------------------------------------------------

class WindowError(ValueError):
    pass


def _parse_hhmm(value: str) -> dtime:
    try:
        hh, mm = str(value).strip().split(":")
        return dtime(int(hh), int(mm))
    except Exception:
        raise WindowError(f"time must be HH:MM, got {value!r}")


def _zone(name: str):
    from zoneinfo import ZoneInfo
    try:
        return ZoneInfo(name or "UTC")
    except Exception:
        raise WindowError(f"unknown timezone {name!r}")


def validate_window(*, starts_at: Optional[datetime], ends_at: Optional[datetime],
                    weekdays: Optional[List[int]], start_time: Optional[str],
                    end_time: Optional[str], timezone: str) -> None:
    """A window is EITHER one-off (both datetimes) OR weekly recurring
    (weekdays + both times); mixing them would make 'is it active' ambiguous."""
    _zone(timezone)
    one_off = starts_at is not None or ends_at is not None
    recurring = bool(weekdays) or start_time is not None or end_time is not None
    if one_off == recurring:
        raise WindowError("give either starts_at/ends_at (one-off) or "
                          "weekdays + start_time/end_time (weekly), not both")
    if one_off:
        if starts_at is None or ends_at is None or ends_at <= starts_at:
            raise WindowError("a one-off window needs starts_at < ends_at")
        return
    if not weekdays or any((not isinstance(d, int)) or d < 0 or d > 6 for d in weekdays):
        raise WindowError("weekdays must be a non-empty list of 0 (Mon) .. 6 (Sun)")
    if start_time is None or end_time is None:
        raise WindowError("a weekly window needs start_time and end_time")
    if _parse_hhmm(start_time) == _parse_hhmm(end_time):
        raise WindowError("start_time and end_time must differ")


def window_active(window: Any, now_utc: datetime) -> bool:
    """Whether `window` covers `now_utc` (naive UTC, like every timestamp in
    this codebase). Weekly windows are evaluated in the window's own timezone,
    so 'Sundays 02:00-04:00 Europe/Berlin' follows DST; an end before the
    start wraps past midnight and belongs to the START day's weekday."""
    if not getattr(window, "enabled", True):
        return False
    if window.starts_at is not None and window.ends_at is not None:
        return window.starts_at <= now_utc < window.ends_at
    if not window.weekdays or not window.start_time or not window.end_time:
        return False
    from zoneinfo import ZoneInfo
    try:
        tz = ZoneInfo(window.timezone or "UTC")
    except Exception:
        return False
    local = now_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    start, end = _parse_hhmm(window.start_time), _parse_hhmm(window.end_time)
    t = local.time()
    days = set(window.weekdays)
    if start < end:
        return local.weekday() in days and start <= t < end
    # Overnight: today's late part, or yesterday's window spilling over.
    if local.weekday() in days and t >= start:
        return True
    yesterday = (local - timedelta(days=1)).weekday()
    return yesterday in days and t < end


def active_window(windows: Iterable[Any], now_utc: datetime) -> Optional[Any]:
    for w in windows:
        if window_active(w, now_utc):
            return w
    return None


# --------------------------------------------------------------------------
# Wire formats (pure — tests assert on these)
# --------------------------------------------------------------------------

def pagerduty_payload(routing_key: str, action: str, dedup_key: str, *,
                      summary: str, severity: str = "error", source: str = "traceiq",
                      details: Optional[Dict[str, Any]] = None,
                      link: Optional[str] = None) -> Dict[str, Any]:
    body: Dict[str, Any] = {"routing_key": routing_key, "event_action": action,
                            "dedup_key": dedup_key[:255]}
    if action == "trigger":
        body["payload"] = {
            "summary": summary[:1024],
            "source": source,
            "severity": severity if severity in SEVERITIES else "error",
            "component": "traceiq",
            "custom_details": details or {},
        }
        if link:
            body["links"] = [{"href": link, "text": "Open in TraceIQ"}]
    return body


def opsgenie_request(api_key: str, action: str, alias: str, *, summary: str,
                     severity: str = "error", details: Optional[Dict[str, Any]] = None,
                     api_region: Optional[str] = None) -> Dict[str, Any]:
    """(method, url, headers, json) for an Opsgenie create/close."""
    base = OPSGENIE_ALERTS_URL.get((api_region or "us").lower(), OPSGENIE_ALERTS_URL["us"])
    headers = {"Authorization": f"GenieKey {api_key}", "Content-Type": "application/json"}
    if action == "trigger":
        return {"method": "POST", "url": base, "headers": headers, "json": {
            "message": summary[:130],
            "alias": alias[:512],
            "description": summary[:15000],
            "priority": _OPSGENIE_PRIORITY.get(severity, "P2"),
            "source": "TraceIQ",
            "tags": ["traceiq"],
            "details": {k: str(v)[:8000] for k, v in (details or {}).items()},
        }}
    from urllib.parse import quote
    return {"method": "POST",
            "url": f"{base}/{quote(alias[:512], safe='')}/close?identifierType=alias",
            "headers": headers,
            "json": {"source": "TraceIQ", "note": summary[:25000]}}


def _post(url: str, *, json: Dict[str, Any], headers: Optional[Dict[str, str]] = None):
    """The single network call — tests replace this."""
    return requests.post(url, json=json, headers=headers or {}, timeout=TIMEOUT_SECONDS)


def send(channel: Any, secret: str, action: str, dedup_key: str, *, summary: str,
         details: Optional[Dict[str, Any]] = None, link: Optional[str] = None) -> None:
    """Deliver one page. Raises on any non-2xx so the caller records 'failed'."""
    if channel.kind == "pagerduty":
        body = pagerduty_payload(secret, action, dedup_key, summary=summary,
                                 severity=channel.severity, details=details, link=link)
        resp = _post(PAGERDUTY_EVENTS_URL, json=body)
    elif channel.kind == "opsgenie":
        req = opsgenie_request(secret, action, dedup_key, summary=summary,
                               severity=channel.severity, details=details,
                               api_region=channel.api_region)
        resp = _post(req["url"], json=req["json"], headers=req["headers"])
    else:
        raise ValueError(f"unknown paging channel kind {channel.kind!r}")
    if resp.status_code >= 300:
        # The body may echo request fields but never the key header; keep it short.
        raise RuntimeError(f"{channel.kind} returned HTTP {resp.status_code}: "
                           f"{(resp.text or '')[:200]}")


# --------------------------------------------------------------------------
# Orchestration (sync session — called from Celery tasks)
# --------------------------------------------------------------------------

def monitor_dedup_key(schedule_id: int, region: Optional[str]) -> str:
    return f"traceiq:monitor:{schedule_id}" + (f":{region}" if region else "")


def run_dedup_key(project_id: int, suite_id: Optional[int], case_id: Optional[int],
                  region: Optional[str] = None) -> str:
    key = f"traceiq:run:{project_id}:{suite_id or 0}"
    if case_id:
        key += f":{case_id}"
    if region:
        key += f"@{region}"
    return key


def _is_open(session, channel_id: int, dedup_key: str) -> bool:
    from sqlmodel import select
    from app.models import PagingEvent
    last = session.exec(
        select(PagingEvent)
        .where(PagingEvent.channel_id == channel_id,
               PagingEvent.dedup_key == dedup_key,
               PagingEvent.outcome == "sent")
        .order_by(PagingEvent.created_at.desc(), PagingEvent.id.desc())
        .limit(1)).first()
    return bool(last and last.action == "trigger")


def page(session, *, project_id: int, source: str, action: str, dedup_key: str,
         summary: str, details: Optional[Dict[str, Any]] = None,
         link: Optional[str] = None, run_id: Optional[int] = None,
         schedule_id: Optional[int] = None,
         now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Fan one trigger/resolve out to the project's matching channels,
    honouring maintenance windows. Records every decision; commits. Never
    raises — paging must not break monitor evaluation or finalisation."""
    from sqlmodel import select
    from app.core.secrets import decrypt_secret
    from app.models import MaintenanceWindow, PagingChannel, PagingEvent

    now = now or datetime.utcnow()
    outcomes: List[Dict[str, Any]] = []
    try:
        flag = PagingChannel.on_monitor if source == "monitor" else PagingChannel.on_run_failure
        channels = session.exec(select(PagingChannel).where(
            PagingChannel.project_id == project_id,
            PagingChannel.enabled == True,  # noqa: E712
            flag == True)).all()  # noqa: E712
        if not channels:
            return outcomes
        windows = session.exec(select(MaintenanceWindow).where(
            MaintenanceWindow.project_id == project_id,
            MaintenanceWindow.enabled == True)).all()  # noqa: E712
        window = active_window(windows, now)

        for channel in channels:
            if action == "resolve" and not _is_open(session, channel.id, dedup_key):
                continue        # nothing we opened is open on this channel
            outcome, detail = "sent", None
            if window is not None:
                outcome = "suppressed"
                detail = f"maintenance window '{window.name}' (#{window.id})"
            else:
                try:
                    send(channel, decrypt_secret(channel.secret_encrypted), action,
                         dedup_key, summary=summary, details=details, link=link)
                    channel.last_sent_at = now
                    channel.last_error = None
                except Exception as exc:  # noqa: BLE001
                    outcome, detail = "failed", str(exc)[:500]
                    channel.last_error = detail
                session.add(channel)
            session.add(PagingEvent(
                project_id=project_id, channel_id=channel.id, source=source,
                action=action, dedup_key=dedup_key, outcome=outcome, detail=detail,
                run_id=run_id, schedule_id=schedule_id, created_at=now))
            outcomes.append({"channel_id": channel.id, "kind": channel.kind,
                             "action": action, "outcome": outcome, "detail": detail})
            logger.info("[Paging] %s %s via %s #%s: %s", action, dedup_key,
                        channel.kind, channel.id, outcome)
        session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.error("[Paging] %s for %s failed: %s", action, dedup_key, exc)
        try:
            session.rollback()
        except Exception:
            pass
    return outcomes

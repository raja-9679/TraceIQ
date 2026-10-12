"""Every Celery task must land on a queue some worker actually consumes.

Workers in every shipped deployment run `-Q main-queue` or `-Q aggregator-queue`
(compose files, infrastructure/aio/supervisord.conf, the Helm chart). A task
that falls through to Celery's implicit `celery` queue is enqueued and then
never executed — no error anywhere. That is how run notifications, outbound
webhooks, account emails, failure analysis, proactive heal and persona refresh
went silently dead until 2026-10-12.

Run with:
    cd backend && ./run-tests.sh tests/test_celery_routing.py -q
"""
import re
from pathlib import Path

import pytest

from app.core.celery_app import celery_app

REPO = Path(__file__).resolve().parents[2]
# What every shipped deployment's workers consume. If you add a queue, add a
# worker for it in ALL the deployment files, then add it here.
CONSUMED = {"main-queue", "aggregator-queue"}


def _consumed_queues() -> set:
    """Queues named by `-Q` in every deployment definition we ship."""
    sources = list((REPO / "infrastructure").glob("docker-compose*.yml"))
    sources += [REPO / "infrastructure/aio/supervisord.conf",
                REPO / "deploy/helm/traceiq/templates/celery.yaml"]
    queues = set()
    for src in sources:
        if not src.exists():
            continue
        text = src.read_text()
        queues |= set(re.findall(r'-Q\s+([\w-]+)', text))
        queues |= set(re.findall(r'"-Q"\s+"([\w-]+)"', text))
    return queues


def _queue_for(name: str) -> str:
    route = (celery_app.conf.task_routes or {}).get(name)
    if isinstance(route, dict):
        return route.get("queue")
    return route or celery_app.conf.task_default_queue


def test_deployment_files_consume_the_pinned_queues():
    found = _consumed_queues()
    if not found:
        pytest.skip("deployment files not visible (run-tests.sh mounts backend/ only); CI checks this")
    assert found == CONSUMED


def test_every_task_lands_on_a_consumed_queue():
    celery_app.loader.import_default_modules()
    consumed = CONSUMED
    tasks = sorted(n for n in celery_app.tasks if n.startswith("app."))
    assert tasks, "no app tasks registered — include list broken?"
    stranded = {n: _queue_for(n) for n in tasks if _queue_for(n) not in consumed}
    assert not stranded, f"tasks routed to queues no worker consumes: {stranded}"

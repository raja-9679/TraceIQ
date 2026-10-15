"""Helm chart: region workers + KEDA (WP8) — static checks.

helm/kubeconform are not available in the unit-test container; the CI
`helm-chart` job lints, renders and validates the chart. These checks pin the
two properties that matter if someone edits the templates by hand: KEDA and
region workers are OFF by default (so an upgrade renders what it rendered
before), and KEDA suppresses the CPU HPA (two autoscalers on one Deployment
fight each other).

    ./run-tests.sh tests/test_helm_keda.py -q
"""
from pathlib import Path

import pytest
import yaml

CHART = Path(__file__).resolve().parents[2] / "deploy/helm/traceiq"

pytestmark = pytest.mark.skipif(
    not CHART.exists(), reason="chart not visible (run-tests.sh mounts backend/ only); CI checks this")


def _values():
    return yaml.safe_load((CHART / "values.yaml").read_text())


def test_defaults_render_nothing_new():
    v = _values()
    assert v["keda"]["enabled"] is False
    assert v["regionWorkers"] == []
    assert v["executionWorker"]["region"] == ""
    assert v["executionWorker"]["regionExclusive"] is False


def test_keda_template_is_guarded_and_scales_on_stream_lag():
    t = (CHART / "templates/keda.yaml").read_text()
    assert "{{- if and .Values.keda .Values.keda.enabled }}" in t
    assert "type: redis-streams" in t
    assert "consumerGroup: execution-workers" in t
    assert "lagCount:" in t
    assert 'printf "jobs:pending:%s" .region' in t
    assert "kind: TriggerAuthentication" in t and "key: REDIS_PASSWORD" in t


def test_hpa_is_suppressed_when_keda_is_on():
    t = (CHART / "templates/execution-worker.yaml").read_text()
    assert "{{- if and .Values.executionWorker.autoscaling.enabled (not $kedaOn) }}" in t


def test_region_workers_reuse_the_worker_deployment():
    t = (CHART / "templates/execution-worker.yaml").read_text()
    assert '{{- define "traceiq.executionWorkerDeployment" -}}' in t
    assert "{{- range .Values.regionWorkers }}" in t
    assert "name: WORKER_REGION" in t and "WORKER_REGION_EXCLUSIVE" in t

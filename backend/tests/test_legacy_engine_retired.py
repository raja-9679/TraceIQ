"""WP15b H4: the legacy CONTINUOUS engine (server.ts) is deprecated and no
deployment setting routes a run to it."""
import inspect


def test_distributed_execution_cannot_be_switched_off(monkeypatch):
    # The compose files set USE_DISTRIBUTED_EXECUTION=true, but it was never a
    # Settings field; the dispatcher treats it as a constant.
    monkeypatch.setenv("USE_DISTRIBUTED_EXECUTION", "false")
    from app import worker
    assert worker.USE_DISTRIBUTED_EXECUTION is True
    assert not hasattr(worker.settings, "USE_DISTRIBUTED_EXECUTION")


def test_continuous_mode_dispatches_to_the_workers():
    from app import worker
    src = inspect.getsource(worker.run_test_suite)
    # The only call site of the legacy dispatcher sits behind the constant.
    assert src.count("dispatch_legacy_execution(") == 1
    tail = src.split("CONTINUOUS mode:", 1)[1]
    assert tail.index("dispatch_cases_to_queue(") < tail.index("dispatch_legacy_execution(")

"""TestRun records how its cases were selected (targeted runs, 2026-10)."""
from app.models import TestRun, TestRunRead


def test_selection_fields_default_to_none():
    run = TestRun(test_suite_id=1)
    assert run.selection_mode is None
    assert run.selection_total is None


def test_selection_fields_are_exposed_on_read_model():
    read = TestRunRead(id=1, test_suite_id=1, selection_mode="targeted", selection_total=37)
    assert read.selection_mode == "targeted"
    assert read.selection_total == 37

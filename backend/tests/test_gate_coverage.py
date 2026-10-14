"""The gate says how much of the suite a commit's runs covered — and never fails on it."""
from types import SimpleNamespace as R

from app.api.endpoints.quality import coverage_check


def run(case, mode, total=37):
    return R(test_case_id=case, selection_mode=mode, selection_total=total)


def test_targeted_only_reports_n_of_m():
    c = coverage_check([run(1, "targeted"), run(2, "targeted"), run(2, "targeted")])
    assert c.name == "coverage" and c.passed is True
    assert c.actual == "targeted — 2 of 37 cases ran"


def test_full_run_wins_over_targeted():
    c = coverage_check([run(1, "targeted"), run(None, "full")])
    assert c.actual == "full suite"


def test_runs_without_selection_data_are_unknown():
    c = coverage_check([run(1, None, None)])
    assert c.passed is True
    assert c.actual.startswith("unknown")


def test_single_case_runs_count_as_partial():
    c = coverage_check([run(5, "case")])
    assert c.actual == "targeted — 1 of 37 cases ran"


def test_no_runs_is_unknown_not_a_failure():
    assert coverage_check([]).passed is True


def test_runs_without_selection_data_do_not_hide_a_targeted_selection():
    # e.g. a comparison or scheduled run on the same commit carries no stamp
    c = coverage_check([run(1, "targeted"), run(None, None, None)])
    assert c.actual.startswith("targeted — 1 of 37 cases ran")
    assert "no selection data" in c.actual

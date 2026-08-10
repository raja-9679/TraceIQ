"""Retention selection — workstream G2.

`purge_old_runs` read one number, the global `RUN_RETENTION_DAYS`, and was
disabled by default. Meanwhile `Project.data_policy.retention_days` (workstream
B) and `Plan.limits.retention_days` existed as scaffolding nothing read — so a
project that declared "keep runs 30 days" kept them forever, and a buyer reading
the data-policy screen was being told something untrue.

The decision is small but easy to get subtly wrong, so it lives in a pure
function:

* A project's own retention wins over the global default. That is the whole
  point of a per-project policy.
* The **shorter** of the two never silently loses to the longer. An operator who
  sets a 7-day instance-wide default is stating a ceiling, not a suggestion;
  a project asking for 400 days must not override it upward.
* 0 or absent means "keep forever" *at that level* — not "delete immediately".
  Getting this backwards deletes a customer's entire history on first run, which
  is why every branch here is tested rather than inferred.
"""
import pytest

from app.services.retention import effective_retention_days


# --- Nothing configured -------------------------------------------------------

def test_no_policy_anywhere_keeps_forever():
    assert effective_retention_days(project_days=0, global_days=0) is None


def test_none_is_treated_like_zero():
    assert effective_retention_days(project_days=None, global_days=None) is None


# --- One level configured -----------------------------------------------------

def test_global_applies_when_the_project_says_nothing():
    assert effective_retention_days(project_days=0, global_days=30) == 30


def test_project_applies_when_there_is_no_global():
    assert effective_retention_days(project_days=14, global_days=0) == 14


# --- Both configured ----------------------------------------------------------

def test_the_shorter_window_wins():
    # An instance-wide 7 days is a ceiling. A project cannot keep data longer
    # than the operator allows.
    assert effective_retention_days(project_days=400, global_days=7) == 7


def test_a_project_may_ask_for_less_than_the_global():
    assert effective_retention_days(project_days=7, global_days=90) == 7


def test_equal_values_are_stable():
    assert effective_retention_days(project_days=30, global_days=30) == 30


# --- Nonsense input -----------------------------------------------------------

def test_negative_values_are_ignored_rather_than_deleting_everything():
    # A negative window would compute a cutoff in the future and purge the
    # entire table. Treat it as unset.
    assert effective_retention_days(project_days=-5, global_days=0) is None
    assert effective_retention_days(project_days=-5, global_days=30) == 30


def test_non_numeric_values_are_ignored():
    assert effective_retention_days(project_days="lots", global_days=30) == 30
    assert effective_retention_days(project_days=None, global_days="") is None


@pytest.mark.parametrize("days", [1, 7, 30, 365, 3650])
def test_plausible_windows_pass_through(days):
    assert effective_retention_days(project_days=days, global_days=0) == days

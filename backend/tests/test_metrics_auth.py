"""`/metrics` must not be anonymous.

The endpoint sat on the public API surface with no dependency at all, while the
adjacent `/api/admin/queue-health` was authenticated. Queue depths, run counts
and consumer counts are operational intelligence about a deployment; they
should not be readable by anyone who can reach the port.

Prometheus cannot present a JWT, so the supported mechanism is a dedicated
bearer token (`METRICS_TOKEN`). When no token is configured the endpoint falls
back to requiring a logged-in principal rather than allowing anonymous reads.
"""
from app.api.observability import metrics_token_accepted


def test_configured_token_accepts_matching_bearer():
    assert metrics_token_accepted("s3cret-scrape-token", "Bearer s3cret-scrape-token") is True


def test_configured_token_rejects_wrong_bearer():
    assert metrics_token_accepted("s3cret-scrape-token", "Bearer nope") is False


def test_configured_token_rejects_missing_header():
    assert metrics_token_accepted("s3cret-scrape-token", None) is False


def test_configured_token_rejects_bare_value_without_scheme():
    assert metrics_token_accepted("s3cret-scrape-token", "s3cret-scrape-token") is False


def test_scheme_match_is_case_insensitive():
    assert metrics_token_accepted("tok", "bearer tok") is True


def test_unconfigured_token_never_accepts_a_bearer():
    # An empty METRICS_TOKEN must not mean "any token works" — the caller
    # falls back to principal auth instead.
    assert metrics_token_accepted("", "Bearer anything") is False
    assert metrics_token_accepted("", None) is False


def test_rejects_token_that_is_a_prefix_of_the_configured_one():
    assert metrics_token_accepted("longtoken", "Bearer long") is False

"""SSRF guard behaviour (app/core/net_guard.py).

No database or running stack required — these are pure and safe to run in CI.

The property that matters most: link-local/metadata addresses stay blocked even
when ALLOW_PRIVATE_NETWORK_TARGETS is on, because self-hosted users must be able
to test internal apps without also handing every user a path to
169.254.169.254.
"""
import pytest

from app.core.config import settings
from app.core.net_guard import UnsafeUrlError, validate_outbound_url


@pytest.fixture
def policy():
    """Set the outbound policy for one test and restore it afterwards."""
    original = (
        settings.ALLOW_PRIVATE_NETWORK_TARGETS,
        list(settings.OUTBOUND_ALLOWED_HOSTS),
    )

    def _set(*, allow_private=False, allowed_hosts=()):
        settings.ALLOW_PRIVATE_NETWORK_TARGETS = allow_private
        settings.OUTBOUND_ALLOWED_HOSTS = list(allowed_hosts)

    yield _set
    settings.ALLOW_PRIVATE_NETWORK_TARGETS, settings.OUTBOUND_ALLOWED_HOSTS = original


# IP literals are used throughout so the tests need no DNS and cannot flake.

@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # AWS/GCP metadata
        "http://127.0.0.1:8000/metrics",             # the backend's own metrics
        "http://10.0.0.5/internal",
        "http://192.168.1.10/",
        "http://172.16.0.1/",
        "http://[::1]:9000/",
        "http://[fe80::1]/",
        "http://0.0.0.0/",
    ],
)
async def test_refuses_internal_targets_by_default(policy, url):
    policy(allow_private=False)
    with pytest.raises(UnsafeUrlError):
        await validate_outbound_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://example.com/",
        "ftp://example.com/x",
        "redis://redis:6379/0",
    ],
)
async def test_refuses_non_http_schemes(policy, url):
    policy(allow_private=True)  # scheme check must apply regardless of policy
    with pytest.raises(UnsafeUrlError, match="scheme"):
        await validate_outbound_url(url)


@pytest.mark.parametrize(
    "url",
    ["http://10.0.0.5/internal", "http://192.168.1.10/", "http://127.0.0.1:8000/", "http://[::1]/"],
)
async def test_allows_private_targets_when_enabled(policy, url):
    """The self-hosted case: testing an app on your own network."""
    policy(allow_private=True)
    assert await validate_outbound_url(url) == url


@pytest.mark.parametrize("url", ["http://169.254.169.254/", "http://[fe80::1]/"])
async def test_link_local_blocked_even_when_private_allowed(policy, url):
    """Regression guard. Enabling private targets must not expose metadata."""
    policy(allow_private=True)
    with pytest.raises(UnsafeUrlError, match="link-local"):
        await validate_outbound_url(url)


async def test_explicit_host_allowlist_overrides_private_denial(policy):
    policy(allow_private=False, allowed_hosts=("10.0.0.5",))
    assert await validate_outbound_url("http://10.0.0.5/x") == "http://10.0.0.5/x"


async def test_allowlist_does_not_leak_to_other_hosts(policy):
    policy(allow_private=False, allowed_hosts=("10.0.0.5",))
    with pytest.raises(UnsafeUrlError):
        await validate_outbound_url("http://10.0.0.6/x")


async def test_rejects_url_without_host(policy):
    policy()
    with pytest.raises(UnsafeUrlError):
        await validate_outbound_url("http:///nohost")


async def test_unresolvable_host_is_refused_not_crashed(policy):
    policy()
    with pytest.raises(UnsafeUrlError):
        await validate_outbound_url(
            "http://this-host-should-not-resolve.invalid/"
        )

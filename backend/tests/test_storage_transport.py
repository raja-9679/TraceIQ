"""MinIO endpoint resolution and server-side encryption.

Two defects this covers:

*TLS was unreachable.* `_init_clients` prefixed `http://` onto any endpoint
without a scheme, and compose ships `MINIO_ENDPOINT: minio:9000` (scheme-less).
So the internal client always spoke plaintext no matter how the deployment was
configured, and presigned URLs were signed against a plain-HTTP host.

*SSE could not be set on two of three write paths.* `upload_fileobj` already
built an `ExtraArgs` dict, but `upload_file` and `copy_object` took none — and
`copy_object` is what promotes a visual baseline, so baselines would have
landed unencrypted even in a deployment that had turned SSE on everywhere else.
"""
from app.core.storage import normalize_endpoint, sse_extra_args


# --------------------------------------------------------------------------
# Endpoint resolution
# --------------------------------------------------------------------------

def test_scheme_less_endpoint_defaults_to_http_for_backward_compatibility():
    # Existing compose files ship `minio:9000`; they must keep working.
    assert normalize_endpoint("minio:9000", use_ssl=False) == "http://minio:9000"


def test_scheme_less_endpoint_becomes_https_when_ssl_is_requested():
    # This is the case that was impossible before.
    assert normalize_endpoint("minio:9000", use_ssl=True) == "https://minio:9000"


def test_an_explicit_https_endpoint_is_preserved():
    assert normalize_endpoint("https://s3.example.com", use_ssl=False) == "https://s3.example.com"


def test_an_explicit_http_endpoint_is_preserved_even_when_ssl_is_requested():
    # An operator who spells out http:// means it; silently upgrading would
    # produce a confusing connection failure rather than an honest one.
    assert normalize_endpoint("http://minio:9000", use_ssl=True) == "http://minio:9000"


def test_a_bare_host_with_no_port_is_accepted():
    assert normalize_endpoint("s3.example.com", use_ssl=True) == "https://s3.example.com"


def test_whitespace_is_stripped():
    assert normalize_endpoint("  minio:9000  ", use_ssl=True) == "https://minio:9000"


def test_an_empty_endpoint_stays_empty_rather_than_becoming_a_scheme():
    # "http://" alone would be a confusing client construction error.
    assert normalize_endpoint("", use_ssl=False) == ""
    assert normalize_endpoint(None, use_ssl=True) == ""


# --------------------------------------------------------------------------
# Server-side encryption
# --------------------------------------------------------------------------

def test_no_sse_configured_yields_no_extra_args():
    # Must stay None/empty so MinIO deployments without SSE are unaffected.
    assert sse_extra_args(None) == {}
    assert sse_extra_args("") == {}


def test_aes256_sse_is_passed_through():
    assert sse_extra_args("AES256") == {"ServerSideEncryption": "AES256"}


def test_kms_sse_requires_and_carries_the_key_id():
    args = sse_extra_args("aws:kms", kms_key_id="arn:aws:kms:...:key/abc")
    assert args["ServerSideEncryption"] == "aws:kms"
    assert args["SSEKMSKeyId"] == "arn:aws:kms:...:key/abc"


def test_kms_without_a_key_id_still_sets_the_algorithm():
    # Some backends have a configured default key; omitting the id is valid.
    assert sse_extra_args("aws:kms") == {"ServerSideEncryption": "aws:kms"}


def test_sse_algorithm_is_case_normalised():
    assert sse_extra_args("aes256") == {"ServerSideEncryption": "AES256"}


def test_an_unknown_algorithm_is_rejected_rather_than_passed_through():
    # A typo must not silently disable encryption on every upload.
    import pytest
    with pytest.raises(ValueError):
        sse_extra_args("rot13")


def test_sse_args_merge_with_caller_supplied_extras():
    merged = sse_extra_args("AES256", base={"ContentType": "image/png"})
    assert merged["ContentType"] == "image/png"
    assert merged["ServerSideEncryption"] == "AES256"


def test_caller_extras_survive_when_sse_is_off():
    assert sse_extra_args(None, base={"ContentType": "image/png"}) == {"ContentType": "image/png"}

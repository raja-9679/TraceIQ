"""The S3 object-store wrapper (app/core/storage.py), without a server.

botocore's Stubber answers each S3 call with a canned response and checks the
request parameters, so these pin the wrapper's behaviour — what it asks the
store for and how it reads the answers. tests/integration/test_object_store_live.py
runs the same operations against a real store.
"""
import io
import logging
from urllib.parse import urlparse

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError, ConnectTimeoutError
from botocore.response import StreamingBody
from botocore.stub import ANY, Stubber

from app.core import storage

SETTINGS = {
    "MINIO_ENDPOINT": "object-store:9000",
    "MINIO_PUBLIC_URL": "http://artifacts.example.test:9000",
    "MINIO_ACCESS_KEY": "unit-access",
    "MINIO_SECRET_KEY": "unit-secret-value",
    "MINIO_BUCKET_NAME": "unit-bucket",
    "MINIO_USE_SSL": "false",
    "MINIO_SSE_ALGORITHM": "",
    "MINIO_SSE_KMS_KEY_ID": "",
}


@pytest.fixture
def store(monkeypatch):
    from app.services import instance_settings
    monkeypatch.setattr(instance_settings, "effective", lambda key: SETTINGS.get(key))
    client = storage.MinioClient()
    client._init_clients()
    with Stubber(client.s3) as stub:
        client.stub = stub
        yield client
        stub.assert_no_pending_responses()


def _body(data: bytes) -> StreamingBody:
    return StreamingBody(io.BytesIO(data), len(data))


def test_neutral_alias_is_the_same_client():
    assert storage.object_store is storage.minio_client


# --- ensure_bucket -------------------------------------------------------------

def test_ensure_bucket_creates_only_when_missing_and_sets_cors(store):
    store.stub.add_client_error("head_bucket", service_error_code="404", http_status_code=404,
                                expected_params={"Bucket": "unit-bucket"})
    store.stub.add_response("create_bucket", {}, {"Bucket": "unit-bucket"})
    store.stub.add_response("put_bucket_cors", {}, {"Bucket": "unit-bucket", "CORSConfiguration": ANY})
    store.ensure_bucket()


def test_ensure_bucket_existing_bucket_is_not_recreated(store):
    store.stub.add_response("head_bucket", {}, {"Bucket": "unit-bucket"})
    store.stub.add_response("put_bucket_cors", {}, {"Bucket": "unit-bucket", "CORSConfiguration": ANY})
    store.ensure_bucket()


def test_ensure_bucket_auth_failure_is_raised_not_answered_with_create(store):
    store.stub.add_client_error("head_bucket", service_error_code="403", http_status_code=403)
    with pytest.raises(ClientError):
        store.ensure_bucket()


def test_cors_rule_allows_browser_get(store):
    seen = {}
    store.stub.add_response("head_bucket", {})
    store.stub.add_response("put_bucket_cors", {})

    def capture(params, **_):
        seen.update(params)
    store.s3.meta.events.register("provide-client-params.s3.PutBucketCors", capture)
    store.ensure_bucket()
    rule = seen["CORSConfiguration"]["CORSRules"][0]
    assert set(rule["AllowedMethods"]) == {"GET", "HEAD"} and rule["AllowedOrigins"] == ["*"]


def test_cors_failure_warns_instead_of_failing_startup(store, caplog):
    store.stub.add_response("head_bucket", {})
    store.stub.add_client_error("put_bucket_cors", service_error_code="NotImplemented",
                                http_status_code=501)
    with caplog.at_level(logging.WARNING, logger="app.core.storage"):
        store.ensure_bucket()
    assert "CORS" in caplog.text


# --- reads -----------------------------------------------------------------------

def test_list_objects_follows_every_page(store):
    store.stub.add_response("list_objects_v2", {
        "IsTruncated": True, "NextContinuationToken": "t1",
        "Contents": [{"Key": "runs/1/a.png", "Size": 3}]},
        {"Bucket": "unit-bucket", "Prefix": "runs/1/"})
    store.stub.add_response("list_objects_v2", {
        "IsTruncated": False, "Contents": [{"Key": "runs/1/b.png", "Size": 4}]},
        {"Bucket": "unit-bucket", "Prefix": "runs/1/", "ContinuationToken": "t1"})
    assert store.list_objects("runs/1/") == [
        {"key": "runs/1/a.png", "size": 3}, {"key": "runs/1/b.png", "size": 4}]


def test_find_object_searches_beyond_the_first_page(store):
    store.stub.add_response("list_objects_v2", {
        "IsTruncated": True, "NextContinuationToken": "t1",
        "Contents": [{"Key": "runs/1/x.png", "Size": 1}]})
    store.stub.add_response("list_objects_v2", {
        "IsTruncated": False, "Contents": [{"Key": "runs/1/step-3.png", "Size": 1}]})
    assert store.find_object("runs/1/", "step-3.png") == "runs/1/step-3.png"


def test_object_size_and_missing_object(store):
    store.stub.add_response("head_object", {"ContentLength": 42},
                            {"Bucket": "unit-bucket", "Key": "runs/1/t.zip"})
    store.stub.add_client_error("head_object", service_error_code="404", http_status_code=404)
    assert store.object_size("runs/1/t.zip") == 42
    assert store.object_size("runs/1/gone.zip") is None


def test_object_size_other_errors_propagate(store):
    store.stub.add_client_error("head_object", service_error_code="403", http_status_code=403)
    with pytest.raises(ClientError):
        store.object_size("runs/1/t.zip")


def test_get_object_bytes_enforces_the_limit_on_bytes_read(store):
    store.stub.add_response("get_object", {"Body": _body(b"12345")},
                            {"Bucket": "unit-bucket", "Key": "k"})
    store.stub.add_response("get_object", {"Body": _body(b"123456")})
    assert store.get_object_bytes("k", max_bytes=5) == b"12345"
    with pytest.raises(ValueError):
        store.get_object_bytes("k", max_bytes=5)


# --- deletes ---------------------------------------------------------------------

def test_delete_prefix_counts_only_what_the_store_deleted(store, caplog):
    store.stub.add_response("list_objects_v2", {
        "IsTruncated": False,
        "Contents": [{"Key": f"runs/7/{i}.png", "Size": 1} for i in range(3)]})
    store.stub.add_response("delete_objects", {
        "Deleted": [{"Key": "runs/7/0.png"}, {"Key": "runs/7/1.png"}],
        "Errors": [{"Key": "runs/7/2.png", "Code": "AccessDenied", "Message": "no"}]},
        {"Bucket": "unit-bucket", "Delete": {"Objects": [{"Key": f"runs/7/{i}.png"} for i in range(3)]}})
    with caplog.at_level(logging.WARNING, logger="app.core.storage"):
        assert store.delete_prefix("runs/7/") == 2
    assert "AccessDenied" in caplog.text


def test_delete_prefix_chunks_at_1000(store):
    keys = [{"Key": f"runs/8/{i:05d}", "Size": 1} for i in range(1500)]
    store.stub.add_response("list_objects_v2", {"IsTruncated": False, "Contents": keys})
    store.stub.add_response("delete_objects", {}, {"Bucket": "unit-bucket", "Delete": {"Objects": [{"Key": k["Key"]} for k in keys[:1000]]}})
    store.stub.add_response("delete_objects", {}, {"Bucket": "unit-bucket", "Delete": {"Objects": [{"Key": k["Key"]} for k in keys[1000:]]}})
    assert store.delete_prefix("runs/8/") == 1500


def test_delete_prefix_refuses_an_empty_prefix(store):
    assert store.delete_prefix("") == 0


# --- presigned URLs ------------------------------------------------------------

def test_public_presigned_url_is_signed_for_the_browser_host(store):
    url = urlparse(store.get_presigned_url("runs/1/traces/t.zip", expiration=60))
    assert url.netloc == "artifacts.example.test:9000"
    assert url.path == "/unit-bucket/runs/1/traces/t.zip"   # path-style
    assert "X-Amz-Signature=" in url.query and "X-Amz-Expires=60" in url.query


def test_internal_presigned_url_uses_the_network_endpoint(store):
    url = urlparse(store.get_internal_presigned_url("app-builds/1/x/app.apk"))
    assert (url.scheme, url.netloc) == ("http", "object-store:9000")


def test_blank_public_url_falls_back_to_internal(monkeypatch):
    from app.services import instance_settings
    monkeypatch.setattr(instance_settings, "effective",
                        lambda key: {**SETTINGS, "MINIO_PUBLIC_URL": " "}.get(key))
    client = storage.MinioClient()
    assert urlparse(client.get_presigned_url("k")).netloc == "object-store:9000"


# --- unavailable store -----------------------------------------------------------

def test_unreachable_store_fails_fast_with_a_connection_error(monkeypatch):
    from app.services import instance_settings
    # Port 9 (discard) on loopback: refused immediately, no store behind it.
    monkeypatch.setattr(instance_settings, "effective",
                        lambda key: {**SETTINGS, "MINIO_ENDPOINT": "127.0.0.1:9"}.get(key))
    client = storage.MinioClient()
    with pytest.raises((EndpointConnectionError, ConnectTimeoutError)):
        client.object_size("runs/1/t.zip")

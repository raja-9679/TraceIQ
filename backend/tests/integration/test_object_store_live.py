"""The object-store wrapper and the migration script against a REAL S3 store
(RustFS in the stack and in CI). Each test module run uses its own throwaway
buckets and removes them afterwards.

    ./run-tests-live.sh tests/integration/test_object_store_live.py -q

Skipped unless TRACEIQ_LIVE_STORE=1 (run-tests-live.sh sets it when it finds
the stack's object-store container; CI sets it).
"""
from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("TRACEIQ_LIVE_STORE") != "1",
    reason="needs a live object store; set TRACEIQ_LIVE_STORE=1 and MINIO_*")

from app.core import storage  # noqa: E402

RUN = uuid.uuid4().hex[:8]


def _settings(bucket):
    return {
        "MINIO_ENDPOINT": os.environ["MINIO_ENDPOINT"],
        # Inside the test container the "browser" is this process, so the
        # public URL is the network endpoint — the signature still has to
        # match the host the consumer actually sends.
        "MINIO_PUBLIC_URL": storage.normalize_endpoint(os.environ["MINIO_ENDPOINT"], False),
        "MINIO_ACCESS_KEY": os.environ["MINIO_ACCESS_KEY"],
        "MINIO_SECRET_KEY": os.environ["MINIO_SECRET_KEY"],
        "MINIO_BUCKET_NAME": bucket,
        "MINIO_USE_SSL": "false",
    }


def _client(monkeypatch, bucket):
    from app.services import instance_settings
    conf = _settings(bucket)
    monkeypatch.setattr(instance_settings, "effective", lambda key: conf.get(key))
    c = storage.MinioClient()
    c._init_clients()
    return c


def _drop_bucket(c):
    try:
        for o in c.list_objects(""):
            c.delete_object(o["key"])
        c.s3.delete_bucket(Bucket=c.bucket)
    except Exception:  # noqa: BLE001 - best-effort cleanup
        pass


@pytest.fixture
def store(monkeypatch):
    c = _client(monkeypatch, f"tiq-live-{RUN}")
    c.ensure_bucket()
    yield c
    _drop_bucket(c)


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=15) as r:
        # HTTP header names are case-insensitive; normalise for lookups.
        return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()


def test_bucket_init_is_idempotent(store):
    store.ensure_bucket()
    store.ensure_bucket()
    assert store.s3.head_bucket(Bucket=store.bucket)["ResponseMetadata"]["HTTPStatusCode"] == 200


def test_upload_download_delete_round_trip(store, tmp_path):
    f = tmp_path / "trace.zip"
    f.write_bytes(b"PK\x03\x04 trace")
    store.upload_file(str(f), "runs/1/traces/trace.zip", content_type="application/zip")
    assert store.object_exists("runs/1/traces/trace.zip")
    assert store.object_size("runs/1/traces/trace.zip") == f.stat().st_size
    assert store.get_object_bytes("runs/1/traces/trace.zip") == f.read_bytes()
    head = store.s3.head_object(Bucket=store.bucket, Key="runs/1/traces/trace.zip")
    assert head["ContentType"] == "application/zip"
    store.delete_object("runs/1/traces/trace.zip")
    assert store.object_size("runs/1/traces/trace.zip") is None


def test_large_streamed_upload_goes_multipart_and_reads_back(store):
    data = os.urandom(20 * 1024 * 1024)

    class Stream:  # an UploadFile-like stream, never seekable to the end up front
        def __init__(self):
            self._b = memoryview(data)
            self._i = 0

        def read(self, n=-1):
            n = len(self._b) - self._i if n is None or n < 0 else n
            out = bytes(self._b[self._i:self._i + n])
            self._i += len(out)
            return out

    store.upload_fileobj(Stream(), "app-builds/1/u/app.apk",
                         content_type="application/vnd.android.package-archive")
    etag = store.s3.head_object(Bucket=store.bucket, Key="app-builds/1/u/app.apk")["ETag"]
    assert "-" in etag  # multipart
    assert store.get_object_bytes("app-builds/1/u/app.apk") == data


def test_presigned_get_works_for_its_consumer_and_serves_cors(store):
    store.s3.put_object(Bucket=store.bucket, Key="runs/2/traces/t.zip", Body=b"zipbytes",
                        ContentType="application/zip")
    url = store.get_presigned_url("runs/2/traces/t.zip", expiration=120)
    status, headers, body = _get(url, {"Origin": "http://localhost:8080"})
    assert (status, body) == (200, b"zipbytes")
    # The trace timeline fetch()es this cross-origin.
    assert headers.get("access-control-allow-origin") in {"*", "http://localhost:8080"}

    status, _, body = _get(store.get_internal_presigned_url("runs/2/traces/t.zip", 120))
    assert (status, body) == (200, b"zipbytes")


def test_tampered_or_foreign_presigned_url_is_refused(store):
    store.s3.put_object(Bucket=store.bucket, Key="runs/3/a.png", Body=b"a")
    url = store.get_presigned_url("runs/3/a.png", expiration=120)
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(url.replace("/runs/3/a.png", "/runs/3/b.png"))
    assert exc.value.code == 403


def test_copy_list_find_and_bulk_delete(store):
    store.s3.put_object(Bucket=store.bucket, Key="runs/4/screenshots/step-1.png", Body=b"png")
    store.copy_object("runs/4/screenshots/step-1.png", "baselines/9/1/chromium.png")
    assert store.get_object_bytes("baselines/9/1/chromium.png") == b"png"

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(16) as ex:
        list(ex.map(lambda i: store.s3.put_object(
            Bucket=store.bucket, Key=f"runs/5/screenshots/{i:05d}.png", Body=b"x"), range(1105)))
    assert len(store.list_objects("runs/5/")) == 1105
    assert store.find_object("runs/5/", "01104.png") == "runs/5/screenshots/01104.png"
    assert sorted(store.list_prefixes("runs/")) == ["runs/4/", "runs/5/"]
    assert store.delete_prefix("runs/5/") == 1105
    assert store.list_objects("runs/5/") == []


def test_migration_script_copies_verifies_and_never_touches_the_source(monkeypatch):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
    import migrate_object_store as mig

    src = _client(monkeypatch, f"tiq-mig-src-{RUN}")
    dst = _client(monkeypatch, f"tiq-mig-dst-{RUN}")
    src.ensure_bucket()
    try:
        big = os.urandom(9 * 1024 * 1024)
        src.s3.put_object(Bucket=src.bucket, Key="runs/1/traces/t.zip", Body=b"t", ContentType="application/zip")
        src.s3.put_object(Bucket=src.bucket, Key="runs/1/videos/v.webm", Body=big, ContentType="video/webm")
        before = sorted((o["key"], o["size"]) for o in src.list_objects(""))

        dry = mig.migrate(src.s3, src.bucket, dst.s3, dst.bucket, "", apply=False, extra_args={}, log=lambda *_: None)
        assert (dry["seen"], dry["to_copy"], dry["copied"]) == (2, 2, 0)
        with pytest.raises(Exception):
            dst.s3.head_bucket(Bucket=dst.bucket)  # dry run created nothing

        done = mig.migrate(src.s3, src.bucket, dst.s3, dst.bucket, "", apply=True, extra_args={}, log=lambda *_: None)
        assert (done["copied"], done["failed"]) == (2, [])
        assert dst.get_object_bytes("runs/1/videos/v.webm") == big
        assert dst.s3.head_object(Bucket=dst.bucket, Key="runs/1/traces/t.zip")["ContentType"] == "application/zip"

        again = mig.migrate(src.s3, src.bucket, dst.s3, dst.bucket, "", apply=True, extra_args={}, log=lambda *_: None)
        assert (again["already_present"], again["copied"]) == (2, 0)

        assert sorted((o["key"], o["size"]) for o in src.list_objects("")) == before
    finally:
        _drop_bucket(src)
        _drop_bucket(dst)


def test_migration_script_refuses_copying_a_bucket_onto_itself():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
    import migrate_object_store as mig
    ep = os.environ["MINIO_ENDPOINT"]
    with pytest.raises(SystemExit):
        mig.main(["--source-endpoint", ep, "--source-access-key", "a", "--source-secret-key", "b",
                  "--dest-endpoint", ep, "--dest-bucket", "same"])

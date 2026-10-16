#!/usr/bin/env python
"""Copy every artifact from one S3-compatible store to another, over the S3 API.

Written for moving an existing MinIO install onto the bundled RustFS store,
whose data directory is NOT assumed to be readable from MinIO's on-disk
format. It is a plain S3-to-S3 copy, so it also works for moving to a cloud
bucket.

Safety properties:

  * Read-only on the source. Nothing is ever deleted or overwritten there.
  * Dry run by default; pass --apply to write.
  * Idempotent: an object already present at the destination with the same
    size is skipped, so an interrupted run can simply be re-run.
  * Verified: every copied object is re-checked at the destination (size), and
    the bytes read are hashed against the source ETag when the source ETag is
    a plain MD5 (single-part uploads). Any mismatch fails the run (exit 1).

The destination defaults to this instance's own settings (MINIO_ENDPOINT,
MINIO_ACCESS_KEY, MINIO_SECRET_KEY, MINIO_BUCKET_NAME, MINIO_SSE_*), so inside
the backend container only the source needs naming:

  python scripts/migrate_object_store.py \\
      --source-endpoint http://old-minio:9000 \\
      --source-access-key "$OLD_USER" --source-secret-key "$OLD_PASSWORD"
  # review the plan, then:
  python scripts/migrate_object_store.py ... --apply

Credentials may also come from SOURCE_ACCESS_KEY / SOURCE_SECRET_KEY in the
environment, which keeps them out of shell history. See SELF_HOSTING.md,
"Object storage → Migrating from MinIO", for the full procedure.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from typing import Dict, Iterator, Optional

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.client import Config
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.storage import normalize_endpoint, sse_extra_args  # noqa: E402


def _client(endpoint: str, access_key: str, secret_key: str):
    return boto3.client(
        "s3", endpoint_url=endpoint, aws_access_key_id=access_key,
        aws_secret_access_key=secret_key, region_name="us-east-1",
        config=Config(signature_version="s3v4", connect_timeout=5, read_timeout=120,
                      retries={"max_attempts": 5, "mode": "standard"}))


def _objects(s3, bucket: str, prefix: str) -> Iterator[Dict]:
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        yield from page.get("Contents", [])


def _dest_size(s3, bucket: str, key: str) -> Optional[int]:
    try:
        return int(s3.head_object(Bucket=bucket, Key=key)["ContentLength"])
    except ClientError as e:
        if str(e.response.get("Error", {}).get("Code")) in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise


class _HashingReader:
    """File-like wrapper that MD5s whatever passes through it."""

    def __init__(self, body):
        self._body = body
        self.md5 = hashlib.md5()  # noqa: S324 - integrity check against S3 ETag, not security
        self.bytes = 0

    def read(self, n: int = -1) -> bytes:
        if n == 0:
            return b""
        chunk = self._body.read(n) if n > 0 else self._body.read()
        self.md5.update(chunk)
        self.bytes += len(chunk)
        return chunk


def _plain_md5_etag(etag: str) -> Optional[str]:
    """The ETag as an MD5 hex digest, or None for multipart/encrypted ETags."""
    tag = (etag or "").strip('"')
    return tag if len(tag) == 32 and "-" not in tag else None


def migrate(src, src_bucket: str, dst, dst_bucket: str, prefix: str, apply: bool,
            extra_args: Dict, log=print) -> Dict:
    summary = {"seen": 0, "bytes_seen": 0, "already_present": 0, "to_copy": 0,
               "copied": 0, "bytes_copied": 0, "failed": [], "applied": apply}
    if apply:
        try:
            dst.head_bucket(Bucket=dst_bucket)
        except ClientError:
            dst.create_bucket(Bucket=dst_bucket)
            log(f"created destination bucket {dst_bucket}")
    transfer = TransferConfig(multipart_threshold=64 * 1024 * 1024, max_concurrency=4)

    for obj in _objects(src, src_bucket, prefix):
        key, size = obj["Key"], int(obj["Size"])
        summary["seen"] += 1
        summary["bytes_seen"] += size
        try:
            if _dest_size(dst, dst_bucket, key) == size:
                summary["already_present"] += 1
                continue
        except ClientError as e:
            if apply:
                summary["failed"].append({"key": key, "error": f"destination check: {e}"})
                continue
            # Dry run against a bucket that does not exist yet: everything is to copy.
        summary["to_copy"] += 1
        if not apply:
            continue
        try:
            got = src.get_object(Bucket=src_bucket, Key=key)
            reader = _HashingReader(got["Body"])
            extra = dict(extra_args)
            if got.get("ContentType"):
                extra["ContentType"] = got["ContentType"]
            if got.get("Metadata"):
                extra["Metadata"] = got["Metadata"]
            dst.upload_fileobj(reader, dst_bucket, key, ExtraArgs=extra or None, Config=transfer)
            expected_md5 = _plain_md5_etag(got.get("ETag", ""))
            if reader.bytes != size:
                raise ValueError(f"read {reader.bytes} bytes, source listed {size}")
            if expected_md5 and reader.md5.hexdigest() != expected_md5:
                raise ValueError("bytes read do not match the source ETag (MD5)")
            if _dest_size(dst, dst_bucket, key) != size:
                raise ValueError("destination size differs after copy")
            summary["copied"] += 1
            summary["bytes_copied"] += size
            if summary["copied"] % 500 == 0:
                log(f"... {summary['copied']} copied")
        except Exception as e:  # noqa: BLE001 - record and continue; the run fails at the end
            summary["failed"].append({"key": key, "error": str(e)[:300]})
    return summary


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--source-endpoint", required=True, help="e.g. http://old-minio:9000")
    p.add_argument("--source-access-key", default=os.environ.get("SOURCE_ACCESS_KEY"))
    p.add_argument("--source-secret-key", default=os.environ.get("SOURCE_SECRET_KEY"))
    p.add_argument("--source-bucket", help="defaults to the destination bucket name")
    p.add_argument("--dest-endpoint", default=None, help="defaults to MINIO_ENDPOINT")
    p.add_argument("--dest-access-key", default=os.environ.get("MINIO_ACCESS_KEY"))
    p.add_argument("--dest-secret-key", default=os.environ.get("MINIO_SECRET_KEY"))
    p.add_argument("--dest-bucket", default=os.environ.get("MINIO_BUCKET_NAME") or "test-artifacts")
    p.add_argument("--prefix", default="", help="only copy keys under this prefix")
    p.add_argument("--apply", action="store_true", help="actually copy (default: dry run)")
    args = p.parse_args(argv)

    if not (args.source_access_key and args.source_secret_key):
        p.error("source credentials are required (flags or SOURCE_ACCESS_KEY / SOURCE_SECRET_KEY)")
    use_ssl = str(os.environ.get("MINIO_USE_SSL") or "").lower() == "true"
    dest_endpoint = normalize_endpoint(args.dest_endpoint or os.environ.get("MINIO_ENDPOINT"), use_ssl)
    if not (dest_endpoint and args.dest_access_key and args.dest_secret_key):
        p.error("destination not configured (MINIO_ENDPOINT / MINIO_ACCESS_KEY / MINIO_SECRET_KEY)")
    source_endpoint = normalize_endpoint(args.source_endpoint, False)
    if source_endpoint.rstrip("/") == dest_endpoint.rstrip("/") and \
            (args.source_bucket or args.dest_bucket) == args.dest_bucket:
        p.error("source and destination are the same bucket on the same endpoint")

    extra = sse_extra_args(os.environ.get("MINIO_SSE_ALGORITHM"),
                           os.environ.get("MINIO_SSE_KMS_KEY_ID"))
    src = _client(source_endpoint, args.source_access_key, args.source_secret_key)
    dst = _client(dest_endpoint, args.dest_access_key, args.dest_secret_key)
    src_bucket = args.source_bucket or args.dest_bucket

    print(f"{'COPY' if args.apply else 'DRY RUN'}: {source_endpoint}/{src_bucket}/{args.prefix}"
          f" -> {dest_endpoint}/{args.dest_bucket}/{args.prefix}  (source is never modified)")
    summary = migrate(src, src_bucket, dst, args.dest_bucket, args.prefix, args.apply, extra)
    print(json.dumps(summary, indent=2))
    if summary["failed"]:
        print(f"FAILED: {len(summary['failed'])} object(s) not copied/verified; re-run to retry.",
              file=sys.stderr)
        return 1
    if not args.apply:
        print("Dry run only. Re-run with --apply to copy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

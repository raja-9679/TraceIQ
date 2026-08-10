from typing import Any, Dict, Optional

import boto3
from botocore.client import Config

#: Algorithms boto3/MinIO accept for at-rest encryption.
_SSE_ALGORITHMS = {"AES256": "AES256", "AWS:KMS": "aws:kms"}


def normalize_endpoint(endpoint: Optional[str], use_ssl: bool) -> str:
    """Resolve an endpoint into a full URL boto3 can use.

    Previously this hardcoded `http://` onto anything without a scheme, and
    compose ships `MINIO_ENDPOINT: minio:9000` — so the internal client always
    spoke plaintext however the deployment was configured, and presigned URLs
    were signed against a plain-HTTP host.

    An endpoint that already names a scheme is respected as written, including
    an explicit `http://` under `use_ssl`: silently upgrading it would turn an
    operator's stated intent into a confusing connection error.
    """
    raw = (endpoint or "").strip()
    if not raw:
        return ""
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    return f"{'https' if use_ssl else 'http'}://{raw}"


def sse_extra_args(
    algorithm: Optional[str],
    kms_key_id: Optional[str] = None,
    base: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the `ExtraArgs`/copy kwargs that request server-side encryption.

    Returns `base` unchanged when no algorithm is configured, so deployments
    without SSE are unaffected. An unrecognised algorithm raises rather than
    being dropped — a typo must not silently disable encryption on every
    upload, which is the failure mode you would never notice.
    """
    args: Dict[str, Any] = dict(base or {})
    if not algorithm or not str(algorithm).strip():
        return args

    key = str(algorithm).strip().upper()
    if key not in _SSE_ALGORITHMS:
        raise ValueError(
            f"Unknown MINIO_SSE_ALGORITHM {algorithm!r}; expected one of "
            f"{', '.join(sorted(_SSE_ALGORITHMS.values()))}")

    args["ServerSideEncryption"] = _SSE_ALGORITHMS[key]
    if key == "AWS:KMS" and kms_key_id:
        args["SSEKMSKeyId"] = kms_key_id
    return args


def _client_config() -> Config:
    """boto3 client config with BOUNDED retries and timeouts.

    botocore's defaults retry with exponential backoff and no connect timeout,
    so an unreachable object store makes every call hang for minutes instead of
    failing. That is visible in two places: a request handler blocks while a
    user waits, and the retention/purge paths (which delete artifacts
    best-effort and are supposed to degrade gracefully) took 100 seconds per
    handful of objects when the endpoint was down.

    Five seconds to connect, three attempts. A genuinely slow store still gets a
    30-second read window, which is generous for the object sizes here.
    """
    return Config(
        signature_version="s3v4",
        connect_timeout=5,
        read_timeout=30,
        retries={"max_attempts": 3, "mode": "standard"},
    )


class MinioClient:
    """S3/MinIO access. Config is resolved lazily on FIRST USE (not import),
    through the effective instance settings (admin UI DB override, else env).
    Once built, the clients are frozen for the life of the process — storage
    settings are advertised as restart-required in the admin UI, because
    swapping buckets/endpoints mid-flight would strand in-progress artifacts.
    """
    def __init__(self):
        self._s3 = None
        self._s3_public = None
        self._bucket = None
        self._sse = None
        self._sse_kms_key_id = None

    def _init_clients(self):
        from app.services.instance_settings import effective

        # boto3 wants a full URL. `MINIO_USE_SSL` decides the scheme for a
        # scheme-less endpoint (which is what compose ships); an endpoint that
        # spells out its own scheme wins.
        use_ssl = str(effective("MINIO_USE_SSL") or "").strip().lower() == "true"
        endpoint = normalize_endpoint(effective("MINIO_ENDPOINT"), use_ssl)
        access_key = effective("MINIO_ACCESS_KEY")
        secret_key = effective("MINIO_SECRET_KEY")

        self._sse = effective("MINIO_SSE_ALGORITHM") or None
        self._sse_kms_key_id = effective("MINIO_SSE_KMS_KEY_ID") or None
        # Validate once at client construction rather than on every upload, so
        # a typo surfaces at startup instead of silently disabling encryption.
        sse_extra_args(self._sse, self._sse_kms_key_id)

        self._s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=_client_config(),
            region_name="us-east-1"
        )

        # separate client for generating public URLs (localhost)
        # Boto3 uses the endpoint URL to generate the signature's Host header.
        # So we must use the external hostname here for signatures to match user's browser requests.
        self._s3_public = boto3.client(
            "s3",
            endpoint_url=effective("MINIO_PUBLIC_URL"),
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=_client_config(),
            region_name="us-east-1"
        )

        self._bucket = effective("MINIO_BUCKET_NAME")

    @property
    def s3(self):
        if self._s3 is None:
            self._init_clients()
        return self._s3

    @property
    def s3_public(self):
        if self._s3_public is None:
            self._init_clients()
        return self._s3_public

    @property
    def bucket(self):
        if self._bucket is None:
            self._init_clients()
        return self._bucket

    def ensure_bucket(self):
        try:
            self.s3.head_bucket(Bucket=self.bucket)
        except:
            self.s3.create_bucket(Bucket=self.bucket)
        
        # Set CORS to allow Playwright Trace Viewer
        try:
            self.s3.put_bucket_cors(
                Bucket=self.bucket,
                CORSConfiguration={
                    'CORSRules': [
                        {
                            'AllowedHeaders': ['*'],
                            'AllowedMethods': ['GET', 'HEAD'],
                            'AllowedOrigins': ['*'],
                            'ExposeHeaders': ['ETag'],
                            'MaxAgeSeconds': 3000
                        }
                    ]
                }
            )
        except Exception as e:
            if "NotImplemented" not in str(e):
                print(f"Failed to set CORS: {e}")

    def _extra_args(self, base: dict = None) -> dict:
        """Caller extras plus whatever server-side encryption is configured."""
        if self._s3 is None:
            self._init_clients()
        return sse_extra_args(self._sse, self._sse_kms_key_id, base=base)

    def upload_file(self, file_path: str, object_name: str, content_type: str = None):
        extra = self._extra_args({"ContentType": content_type} if content_type else None)
        self.s3.upload_file(file_path, self.bucket, object_name, ExtraArgs=extra or None)
        return object_name

    def upload_fileobj(self, fileobj, object_name: str, content_type: str = None):
        """Stream an open file-like object (e.g. FastAPI UploadFile.file)
        straight to MinIO without buffering it on disk. Used for app-build
        binaries, which can be hundreds of MB."""
        extra = self._extra_args({"ContentType": content_type} if content_type else None)
        self.s3.upload_fileobj(
            fileobj, self.bucket, object_name,
            ExtraArgs=extra or None,
        )
        return object_name

    def delete_object(self, object_name: str):
        self.s3.delete_object(Bucket=self.bucket, Key=object_name)

    def copy_object(self, source_key: str, dest_key: str):
        """Server-side copy within the bucket (used to promote a run's
        candidate screenshot into a durable baseline object).

        SSE has to be re-stated on a copy: S3 does not carry the source
        object's encryption over to the destination. Missing it here would
        leave promoted visual baselines unencrypted in a deployment that had
        SSE switched on everywhere else — the kind of gap nobody notices.
        """
        self.s3.copy_object(
            Bucket=self.bucket,
            CopySource={"Bucket": self.bucket, "Key": source_key},
            Key=dest_key,
            **self._extra_args(),
        )
        return dest_key

    def find_object(self, prefix: str, suffix: str):
        """Return the first object key under prefix ending with suffix, or None."""
        resp = self.s3.list_objects_v2(Bucket=self.bucket, Prefix=prefix)
        for obj in resp.get("Contents", []):
            if obj["Key"].endswith(suffix):
                return obj["Key"]
        return None

    def object_exists(self, object_name: str) -> bool:
        try:
            self.s3.head_object(Bucket=self.bucket, Key=object_name)
            return True
        except Exception:
            return False

    def get_presigned_url(self, object_name: str, expiration=3600):
        # Use the public client to generate URLs relative to localhost
        url = self.s3_public.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": object_name},
            ExpiresIn=expiration
        )
        return url

    def get_internal_presigned_url(self, object_name: str, expiration=3600):
        """Presigned URL against the internal endpoint (minio:9000) — for
        consumers on the docker network (workers); the public URL's
        localhost host is unreachable from inside containers."""
        return self.s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": object_name},
            ExpiresIn=expiration,
        )

    def delete_prefix(self, prefix: str) -> int:
        """Delete every object under `prefix`, returning how many were removed.

        Paginated deliberately: `list_objects_v2` caps at 1000 keys per call, so
        the unpaginated version silently left the rest behind on any run with a
        lot of screenshots — a deletion that reports success and does most of the
        job is worse than one that fails.
        """
        if not prefix:
            return 0
        deleted = 0
        token = None
        while True:
            kwargs = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            listing = self.s3.list_objects_v2(**kwargs)
            keys = [{"Key": obj["Key"]} for obj in listing.get("Contents", [])]
            if keys:
                # delete_objects also caps at 1000 per request.
                for chunk_start in range(0, len(keys), 1000):
                    chunk = keys[chunk_start:chunk_start + 1000]
                    self.s3.delete_objects(Bucket=self.bucket,
                                           Delete={"Objects": chunk})
                    deleted += len(chunk)
            if not listing.get("IsTruncated"):
                break
            token = listing.get("NextContinuationToken")
            if not token:
                break
        return deleted

    def list_prefixes(self, prefix: str, delimiter: str = "/") -> list:
        """Immediate 'directory' names under `prefix` (for orphan detection)."""
        out = []
        token = None
        while True:
            kwargs = {"Bucket": self.bucket, "Prefix": prefix, "Delimiter": delimiter}
            if token:
                kwargs["ContinuationToken"] = token
            listing = self.s3.list_objects_v2(**kwargs)
            out.extend(cp["Prefix"] for cp in listing.get("CommonPrefixes", []))
            if not listing.get("IsTruncated"):
                break
            token = listing.get("NextContinuationToken")
            if not token:
                break
        return out

    def delete_run_artifacts(self, run_id: int):
        """Delete all artifacts associated with a run ID (prefix match)"""
        try:
            count = self.delete_prefix(f"runs/{run_id}/")
            if count:
                print(f"Deleted {count} artifacts for run {run_id}")
        except Exception as e:
            print(f"Failed to delete artifacts for run {run_id}: {e}")

minio_client = MinioClient()

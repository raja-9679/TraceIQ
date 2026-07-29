import boto3
from botocore.client import Config

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

    def _init_clients(self):
        from app.services.instance_settings import effective

        # We need to parse the MINIO_ENDPOINT to handle http/https if present,
        # but boto3 expects endpoint_url to include scheme.
        endpoint = str(effective("MINIO_ENDPOINT") or "")
        if not endpoint.startswith("http"):
            endpoint = f"http://{endpoint}"
        access_key = effective("MINIO_ACCESS_KEY")
        secret_key = effective("MINIO_SECRET_KEY")

        self._s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(signature_version="s3v4"),
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
            config=Config(signature_version="s3v4"),
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

    def upload_file(self, file_path: str, object_name: str):
        self.s3.upload_file(file_path, self.bucket, object_name)
        return object_name

    def upload_fileobj(self, fileobj, object_name: str, content_type: str = None):
        """Stream an open file-like object (e.g. FastAPI UploadFile.file)
        straight to MinIO without buffering it on disk. Used for app-build
        binaries, which can be hundreds of MB."""
        extra = {"ContentType": content_type} if content_type else {}
        self.s3.upload_fileobj(
            fileobj, self.bucket, object_name,
            ExtraArgs=extra or None,
        )
        return object_name

    def delete_object(self, object_name: str):
        self.s3.delete_object(Bucket=self.bucket, Key=object_name)

    def copy_object(self, source_key: str, dest_key: str):
        """Server-side copy within the bucket (used to promote a run's
        candidate screenshot into a durable baseline object)."""
        self.s3.copy_object(
            Bucket=self.bucket,
            CopySource={"Bucket": self.bucket, "Key": source_key},
            Key=dest_key,
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

    def delete_run_artifacts(self, run_id: int):
        """Delete all artifacts associated with a run ID (prefix match)"""
        try:
            prefix = f"runs/{run_id}/"
            # List all objects with the prefix
            objects_to_delete = self.s3.list_objects_v2(Bucket=self.bucket, Prefix=prefix)
            
            if 'Contents' in objects_to_delete:
                delete_keys = [{'Key': obj['Key']} for obj in objects_to_delete['Contents']]
                self.s3.delete_objects(
                    Bucket=self.bucket,
                    Delete={'Objects': delete_keys}
                )
                print(f"Deleted {len(delete_keys)} artifacts for run {run_id}")
        except Exception as e:
            print(f"Failed to delete artifacts for run {run_id}: {e}")

minio_client = MinioClient()

import boto3
from botocore.client import Config
from app.core.config import settings

class MinioClient:
    def __init__(self):
        # We need to parse the MINIO_ENDPOINT to handle http/https if present, 
        # but boto3 expects endpoint_url to include scheme.
        endpoint = settings.MINIO_ENDPOINT
        if not endpoint.startswith("http"):
            endpoint = f"http://{endpoint}"

        self.s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=settings.MINIO_ACCESS_KEY,
            aws_secret_access_key=settings.MINIO_SECRET_KEY,
            config=Config(signature_version="s3v4"),
            region_name="us-east-1"
        )
        
        # separate client for generating public URLs (localhost)
        # Boto3 uses the endpoint URL to generate the signature's Host header.
        # So we must use the external hostname here for signatures to match user's browser requests.
        self.s3_public = boto3.client(
            "s3",
            endpoint_url="http://localhost:9000", 
            aws_access_key_id=settings.MINIO_ACCESS_KEY,
            aws_secret_access_key=settings.MINIO_SECRET_KEY,
            config=Config(signature_version="s3v4"),
            region_name="us-east-1"
        )
        
        self.bucket = settings.MINIO_BUCKET_NAME

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
            print(f"Failed to set CORS: {e}")

    def upload_file(self, file_path: str, object_name: str):
        self.s3.upload_file(file_path, self.bucket, object_name)
        return object_name

    def get_presigned_url(self, object_name: str, expiration=3600):
        # Use the public client to generate URLs relative to localhost
        url = self.s3_public.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": object_name},
            ExpiresIn=expiration
        )
        return url

minio_client = MinioClient()

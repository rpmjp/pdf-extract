import boto3
from botocore.client import Config
from .config import settings

s3 = boto3.client(
    "s3",
    endpoint_url=settings.minio_endpoint,
    aws_access_key_id=settings.minio_access_key,
    aws_secret_access_key=settings.minio_secret_key,
    config=Config(signature_version="s3v4"),
)


def ensure_bucket():
    existing = [b["Name"] for b in s3.list_buckets().get("Buckets", [])]
    if settings.minio_bucket not in existing:
        s3.create_bucket(Bucket=settings.minio_bucket)


def put_object(key: str, data: bytes, content_type: str = "application/pdf"):
    s3.put_object(
        Bucket=settings.minio_bucket, Key=key, Body=data, ContentType=content_type
    )

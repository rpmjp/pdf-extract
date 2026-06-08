"""MinIO/S3 object storage helpers for uploaded PDFs.

The database stores metadata and object keys; the raw PDF bytes live in MinIO so
large files do not bloat Postgres.  Writes optionally request server-side
encryption, and production deployments should back the bucket with durable
storage and backup policies.
"""
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

_SSE_KWARGS = {"ServerSideEncryption": "AES256"} if settings.minio_sse_enabled else {}


def ensure_bucket():
    """Create/configure the document bucket during API startup."""
    existing = [b["Name"] for b in s3.list_buckets().get("Buckets", [])]
    if settings.minio_bucket not in existing:
        s3.create_bucket(Bucket=settings.minio_bucket)

    if settings.minio_sse_enabled:
        try:
            s3.put_bucket_encryption(
                Bucket=settings.minio_bucket,
                ServerSideEncryptionConfiguration={
                    "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
                },
            )
        except Exception:
            pass  # MinIO may not support the API call if KMS not configured; SSE per-object still works


def put_object(key: str, data: bytes, content_type: str = "application/pdf"):
    """Write in-memory bytes to the document bucket."""
    s3.put_object(
        Bucket=settings.minio_bucket,
        Key=key,
        Body=data,
        ContentType=content_type,
        **_SSE_KWARGS,
    )


def put_file(key: str, path: str, content_type: str = "application/pdf"):
    """Stream a local temp file into object storage without loading it twice."""
    with open(path, "rb") as handle:
        s3.put_object(
            Bucket=settings.minio_bucket,
            Key=key,
            Body=handle,
            ContentType=content_type,
            **_SSE_KWARGS,
        )


def get_object(key: str) -> bytes:
    """Read a PDF object back from MinIO for parsing, viewing, or export."""
    resp = s3.get_object(Bucket=settings.minio_bucket, Key=key)
    return resp["Body"].read()

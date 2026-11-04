"""Content-addressed screenshot storage on S3 (MinIO locally)."""
from __future__ import annotations

import asyncio
import hashlib
import io
from functools import lru_cache

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from PIL import Image

from . import db
from .config import settings


@lru_cache
def _s3():
    s = settings()
    return boto3.client("s3", endpoint_url=s.s3_endpoint, region_name=s.aws_region,
                        config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 5, "mode": "adaptive"}))


def ensure_bucket() -> None:
    s3, bucket = _s3(), settings().s3_bucket
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError:
        s3.create_bucket(Bucket=bucket)


async def put_screenshot(png: bytes, dom_hash: str | None = None, url: str | None = None) -> str:
    """Store once per distinct image; return its sha256 (the screenshots table's primary key)."""
    sha = hashlib.sha256(png).hexdigest()
    key = f"screens/{sha[:2]}/{sha}.png"
    p = await db.pool()
    exists = await p.fetchval("SELECT 1 FROM screenshots WHERE sha256 = $1", sha)
    if not exists:
        await asyncio.to_thread(_s3().put_object, Bucket=settings().s3_bucket, Key=key, Body=png, ContentType="image/png")
        w, h = Image.open(io.BytesIO(png)).size
        await p.execute(
            "INSERT INTO screenshots (sha256, storage_key, width, height, dom_hash, url) VALUES ($1,$2,$3,$4,$5,$6) "
            "ON CONFLICT (sha256) DO NOTHING", sha, key, w, h, dom_hash, url)
    return sha


async def get_screenshot(sha: str) -> bytes | None:
    p = await db.pool()
    key = await p.fetchval("SELECT storage_key FROM screenshots WHERE sha256 = $1", sha)
    if key is None:
        return None
    obj = await asyncio.to_thread(_s3().get_object, Bucket=settings().s3_bucket, Key=key)
    return obj["Body"].read()

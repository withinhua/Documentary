"""S3-compatible object storage (Cloudflare R2 by default: zero egress fees).

The launcher holds the credentials. The rented machine only ever gets presigned URLs scoped to
single objects and expiring within the hour, so a host operator can't read or write anything else.
"""
from __future__ import annotations

import os

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from botocore.exceptions import ClientError

FAST = TransferConfig(multipart_threshold=16 * 2**20, multipart_chunksize=32 * 2**20,
                      max_concurrency=32, use_threads=True)


class Store:
    def __init__(self, bucket: str | None = None, endpoint: str | None = None,
                 key: str | None = None, secret: str | None = None):
        self.bucket = bucket or os.environ["S3_BUCKET"]
        self.s3 = boto3.client(
            "s3",
            endpoint_url=endpoint or os.environ.get("S3_ENDPOINT_URL") or None,
            aws_access_key_id=key or os.environ.get("S3_ACCESS_KEY_ID"),
            aws_secret_access_key=secret or os.environ.get("S3_SECRET_ACCESS_KEY"),
            region_name="auto",
            config=Config(signature_version="s3v4", max_pool_connections=64,
                          retries={"max_attempts": 5, "mode": "adaptive"}),
        )

    def upload(self, path, key: str) -> None:
        self.s3.upload_file(str(path), self.bucket, key, Config=FAST)

    def download(self, key: str, path) -> None:
        self.s3.download_file(self.bucket, key, str(path), Config=FAST)

    def exists(self, key: str) -> bool:
        try:
            self.s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    def read_json(self, key: str):
        import json
        try:
            return json.loads(self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read())
        except ClientError:
            return None

    def get_url(self, key: str, ttl: int = 3600) -> str:
        return self.s3.generate_presigned_url("get_object", Params={"Bucket": self.bucket, "Key": key},
                                              ExpiresIn=ttl)

    def put_url(self, key: str, ttl: int = 3600) -> str:
        return self.s3.generate_presigned_url("put_object", Params={"Bucket": self.bucket, "Key": key},
                                              ExpiresIn=ttl)

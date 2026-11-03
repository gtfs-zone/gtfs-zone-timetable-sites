"""The sites.gtfs.zone bucket over the S3 API."""

import os
from dataclasses import dataclass
from pathlib import PurePosixPath

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

# Pages refresh daily, so browsers and proxies may keep them only briefly.
CACHE_CONTROL = "public, max-age=600"

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".xml": "application/xml",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
}


@dataclass(frozen=True)
class BucketSettings:
    endpoint: str
    bucket: str
    access_key: str
    secret_key: str
    region: str

    @classmethod
    def from_env(cls) -> "BucketSettings":
        return cls(
            endpoint=os.environ["S3_ENDPOINT"],
            bucket=os.environ.get("S3_BUCKET", "sites.gtfs.zone"),
            access_key=os.environ["S3_ACCESS_KEY"],
            secret_key=os.environ["S3_SECRET_KEY"],
            region=os.environ.get("S3_REGION", "garage"),
        )


class Bucket:
    """list_keys / get / put / delete against one bucket."""

    def __init__(self, settings: BucketSettings) -> None:
        self.name = settings.bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.endpoint,
            aws_access_key_id=settings.access_key,
            aws_secret_access_key=settings.secret_key,
            region_name=settings.region,
            # Garage serves every bucket from one host, so the bucket goes in
            # the path.
            config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 3}),
        )

    def list_keys(self, prefix: str = "") -> set[str]:
        paginator = self._client.get_paginator("list_objects_v2")
        return {
            item["Key"]
            for page in paginator.paginate(Bucket=self.name, Prefix=prefix)
            for item in page.get("Contents", [])
        }

    def get(self, key: str) -> bytes | None:
        """The object's body, or None when there is no such key."""
        try:
            response = self._client.get_object(Bucket=self.name, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                return None
            raise
        with response["Body"] as stream:
            return stream.read()

    def put(self, key: str, body: bytes) -> None:
        self._client.put_object(
            Bucket=self.name,
            Key=key,
            Body=body,
            ContentType=CONTENT_TYPES.get(
                PurePosixPath(key).suffix, "application/octet-stream"
            ),
            CacheControl=CACHE_CONTROL,
        )

    def delete(self, keys: set[str]) -> None:
        batch = sorted(keys)
        # delete_objects takes at most 1000 keys per call.
        for start in range(0, len(batch), 1000):
            self._client.delete_objects(
                Bucket=self.name,
                Delete={
                    "Objects": [{"Key": key} for key in batch[start : start + 1000]],
                    "Quiet": True,
                },
            )

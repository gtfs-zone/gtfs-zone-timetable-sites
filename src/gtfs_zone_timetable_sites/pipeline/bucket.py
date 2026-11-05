"""The sites.gtfs.zone bucket over the S3 API."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

import obstore

if TYPE_CHECKING:
    from obstore.store import ObjectStore

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
    def from_env(cls) -> BucketSettings:
        return cls(
            endpoint=os.environ["S3_ENDPOINT"],
            bucket=os.environ.get("S3_BUCKET", "sites.gtfs.zone"),
            access_key=os.environ["S3_ACCESS_KEY"],
            secret_key=os.environ["S3_SECRET_KEY"],
            region=os.environ.get("S3_REGION", "garage"),
        )


def s3_store(settings: BucketSettings) -> ObjectStore:
    from obstore.store import S3Store

    return S3Store(
        settings.bucket,
        endpoint=settings.endpoint,
        region=settings.region,
        access_key_id=settings.access_key,
        secret_access_key=settings.secret_key,
        # Garage serves every bucket from one host, so the bucket goes in the
        # path.
        virtual_hosted_style_request=False,
        client_options={
            "allow_http": settings.endpoint.startswith("http://"),
            "timeout": "120s",
        },
    )


class Bucket:
    """list_keys / get / put / delete against one bucket, or against `store`."""

    def __init__(
        self, settings: BucketSettings, store: ObjectStore | None = None
    ) -> None:
        self.name = settings.bucket
        self._store = store if store is not None else s3_store(settings)

    def list_keys(self, prefix: str = "") -> set[str]:
        return {
            meta["path"]
            for batch in obstore.list(self._store, prefix=prefix or None)
            for meta in batch
        }

    def get(self, key: str) -> bytes | None:
        """The object's body, or None when there is no such key."""
        try:
            return bytes(obstore.get(self._store, key).bytes())
        except FileNotFoundError:
            return None

    def put(self, key: str, body: bytes) -> None:
        obstore.put(
            self._store,
            key,
            body,
            use_multipart=False,
            attributes={
                "Content-Type": CONTENT_TYPES.get(
                    PurePosixPath(key).suffix, "application/octet-stream"
                ),
                "Cache-Control": CACHE_CONTROL,
            },
        )

    def delete(self, keys: set[str]) -> None:
        # obstore sends S3 bulk deletes of up to 1000 keys each.
        if keys:
            obstore.delete(self._store, sorted(keys))

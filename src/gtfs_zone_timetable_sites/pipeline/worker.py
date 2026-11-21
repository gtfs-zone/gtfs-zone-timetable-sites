"""One site's download, build and publish, run in a worker process with its
own bucket and HTTP clients from the environment."""

from datetime import date

import httpx

from gtfs_zone_timetable_sites.config import USER_AGENT, Site
from gtfs_zone_timetable_sites.pipeline.bucket import (
    Bucket,
    BucketSettings,
    localities_from_env,
)
from gtfs_zone_timetable_sites.pipeline.publish import build_and_publish


def publish_one(site: Site, today: date, version: str) -> dict:
    settings = BucketSettings.from_env()
    bucket = Bucket(settings)
    with httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=120.0, follow_redirects=True
    ) as http:
        return build_and_publish(
            bucket, http, site, today, version, localities_from_env(settings)
        )

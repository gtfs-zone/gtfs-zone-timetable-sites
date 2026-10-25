"""Dagster entrypoint: the sites split into SHARDS partitions by slug, each
rebuilt daily and once more whenever a new timetable-sites version is deployed.

Each run resolves every site from sites.yaml and feeds.json, builds its shard's
sites in worker processes and then rewrites the bucket root from every shard's
file, so the root is current after every run. The Gatus heartbeat is pushed
once nearly every site has been built today, not counting feeds known to be
unusable.
"""

import functools
import os
from collections import Counter
from datetime import date
from importlib.metadata import version
from pathlib import Path

import httpx
from dagster import (
    AssetExecutionContext,
    DefaultScheduleStatus,
    DefaultSensorStatus,
    Definitions,
    MaterializeResult,
    MetadataValue,
    RunRequest,
    ScheduleEvaluationContext,
    SensorEvaluationContext,
    StaticPartitionsDefinition,
    asset,
    define_asset_job,
    schedule,
    sensor,
)

from gtfs_zone_timetable_sites.build import BASE_URL
from gtfs_zone_timetable_sites.catalog import (
    SHARDS,
    redirects,
    resolve_sites,
    shard_of,
)
from gtfs_zone_timetable_sites.config import FEEDS_URL, USER_AGENT, load_config
from gtfs_zone_timetable_sites.pipeline import indexnow
from gtfs_zone_timetable_sites.pipeline.bucket import Bucket, BucketSettings
from gtfs_zone_timetable_sites.pipeline.heartbeat import push_heartbeat
from gtfs_zone_timetable_sites.pipeline.publish import (
    ALIASES,
    BAD,
    SLUGS,
    content_key,
    duplicates,
    publish_root,
    put_json,
    read_contents,
    read_entries,
    read_json,
    run_shard,
)
from gtfs_zone_timetable_sites.pipeline.worker import publish_one

CONFIG = load_config(
    Path(os.environ.get("TIMETABLE_SITES_CONFIG", "sites.yaml")).read_text()
)
VERSION = version("gtfs-zone-timetable-sites")
WORKERS = int(os.environ.get("TIMETABLE_SITES_WORKERS", "4"))
# Address space per worker; a feed that needs more fails alone.
WORKER_MEMORY = int(os.environ.get("TIMETABLE_SITES_WORKER_MEMORY", "2500000000"))
# The share of sites that may be behind today while still pushing the heartbeat.
BEHIND_RATIO = 0.05

KEYS = [f"{shard:02d}" for shard in range(SHARDS)]
shard_partitions = StaticPartitionsDefinition(KEYS)


@asset(partitions_def=shard_partitions)
def site_pages(context: AssetExecutionContext) -> MaterializeResult:
    """Build and publish the shard's sites, then rewrite the root."""
    shard = int(context.partition_key)
    today = date.today()
    bucket = Bucket(BucketSettings.from_env())
    with httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=120.0, follow_redirects=True
    ) as http:
        feeds_doc = http.get(FEEDS_URL).raise_for_status().json()
        pinned = read_json(bucket, SLUGS) or {}
        old_aliases = read_json(bucket, ALIASES) or {}
        sites, slugs, aliases = resolve_sites(CONFIG, feeds_doc, pinned, old_aliases)
        if slugs != pinned:
            put_json(bucket, SLUGS, slugs)
        if aliases != old_aliases:
            put_json(bucket, ALIASES, aliases)
        # A copy of another site's feed is built but not indexed.
        dupes = duplicates(sites, read_contents(bucket))
        sites = [
            site.model_copy(update={"index": False}) if site.slug in dupes else site
            for site in sites
        ]
        mine = [site for site in sites if shard_of(site.slug) == shard]
        context.log.info("shard %02d: %d of %d sites", shard, len(mine), len(sites))

        publish = functools.partial(publish_one, today=today, version=VERSION)
        results, failures, skipped = run_shard(
            bucket, shard, mine, publish, today, VERSION, WORKERS, WORKER_MEMORY
        )
        for slug, error in sorted(failures.items()):
            context.log.warning("%s: %s", slug, error)
        if skipped:
            context.log.info("skipped %d known bad feeds", len(skipped))

        # After the root, which serves the IndexNow key file.
        live = {site.slug for site in sites}
        contents = read_contents(bucket)
        behind = publish_root(
            bucket,
            read_entries(bucket),
            live,
            today,
            indexnow.KEY,
            contents,
            sites,
            redirects(slugs, aliases, live),
        )
        indexnow.ping(
            http,
            [
                f"{BASE_URL}/{slug}/{path.removesuffix('index.html')}"
                for slug, result in sorted(results.items())
                for path in result["changed"]
            ],
        )
    bad = {slug for slug in behind if contents.get(slug, {}).get("outcome") in BAD}
    context.log.info(
        "%d of %d sites not built today, %d of them known bad",
        len(behind),
        len(live),
        len(bad),
    )
    if len(behind) - len(bad) <= BEHIND_RATIO * len(live):
        push_heartbeat()

    uploaded = sum(result["counts"]["uploaded"] for result in results.values())
    report = read_json(bucket, content_key(shard)) or {}
    outcomes = Counter(entry["outcome"] for entry in report.values() if entry)
    return MaterializeResult(
        metadata={
            "sites": len(mine),
            "built": len(results),
            "failed": len(failures),
            "skipped": len(skipped),
            "outcomes": MetadataValue.json(dict(outcomes)),
            "uploaded": uploaded,
            "behind": len(behind),
            "behind_bad": len(bad),
            "failures": MetadataValue.json(failures),
            "root": MetadataValue.url(f"{BASE_URL}/"),
        }
    )


sites_job = define_asset_job(
    "sites", selection=[site_pages], partitions_def=shard_partitions
)


# Two hours after feed-catalog's 09:00 UTC catalog run, which shares the run
# queue and publishes the feeds.json sites are resolved from.
@schedule(
    job=sites_job,
    cron_schedule="0 11 * * *",
    execution_timezone="UTC",
    default_status=DefaultScheduleStatus.RUNNING,
)
def daily_sites(context: ScheduleEvaluationContext) -> list[RunRequest]:
    day = context.scheduled_execution_time.date().isoformat()
    return [RunRequest(run_key=f"{key}-{day}", partition_key=key) for key in KEYS]


# Rebuilds every shard once per deployed version; Dagster skips run keys this
# sensor has already requested.
@sensor(job=sites_job, default_status=DefaultSensorStatus.RUNNING)
def new_version(context: SensorEvaluationContext) -> list[RunRequest]:
    return [
        RunRequest(run_key=f"shard-{key}-v{VERSION}", partition_key=key) for key in KEYS
    ]


defs = Definitions(
    assets=[site_pages],
    jobs=[sites_job],
    schedules=[daily_sites],
    sensors=[new_version],
)

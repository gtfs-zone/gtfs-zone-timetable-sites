"""What a GTFS zip contains, for the content report feed-catalog reads. No I/O."""

import hashlib
import io

from gtfs_zone_timetable_sites.gtfs.reader import Feed, ZipSource
from gtfs_zone_timetable_sites.gtfs.service import first_service_date, last_service_date
from gtfs_zone_timetable_sites.languages import feed_languages
from gtfs_zone_timetable_sites.places import (
    Located,
    feed_location,
    feed_place,
    place_tags,
)


def zip_digest(source: ZipSource) -> tuple[int, str]:
    """Size and sha256 of zip bytes or a seekable binary file."""
    if isinstance(source, bytes):
        return len(source), hashlib.sha256(source).hexdigest()
    source.seek(0)
    digest = hashlib.file_digest(source, "sha256").hexdigest()
    return source.seek(0, io.SEEK_END), digest


def feed_facts(
    source: ZipSource, feed: Feed, filtered: bool, stations: list[Located]
) -> dict:
    """Size, hash, feed_info, service range, agencies, counts, languages and
    location of a parsed feed, with `stations` its located stations.
    `filtered` marks counts taken after a site's route filter."""
    start = first_service_date(feed)
    end = last_service_date(feed)
    languages = feed_languages(feed)
    size, sha256 = zip_digest(source)
    facts: dict = {
        "bytes": size,
        "sha256": sha256,
        "service": {
            "start": start and start.isoformat(),
            "end": end and end.isoformat(),
        },
        "agencies": [
            {"name": agency.name, "url": agency.url, "timezone": agency.timezone}
            for agency in feed.agencies.values()
        ],
        "counts": {
            "routes": len(feed.routes),
            "stops": len(feed.stops),
            "trips": feed.trip_count,
        },
        "routeTypes": sorted({route.route_type for route in feed.routes.values()}),
        "filtered": filtered,
        "languages": {
            "default": languages.default,
            "offered": list(languages.offered),
            "readings": list(languages.readings),
        },
    }
    if info := feed.feed_info:
        facts["feedInfo"] = {
            "publisher": info.publisher_name,
            "publisherUrl": info.publisher_url,
            "version": info.version,
            "start": info.start and info.start.isoformat(),
            "end": info.end and info.end.isoformat(),
        }
    if location := feed_location(stations):
        facts["location"] = location
        facts["placeTags"] = place_tags(stations)
    if place := feed_place(stations):
        facts["place"] = {
            "countryCode": place.country_code,
            "country": place.country,
            "region": place.region,
        }
    return facts


def sniff(body: bytes) -> str:
    """What a body that is not a zip looks like: empty, html, json, xml or
    unknown."""
    head = body[:512].lstrip().lower()
    if not head:
        return "empty"
    if head.startswith((b"<!doctype html", b"<html")) or b"<html" in head:
        return "html"
    if head.startswith((b"{", b"[")):
        return "json"
    if head.startswith(b"<"):
        return "xml"
    return "unknown"

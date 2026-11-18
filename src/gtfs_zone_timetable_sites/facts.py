"""What a GTFS zip contains, for the content report feed-catalog reads. No I/O."""

import hashlib

from gtfs_zone_timetable_sites.gtfs.reader import Feed
from gtfs_zone_timetable_sites.gtfs.service import first_service_date, last_service_date
from gtfs_zone_timetable_sites.languages import feed_languages


def feed_facts(zip_bytes: bytes, feed: Feed, filtered: bool) -> dict:
    """Size, hash, feed_info, service range, agencies, counts and languages of
    a parsed feed. `filtered` marks counts taken after a site's route filter."""
    start = first_service_date(feed)
    end = last_service_date(feed)
    languages = feed_languages(feed)
    facts: dict = {
        "bytes": len(zip_bytes),
        "sha256": hashlib.sha256(zip_bytes).hexdigest(),
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
            "trips": len(feed.trips),
        },
        "routeTypes": sorted({route.route_type for route in feed.routes.values()}),
        "filtered": filtered,
        "languages": {
            "default": languages.default,
            "offered": list(languages.offered),
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

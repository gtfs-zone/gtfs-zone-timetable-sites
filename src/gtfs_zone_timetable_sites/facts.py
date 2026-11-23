"""What a GTFS zip contains, for the content report feed-catalog reads. No I/O."""

from gtfs_zone_feed_fetch.facts import Summary, zip_digest
from gtfs_zone_feed_fetch.facts import feed_facts as facts_of
from gtfs_zone_feed_fetch.places import Located

from gtfs_zone_timetable_sites.gtfs.reader import Feed, ZipSource
from gtfs_zone_timetable_sites.gtfs.service import first_service_date, last_service_date
from gtfs_zone_timetable_sites.languages import feed_languages


def feed_facts(
    source: ZipSource,
    feed: Feed,
    filtered: bool,
    stations: list[Located],
    digest: tuple[int, str] | None = None,
) -> dict:
    """Size, hash, feed_info, service range, agencies, counts, languages and
    location of a parsed feed, with `stations` its located stations.
    `filtered` marks counts taken after a site's route filter; `digest` is the
    zip's size and sha256 when already known."""
    summary = Summary(
        agencies=tuple(feed.agencies.values()),
        feed_info=feed.feed_info,
        routes=len(feed.routes),
        route_types=frozenset(route.route_type for route in feed.routes.values()),
        stops=len(feed.stops),
        trips=feed.trip_count,
        start=first_service_date(feed),
        end=last_service_date(feed),
        languages=feed_languages(feed),
    )
    return facts_of(*(digest or zip_digest(source)), summary, filtered, stations)


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

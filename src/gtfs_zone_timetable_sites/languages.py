"""The languages a feed is published in, from feed_info, agency and
translations.txt. No I/O."""

from dataclasses import replace

from gtfs_zone_feed_fetch.languages import FeedLanguages, known
from gtfs_zone_feed_fetch.languages import feed_languages as languages_of

from gtfs_zone_timetable_sites.gtfs.reader import Feed


def feed_languages(feed: Feed) -> FeedLanguages:
    """The languages of a parsed feed (`gtfs_zone_feed_fetch.languages`)."""
    return languages_of(
        feed.feed_info.lang if feed.feed_info else "",
        [agency.lang for agency in feed.agencies.values()],
        [row.language for row in feed.translations],
    )


# translations.txt fields the pages show, by table: GTFS field -> attribute of
# the reader's row. Other fields are not shown, so their rows are ignored.
APPLIED: dict[str, dict[str, str]] = {
    "agency": {"agency_name": "name", "agency_url": "url", "agency_phone": "phone"},
    "routes": {
        "route_short_name": "short_name",
        "route_long_name": "long_name",
        "route_desc": "desc",
        "route_url": "url",
    },
    "stops": {"stop_name": "name", "stop_code": "code"},
    "trips": {"trip_headsign": "headsign", "trip_short_name": "short_name"},
    "feed_info": {
        "feed_publisher_name": "publisher_name",
        "feed_publisher_url": "publisher_url",
        "feed_version": "version",
    },
}


def translate(feed: Feed, language: str) -> Feed:
    """The feed with its text in `language` (a normalized code) where
    translations.txt gives it: by `record_id`, else by `field_value`, else the
    feed's own text. The same feed when no row applies. Rows for fields the
    pages do not show (`APPLIED`) or in a code not understood are ignored."""
    by_id: dict[tuple[str, str], dict[str, str]] = {}
    by_value: dict[tuple[str, str], dict[str, str]] = {}
    for row in feed.translations:
        if not row.language or not row.translation:
            continue
        field = (row.table_name, row.field_name)
        if row.field_name not in APPLIED.get(row.table_name, {}):
            continue
        if known(row.language) != language:
            continue
        # feed_info has one row, matched with neither record_id nor field_value.
        if row.record_id or (row.table_name == "feed_info" and not row.field_value):
            by_id.setdefault(field, {})[row.record_id] = row.translation
        else:
            by_value.setdefault(field, {})[row.field_value] = row.translation
    if not by_id and not by_value:
        return feed

    def apply[T](table: str, key: str, record: T) -> T:
        changes = {}
        for field, attribute in APPLIED[table].items():
            value = getattr(record, attribute)
            text = by_id.get((table, field), {}).get(key) or by_value.get(
                (table, field), {}
            ).get(value)
            if text and text != value:
                changes[attribute] = text
        return replace(record, **changes) if changes else record

    return replace(
        feed,
        agencies={k: apply("agency", k, v) for k, v in feed.agencies.items()},
        routes={k: apply("routes", k, v) for k, v in feed.routes.items()},
        stops={k: apply("stops", k, v) for k, v in feed.stops.items()},
        trips={k: apply("trips", k, v) for k, v in feed.trips.items()},
        feed_info=feed.feed_info and apply("feed_info", "", feed.feed_info),
    )

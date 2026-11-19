"""The located stations of a parsed feed, for `gtfs_zone_feed_fetch.places`."""

from collections import Counter

from gtfs_zone_feed_fetch.places import Located, station_weights
from gtfs_zone_feed_fetch.places import locate as locate_weights

from gtfs_zone_timetable_sites.gtfs.reader import Feed


def locate(feed: Feed) -> list[Located]:
    """Every located station with stop times, reverse-geocoded."""
    counts = Counter(st.stop_id for times in feed.stop_times.values() for st in times)
    return locate_weights(station_weights(feed.stops, counts))

"""Where a feed runs: the state, province or country holding its stop times.

Each station is reverse-geocoded offline with `reverse_geocode`'s bundled
GeoNames places and weighted by its stop times. A region with at least
`SHARE` of them names the feed; otherwise a country with that share does;
otherwise nothing does.
"""

from collections import Counter
from dataclasses import dataclass

import reverse_geocode

from gtfs_zone_timetable_sites.gtfs.reader import Feed

SHARE = 0.8


@dataclass(frozen=True, slots=True)
class Place:
    # Empty for a feed named by its country alone.
    region: str
    country: str
    country_code: str
    # The stop-time weighted center of the feed's stations in the place.
    lat: float
    lon: float


def station_weights(feed: Feed) -> dict[tuple[float, float], int]:
    """Stop times per located station, by its coordinates."""
    counts = Counter(
        feed.station(st.stop_id) for times in feed.stop_times.values() for st in times
    )
    weights: Counter[tuple[float, float]] = Counter()
    for stop_id, n in counts.items():
        stop = feed.stops.get(stop_id)
        if stop is not None and stop.lat is not None and stop.lon is not None:
            weights[stop.lat, stop.lon] += n
    return weights


def feed_place(feed: Feed) -> Place | None:
    """The feed's region, else its country, else None."""
    weights = station_weights(feed)
    if not weights:
        return None
    points = list(weights)
    found = reverse_geocode.search(points)
    total = sum(weights.values())
    regions: Counter[tuple[str, str, str]] = Counter()
    for point, place in zip(points, found, strict=True):
        key = (place["country_code"], place["country"], place.get("state", ""))
        regions[key] += weights[point]
    countries: Counter[tuple[str, str]] = Counter()
    for (code, country, _), n in regions.items():
        countries[code, country] += n

    (code, country, region), n = regions.most_common(1)[0]
    if not region or n < SHARE * total:
        (code, country), n = countries.most_common(1)[0]
        region = ""
        if n < SHARE * total:
            return None
    inside = [
        (point, weights[point])
        for point, place in zip(points, found, strict=True)
        if place["country_code"] == code
        and (not region or place.get("state", "") == region)
    ]
    weight = sum(w for _, w in inside)
    return Place(
        region=region,
        country=country,
        country_code=code,
        lat=round(sum(p[0] * w for p, w in inside) / weight, 5),
        lon=round(sum(p[1] * w for p, w in inside) / weight, 5),
    )


def place_label(place: Place | None, site_title: str) -> str:
    """'Texas' or 'United States', or "" when the site title already names it."""
    if place is None:
        return ""
    label = place.region or place.country
    return "" if label.casefold() in site_title.casefold() else label

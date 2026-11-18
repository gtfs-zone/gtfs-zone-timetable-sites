from conftest import fixture_files, make_zip

from gtfs_zone_timetable_sites.gtfs.reader import read_feed
from gtfs_zone_timetable_sites.pages import place_jsonld
from gtfs_zone_timetable_sites.places import (
    Place,
    feed_location,
    feed_place,
    locate,
    place_label,
    place_tags,
)

OREGON = Place("Oregon", "United States", "US", 45.5, -122.7)


def branching(stops: list[tuple[float, float]]) -> bytes:
    files = fixture_files("branching")
    rows = files["stops.txt"].splitlines()
    files["stops.txt"] = "\n".join(
        [rows[0]]
        + [
            ",".join([*row.split(",")[:2], str(lat), str(lon), ""])
            for row, (lat, lon) in zip(rows[1:], stops, strict=True)
        ]
    )
    return make_zip(files)


def test_feed_place_is_the_region_holding_the_stop_times():
    portland = [(45.5 + i / 100, -122.65 - i / 100) for i in range(7)]
    place = feed_place(locate(read_feed(branching(portland))))
    assert (place.region, place.country_code) == ("Oregon", "US")


def test_a_feed_across_regions_is_named_by_its_country():
    place = feed_place(locate(read_feed(make_zip(fixture_files("branching")))))
    assert (place.region, place.country) == ("", "United States")


def test_place_label():
    assert place_label(OREGON, "TriMet") == "Oregon"
    assert place_label(OREGON, "Oregon Coast Transit") == ""
    national = Place("", "United States", "US", 39.0, -98.0)
    assert place_label(national, "Amtrak") == "United States"
    assert place_label(None, "Amtrak") == ""
    assert place_label(OREGON, "TriMet", "Portland") == "Portland, Oregon"
    assert place_label(None, "TriMet", "Portland") == "Portland"
    # Parts the title already names are left out.
    assert place_label(OREGON, "Portland Streetcar", "Portland") == "Oregon"
    assert place_label(OREGON, "Oregon Coast Transit", "Newport") == "Newport"
    assert place_label(OREGON, "TriMet", "Oregon") == "Oregon"


def test_place_jsonld():
    (page,) = place_jsonld(OREGON, "Oregon", "https://x/")
    address = page["spatialCoverage"]["address"]
    assert (address["addressRegion"], address["addressCountry"]) == ("Oregon", "US")
    assert place_jsonld(None, "", "https://x/") == []


def test_feed_location_and_place_tags():
    portland = [(45.5 + i / 100, -122.65 - i / 100) for i in range(7)]
    stations = locate(read_feed(branching(portland)))
    location = feed_location(stations)
    assert location["bbox"] == [-122.71, 45.5, -122.65, 45.56]
    assert location["stations"] == 7
    lat, lon = location["center"]
    assert 45.5 <= lat <= 45.56 and -122.71 <= lon <= -122.65
    tags = place_tags(stations)
    assert tags == {"countries": ["United States"], "regions": ["Oregon"]}


def test_place_tags_rank_regions_by_stop_times():
    stations = locate(read_feed(make_zip(fixture_files("branching"))))
    tags = place_tags(stations)
    assert tags["countries"] == ["United States"]
    weights = {s.region: 0 for s in stations}
    for s in stations:
        weights[s.region] += s.weight
    assert len(tags["regions"]) > 1
    assert [weights[r] for r in tags["regions"]] == sorted(
        (weights[r] for r in tags["regions"]), reverse=True
    )


def test_nothing_located():
    assert feed_location([]) is None
    assert feed_place([]) is None
    assert place_tags([]) == {"countries": [], "regions": []}

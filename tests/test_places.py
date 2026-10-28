from conftest import fixture_files, make_zip

from gtfs_zone_timetable_sites.gtfs.reader import read_feed
from gtfs_zone_timetable_sites.pages import place_jsonld
from gtfs_zone_timetable_sites.places import Place, feed_place, place_label

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
    place = feed_place(read_feed(branching(portland)))
    assert (place.region, place.country_code) == ("Oregon", "US")


def test_a_feed_across_regions_is_named_by_its_country():
    place = feed_place(read_feed(make_zip(fixture_files("branching"))))
    assert (place.region, place.country) == ("", "United States")


def test_place_label():
    assert place_label(OREGON, "TriMet") == "Oregon"
    assert place_label(OREGON, "Oregon Coast Transit") == ""
    national = Place("", "United States", "US", 39.0, -98.0)
    assert place_label(national, "Amtrak") == "United States"
    assert place_label(None, "Amtrak") == ""


def test_place_jsonld():
    (page,) = place_jsonld(OREGON, "Oregon", "https://x/")
    address = page["spatialCoverage"]["address"]
    assert (address["addressRegion"], address["addressCountry"]) == ("Oregon", "US")
    assert place_jsonld(None, "", "https://x/") == []

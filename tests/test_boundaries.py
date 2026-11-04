import pytest

from gtfs_zone_timetable_sites.boundaries import country, region_of, world


@pytest.mark.parametrize(
    ("code", "lat", "lon", "expected"),
    [
        ("us", 42.36, -71.06, "US-MA"),
        ("fr", 45.76, 4.83, "FR-ARA"),
        # Outside British Columbia's simplified coast.
        ("ca", 49.28, -123.12, "CA-BC"),
        ("gb", 51.5, -0.12, "GB-ENG"),
    ],
)
def test_region_of(code, lat, lon, expected):
    region = region_of(code, lat, lon)
    assert region is not None and region.id == expected


def test_region_of_far_from_any_region():
    assert region_of("us", 0.0, 0.0) is None
    assert region_of("zz", 42.36, -71.06) is None


def test_bundled_data():
    keys = {f["properties"]["key"] for f in world()["features"]}
    assert {"us", "fr", "no", "jp"} <= keys
    assert country("fr")["license"]
    assert country("zz") is None

from datetime import date

import pytest
from conftest import fixture_zip, make_zip

from gtfs_zone_timetable_sites.gtfs.reader import parse_color, parse_time, read_feed


def test_parse_time():
    assert parse_time("06:45:00") == 6 * 3600 + 45 * 60
    assert parse_time(" 6:45:00") == 6 * 3600 + 45 * 60
    assert parse_time("25:10:30") == 25 * 3600 + 10 * 60 + 30
    assert parse_time("") is None


def test_parse_color():
    assert parse_color("#00ff00") == "00FF00"
    assert parse_color(" 002599 ") == "002599"
    assert parse_color("ZZZZZZ") is None
    assert parse_color("FFF") is None
    assert parse_color("") is None


def test_reads_rows_and_sorts_stop_times():
    feed = read_feed(fixture_zip("branching"))
    assert feed.agencies["a"].timezone == "America/New_York"
    assert feed.routes["R"].name == "4"
    assert feed.trips["north1"].headsign == "North"
    assert [st.stop_id for st in feed.stop_times["north1"]] == ["A", "B", "C", "D"]
    assert feed.calendars["DAILY"].start == date(2026, 1, 1)
    assert feed.shapes == {}
    assert feed.frequencies == {}


def test_missing_times_and_timepoint_column():
    feed = read_feed(fixture_zip("missing-timepoints"))
    times = feed.stop_times["t1"]
    assert times[2].arrival is None and times[2].departure is None
    assert all(st.timepoint is None for st in times)


def test_calendar_dates_only():
    feed = read_feed(fixture_zip("calendar-dates-only"))
    assert feed.calendars == {}
    assert feed.calendar_dates["SAT"][date(2026, 10, 10)] is True


def test_route_filter_drops_trips_and_stop_times():
    feed = read_feed(fixture_zip("branching"), keep_route=lambda r: r.route_type == 2)
    assert feed.routes == {} and feed.trips == {} and feed.stop_times == {}


def test_nested_directory_and_bom():
    files = {
        f"gtfs/{path}": body
        for path, body in {
            "agency.txt": "﻿agency_id,agency_name,agency_url,agency_timezone\n"
            "a,A,https://a,UTC\n",
            "routes.txt": "route_id,route_type\nR,3\n",
            "trips.txt": "route_id,service_id,trip_id\nR,S,T\n",
            "stop_times.txt": "trip_id,stop_id,stop_sequence\n",
            "stops.txt": "stop_id,stop_name\n",
            "calendar_dates.txt": "service_id,date,exception_type\n",
        }.items()
    }
    feed = read_feed(make_zip(files))
    assert feed.agencies["a"].timezone == "UTC"
    assert feed.trips["T"].direction_id is None


def test_missing_required_file():
    with pytest.raises(ValueError, match="stop_times"):
        read_feed(make_zip({"agency.txt": "agency_id\n"}))


def test_amenity_columns():
    feed = read_feed(fixture_zip("amenities"))
    assert (feed.trips["t1"].bikes_allowed, feed.trips["t3"].bikes_allowed) == (1, 0)
    assert feed.trips["t2"].wheelchair_accessible == 1
    assert feed.stops["B"].wheelchair_boarding == 2
    # A platform with 0 inherits its parent station's value.
    assert feed.wheelchair_boarding("STA2") == 1
    assert feed.wheelchair_boarding("missing") == 0


def test_amenity_columns_default_to_unknown():
    feed = read_feed(fixture_zip("branching"))
    trip = feed.trips["north1"]
    assert (trip.bikes_allowed, trip.wheelchair_accessible) == (0, 0)
    assert feed.stops["A"].wheelchair_boarding == 0


def routes_feed(routes: str) -> bytes:
    return make_zip(
        {
            "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\na,A,https://a,UTC\n",
            "routes.txt": routes,
            "trips.txt": "route_id,service_id,trip_id\n",
            "stop_times.txt": "trip_id,stop_id,stop_sequence\n",
            "stops.txt": "stop_id,stop_name\n",
            "calendar_dates.txt": "service_id,date,exception_type\n",
        }
    )


def test_color_shared_by_every_route_is_dropped():
    feed = read_feed(
        routes_feed(
            "route_id,route_type,route_color,route_text_color\n"
            "R1,3,ff0000,FFFFFF\nR2,3,FF0000,FFFFFF\nR3,3,,\n"
        )
    )
    assert all(r.color is None and r.text_color is None for r in feed.routes.values())


def test_distinct_and_single_route_colors_are_kept():
    feed = read_feed(
        routes_feed("route_id,route_type,route_color\nR1,3,FF0000\nR2,3,0000FF\n")
    )
    assert (feed.routes["R1"].color, feed.routes["R2"].color) == ("FF0000", "0000FF")
    feed = read_feed(routes_feed("route_id,route_type,route_color\nR1,3,FF0000\n"))
    assert feed.routes["R1"].color == "FF0000"

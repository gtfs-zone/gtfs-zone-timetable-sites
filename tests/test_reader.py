from datetime import date
from decimal import Decimal

import pytest
from conftest import fixture_files, fixture_zip, make_zip

from gtfs_zone_timetable_sites.gtfs.reader import (
    FareAttribute,
    FareLegRule,
    FareProduct,
    FareRule,
    FareTransferRule,
    Timeframe,
    Translation,
    parse_color,
    parse_time,
    read_feed,
)


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


def test_flat_v1_fares():
    fares = read_feed(fixture_zip("fares-flat-v1")).fares
    assert fares.attributes["express"] == FareAttribute(
        fare_id="express",
        agency_id="a",
        price=Decimal("2.00"),
        currency="USD",
        payment_method=0,
        transfers=1,
        transfer_duration=3600,
    )
    local = fares.attributes["local"]
    assert (local.transfers, local.transfer_duration) == (0, None)
    assert fares.rules[2] == FareRule("express", "X", "", "", "")
    assert len(fares.rules) == 3
    assert fares.products == {} and fares.leg_rules == ()


def test_zone_v1_fares():
    feed = read_feed(fixture_zip("fares-zone-v1"))
    assert [feed.stops[s].zone_id for s in "ABC"] == ["1", "1", "2"]
    near, near_ic = feed.fares.attributes["near"], feed.fares.attributes["near_ic"]
    assert (near.payment_method, near.price) == (0, Decimal("200"))
    assert (near_ic.payment_method, near_ic.price) == (1, Decimal("190"))
    # No agency_id column reads as empty, empty transfers as unlimited.
    assert near.agency_id == "" and near.transfers is None
    assert FareRule("far", "", "2", "1", "") in feed.fares.rules
    assert len(feed.fares.rules) == 8


def test_v2_fares():
    feed = read_feed(fixture_zip("fares-v2"))
    fares = feed.fares
    assert feed.routes["B1"].network_id == "bus"
    assert feed.routes["CR"].network_id == ""
    assert fares.route_networks == {"CR": "rail"}
    assert fares.networks == {"bus": "Local Bus", "rail": "Commuter Rail"}
    assert fares.areas == {"z1": "Zone 1", "z2": ""}
    assert fares.stop_areas == {"z1": ("A", "B"), "z2": ("C",)}
    assert fares.media["card"].name == "Smart card"
    assert fares.media["card"].media_type == 2
    assert fares.products["bus"] == (
        FareProduct("bus", "Bus fare", "cash", Decimal("2.00"), "USD"),
        FareProduct("bus", "Bus fare", "card", Decimal("1.70"), "USD"),
    )
    assert fares.leg_rules[0] == FareLegRule(
        leg_group_id="leg_bus",
        network_id="bus",
        from_area_id="",
        to_area_id="",
        from_timeframe_group_id="any",
        to_timeframe_group_id="",
        fare_product_id="bus",
        rule_priority=None,
        transfer_only=False,
    )
    assert fares.leg_rules[3].rule_priority == 1
    assert fares.leg_rules[4].transfer_only
    assert fares.transfer_rules == (
        FareTransferRule("leg_bus", "leg_rail", None, 7200, 1, 0, ""),
        FareTransferRule("leg_bus", "leg_bus", -1, None, None, 1, "bus"),
    )
    assert fares.timeframes == {
        "any": (Timeframe(None, None, "DAILY"),),
        "peak": (Timeframe(7 * 3600, 9 * 3600, "DAILY"),),
    }
    assert fares.attributes == {} and fares.rules == ()


def test_missing_fare_files_are_empty():
    feed = read_feed(fixture_zip("branching"))
    assert all(not getattr(feed.fares, name) for name in feed.fares.__slots__)
    assert all(stop.zone_id == "" for stop in feed.stops.values())


def test_header_only_fare_files_are_empty():
    # BC Ferries ships fare files with headers and no rows, and an areas.txt
    # with a non-standard wkt column.
    files = fixture_files("branching") | {
        "fare_attributes.txt": "agency_id,fare_id,price,currency_type,"
        "payment_method,transfers,transfer_duration\n",
        "fare_rules.txt": "fare_id,route_id,origin_id,destination_id,contains_id\n",
        "areas.txt": "area_id,wkt\n",
        "farezone_attributes.txt": "zone_id,zone_name\n",
    }
    fares = read_feed(make_zip(files)).fares
    assert all(not getattr(fares, name) for name in fares.__slots__)


def test_languages_and_translations():
    feed = read_feed(fixture_zip("languages"))
    assert feed.feed_info is not None and feed.feed_info.lang == "fr"
    assert [agency.lang for agency in feed.agencies.values()] == ["FR", "fr-CA"]
    assert len(feed.translations) == 5
    assert feed.translations[0] == Translation(
        table_name="stops",
        field_name="stop_name",
        language="en",
        translation="Central Station",
        record_id="A",
        record_sub_id="",
        field_value="",
    )
    by_value = feed.translations[3]
    assert (by_value.record_id, by_value.field_value) == ("", "Hôtel de ville")


def test_missing_and_header_only_translations_are_empty():
    assert read_feed(fixture_zip("branching")).translations == ()
    files = fixture_files("branching") | {
        "translations.txt": "table_name,field_name,language,translation\n"
    }
    feed = read_feed(make_zip(files))
    assert feed.translations == ()
    assert feed.agencies["a"].lang == ""

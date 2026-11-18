from decimal import Decimal

import pytest
from conftest import fixture_files, fixture_zip, make_zip

from gtfs_zone_timetable_sites.fares import (
    Fare,
    FlatFare,
    NoFares,
    Transfers,
    ZoneFares,
    route_fares,
)
from gtfs_zone_timetable_sites.gtfs.reader import read_feed
from gtfs_zone_timetable_sites.pages import (
    fare_badges,
    fare_charts,
    fare_lines,
    transfers_text,
)


def fares_of(files: dict[str, str], trip_ids: list[str]):
    return route_fares(read_feed(make_zip(files)), trip_ids)


def test_flat_fare_per_route():
    feed = read_feed(fixture_zip("fares-flat-v1"))
    assert route_fares(feed, ["r1"]) == FlatFare(
        (Fare(Decimal("1.25"), "USD", 0, Transfers(0, None)),)
    )
    assert route_fares(feed, ["x"]) == FlatFare(
        (Fare(Decimal("2.00"), "USD", 0, Transfers(1, 3600)),)
    )


def test_merged_route_lists_each_price():
    feed = read_feed(fixture_zip("fares-flat-v1"))
    fares = route_fares(feed, ["x", "r1"])
    assert isinstance(fares, FlatFare)
    assert [fare.price for fare in fares.fares] == [Decimal("1.25"), Decimal("2.00")]


def test_no_fare_files_or_header_only():
    assert route_fares(read_feed(fixture_zip("overnight")), ["t1"]) == NoFares()
    files = fixture_files("fares-flat-v1")
    files["fare_attributes.txt"] = files["fare_attributes.txt"].splitlines()[0]
    files["fare_rules.txt"] = files["fare_rules.txt"].splitlines()[0]
    assert fares_of(files, ["r1"]) == NoFares()


def test_zone_rules_covering_every_stop_are_flat():
    files = fixture_files("fares-zone-v1")
    files["stop_times.txt"] = "\n".join(files["stop_times.txt"].splitlines()[:3])
    fares = fares_of(files, ["l1"])
    assert isinstance(fares, FlatFare)
    # Two prices: listed cheapest first, each with its payment method.
    assert [(f.price, f.payment_method) for f in fares.fares] == [
        (Decimal("190"), 1),
        (Decimal("200"), 0),
    ]


def test_one_price_drops_payment_method():
    files = fixture_files("fares-zone-v1")
    files["stop_times.txt"] = "\n".join(files["stop_times.txt"].splitlines()[:3])
    files["fare_attributes.txt"] = files["fare_attributes.txt"].replace(
        "near_ic,190", "near_ic,200"
    )
    fares = fares_of(files, ["l1"])
    assert fares == FlatFare(
        (Fare(Decimal("200"), "JPY", None, Transfers(None, None)),)
    )


def test_contains_rules_match_the_listed_zones():
    files = fixture_files("fares-zone-v1")
    files["fare_rules.txt"] = "fare_id,route_id,origin_id,destination_id,contains_id\n"
    files["fare_rules.txt"] += "near,L,,,1\nfar,L,,,1\nfar,L,,,2\n"
    files["stop_times.txt"] = "\n".join(files["stop_times.txt"].splitlines()[:3])
    fares = fares_of(files, ["l1"])
    assert isinstance(fares, FlatFare)
    assert [fare.price for fare in fares.fares] == [Decimal("200")]


def test_fares_without_rules_apply_to_their_agency():
    files = fixture_files("fares-flat-v1")
    del files["fare_rules.txt"]
    fares = fares_of(files, ["r1"])
    assert isinstance(fares, FlatFare)
    assert [fare.price for fare in fares.fares] == [Decimal("1.25"), Decimal("2.00")]


def test_unknown_fare_id_raises():
    files = fixture_files("fares-flat-v1")
    files["fare_rules.txt"] += "missing,R1,,,\n"
    with pytest.raises(KeyError, match="missing"):
        fares_of(files, ["r1"])


def test_transfers_text():
    assert transfers_text(Transfers(None, None)) == ""
    assert transfers_text(Transfers(0, None)) == "no free transfers"
    assert transfers_text(Transfers(0, 0)) == "no free transfers"
    assert transfers_text(Transfers(0, 5400)) == "valid for 90 min"
    assert transfers_text(Transfers(1, 3600)) == "1 free transfer within 60 min"
    assert transfers_text(Transfers(2, None)) == "2 free transfers"
    assert transfers_text(Transfers(None, 7200)) == (
        "unlimited free transfers within 120 min"
    )
    assert transfers_text(Transfers(1, 3600), "fr") == (
        "1 correspondance gratuite pendant 60 min"
    )


def test_fare_lines():
    one = FlatFare((Fare(Decimal("1.25"), "USD", 0, Transfers(None, None)),))
    assert fare_lines(one) == ("$1.25",)
    assert fare_lines(one, "fr") == ("1,25\xa0$US",)
    two = FlatFare(
        (
            Fare(Decimal("190"), "JPY", 1, Transfers(None, None)),
            Fare(Decimal("200"), "JPY", 0, Transfers(1, 3600)),
        )
    )
    assert fare_lines(two) == (
        "¥190 paid before boarding",
        "¥200 paid on board, 1 free transfer within 60 min",
    )
    assert fare_lines(NoFares()) == ()


def prices(fares: ZoneFares) -> list[list[list[int]]]:
    return [[[int(f.price) for f in cell] for cell in row] for row in fares.cells]


def test_zone_fares_by_origin_and_destination():
    fares = fares_of(fixture_files("fares-zone-v1"), ["l1"])
    assert isinstance(fares, ZoneFares)
    assert fares.labels == ("1", "2")
    assert fares.zones == ("1", "2")
    assert prices(fares) == [[[190, 200], [280, 300]], [[280, 300], [190, 200]]]


def test_zone_order_reads_direction_1_backwards():
    files = fixture_files("fares-zone-v1")
    files["trips.txt"] += "L,DAILY,l2,1\n"
    files["stop_times.txt"] += (
        "l2,09:00:00,09:00:00,C,1\nl2,09:05:00,09:05:00,B,2\nl2,09:10:00,09:10:00,A,3\n"
    )
    fares = fares_of(files, ["l2", "l1"])
    assert isinstance(fares, ZoneFares)
    assert fares.labels == ("1", "2")


def per_stop_files() -> dict[str, str]:
    """A zone per stop, with fares by distance."""
    files = fixture_files("fares-zone-v1")
    files["stops.txt"] = (
        "stop_id,stop_name,stop_lat,stop_lon,zone_id\n"
        "A,Alpha,42.0,-73.0,a\nB,Bravo,42.1,-73.1,b\nC,Charlie,42.2,-73.2,c\n"
    )
    files["fare_rules.txt"] = "fare_id,route_id,origin_id,destination_id,contains_id\n"
    for a, b, fare in [("a", "b", "near"), ("b", "c", "near"), ("a", "c", "far")]:
        files["fare_rules.txt"] += f"{fare},,{a},{b},\n{fare},,{b},{a},\n"
    return files


def test_zone_per_stop_is_labelled_by_stop():
    fares = fares_of(per_stop_files(), ["l1"])
    assert isinstance(fares, ZoneFares)
    assert fares.labels == ("Alpha", "Bravo", "Charlie")
    assert fares.zones == ()
    assert prices(fares) == [[[], [200], [300]], [[200], [], [200]], [[300], [200], []]]
    assert fare_badges(fares) == {}


def test_same_price_between_every_stop_is_flat():
    files = per_stop_files()
    files["fare_rules.txt"] = files["fare_rules.txt"].replace("far,", "near,")
    fares = fares_of(files, ["l1"])
    assert isinstance(fares, FlatFare)
    assert [fare.price for fare in fares.fares] == [Decimal("200")]


def test_contains_rules_by_zones_passed():
    files = fixture_files("fares-zone-v1")
    files["fare_rules.txt"] = "fare_id,route_id,origin_id,destination_id,contains_id\n"
    files["fare_rules.txt"] += "near,L,,,1\nfar,L,,,1\nfar,L,,,2\n"
    fares = fares_of(files, ["l1"])
    assert isinstance(fares, ZoneFares)
    assert prices(fares) == [[[200], [300]], [[300], []]]


def test_rules_naming_zones_off_the_route_are_skipped():
    files = fixture_files("fares-zone-v1")
    files["fare_rules.txt"] += "far,,1,9,\n"
    assert isinstance(fares_of(files, ["l1"]), ZoneFares)


def test_fare_charts_triangle_per_payment_method():
    fares = fares_of(fixture_files("fares-zone-v1"), ["l1"])
    charts = fare_charts(fares)
    assert [chart.caption for chart in charts] == [
        "Paid on board",
        "Paid before boarding",
    ]
    on_board = charts[0]
    assert on_board.heads == ("Zone 1", "Zone 2")
    assert on_board.rows == (
        ("Zone 1", ("¥200",)),
        ("Zone 2", ("¥300", "¥200")),
    )
    assert on_board.note.startswith("Fare between two zones")
    assert fare_badges(fares) == {"1": "1", "2": "2"}


def test_fare_charts_per_stop_without_diagonal():
    [chart] = fare_charts(fares_of(per_stop_files(), ["l1"]))
    assert chart.caption == ""
    assert chart.heads == ("Alpha", "Bravo")
    assert chart.rows == (("Bravo", ("¥200",)), ("Charlie", ("¥300", "¥200")))


def test_fare_charts_one_way_prices_are_a_square():
    files = per_stop_files()
    files["fare_rules.txt"] = files["fare_rules.txt"].replace("far,,c,a,", "near,,c,a,")
    [chart] = fare_charts(fares_of(files, ["l1"]))
    assert chart.heads == ("Alpha", "Bravo", "Charlie")
    assert chart.rows[2] == ("Charlie", ("¥200", "¥200", ""))
    assert chart.note.startswith("Fare from the stop")
    assert fare_charts(FlatFare(())) == ()

from decimal import Decimal

import pytest
from conftest import fixture_files, fixture_zip, make_zip

from gtfs_zone_timetable_sites.fares import (
    Fare,
    FlatFare,
    NoFares,
    Transfers,
    route_fares,
)
from gtfs_zone_timetable_sites.gtfs.reader import read_feed
from gtfs_zone_timetable_sites.pages import fare_lines, transfers_text


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
    # Stops in two zones: not flat.
    assert fares_of(fixture_files("fares-zone-v1"), ["l1"]) == NoFares()


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

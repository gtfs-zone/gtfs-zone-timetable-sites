from decimal import Decimal

import pytest
from conftest import fixture_files, fixture_zip, make_zip

from gtfs_zone_timetable_sites.fares import (
    Fare,
    FareGroup,
    FlatFare,
    LegGroup,
    NoFares,
    SiteFares,
    TransferRule,
    Transfers,
    ZoneFares,
    route_fares,
    site_fares,
)
from gtfs_zone_timetable_sites.gtfs.reader import read_feed
from gtfs_zone_timetable_sites.pages import (
    fare_badges,
    fare_charts,
    fare_lines,
    fare_text,
    transfer_text,
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
    assert fare_badges(fares) == {"A": "Zone 1", "B": "Zone 1", "C": "Zone 2"}


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


def test_v2_flat_fare_lists_media_when_prices_differ():
    feed = read_feed(fixture_zip("fares-v2"))
    fares = route_fares(feed, ["b1"])
    # The transfer-only rule (rail_1) is left out.
    assert fares == FlatFare(
        (
            Fare(Decimal("1.70"), "USD", product="Bus fare", media=("Smart card",)),
            Fare(Decimal("2.00"), "USD", product="Bus fare", media=("Cash",)),
        )
    )
    assert fare_lines(fares) == (
        "$1.70 with Smart card (Bus fare)",
        "$2.00 with Cash (Bus fare)",
    )


def test_v2_one_price_per_product_drops_media():
    files = fixture_files("fares-v2")
    files["fare_products.txt"] = files["fare_products.txt"].replace(
        "card,1.70", "card,2.00"
    )
    fares = fares_of(files, ["b1"])
    assert fares == FlatFare((Fare(Decimal("2.00"), "USD", product="Bus fare"),))
    assert fare_lines(fares) == ("$2.00 (Bus fare)",)


def test_v2_zone_fares_by_area_with_priority_and_timeframe():
    feed = read_feed(fixture_zip("fares-v2"))
    fares = route_fares(feed, ["cr1"])
    assert isinstance(fares, ZoneFares)
    assert fares.named
    assert fares.labels == ("Zone 1", "z2")
    rail_1 = Fare(Decimal("2.40"), "USD", product="Zone 1 rail fare")
    rail_2 = Fare(Decimal("3.70"), "USD", product="Zone 2 rail fare")
    peak = Fare(Decimal("4.25"), "USD", product="Peak rail fare", timeframe="peak")
    assert fares.cells == (((rail_1, peak), (rail_2, peak)), ((), ()))
    assert fare_badges(fares) == {"A": "Zone 1", "B": "Zone 1", "C": "z2"}
    [chart] = fare_charts(fares)
    assert chart.heads == ("Zone 1", "z2")
    assert chart.rows == (
        ("Zone 1", ("$2.40 / $4.25", "$3.70 / $4.25")),
        ("z2", ("", "")),
    )
    line = fare_text(peak, False, "en", feed.fares.timeframes, "12h")
    assert line == "$4.25 (Peak rail fare), 7:00 AM to 9:00 AM"


def test_v2_empty_area_matches_only_without_an_exact_rule():
    files = fixture_files("fares-v2")
    # Without rule_priority an empty area is a fallback, not a wildcard.
    files["fare_leg_rules.txt"] = (
        "leg_group_id,network_id,from_area_id,to_area_id,fare_product_id\n"
        "leg_rail,rail,z1,z1,rail_1\n"
        "leg_rail,rail,z1,,rail_2\n"
        "leg_rail,rail,,,rail_peak\n"
    )
    fares = fares_of(files, ["cr1"])
    assert isinstance(fares, ZoneFares)
    assert [
        [[int(f.price * 100) for f in cell] for cell in row] for row in fares.cells
    ] == [
        [[240], [370]],
        [[425], [425]],
    ]


def test_v2_same_prices_from_different_products_are_flat():
    files = fixture_files("fares-v2")
    files["fare_products.txt"] = (
        "fare_product_id,fare_product_name,fare_media_id,amount,currency\n"
        "rail_1,Rail fare,card,2.40,USD\n"
        "rail_2,Rail ticket,card,2.40,USD\n"
        "rail_peak,Rail fare,card,2.40,USD\n"
        "bus,Bus fare,cash,2.00,USD\n"
    )
    files["fare_leg_rules.txt"] = (
        "leg_group_id,network_id,from_area_id,to_area_id,fare_product_id\n"
        "leg_rail,rail,z1,,rail_1\n"
        "leg_rail,rail,,,rail_2\n"
    )
    fares = fares_of(files, ["cr1"])
    assert fares == FlatFare(
        (
            Fare(Decimal("2.40"), "USD", product="Rail fare"),
            Fare(Decimal("2.40"), "USD", product="Rail ticket"),
        )
    )


def test_v2_places_in_no_area_are_other_stops():
    files = fixture_files("fares-v2")
    files["stop_areas.txt"] = "area_id,stop_id\nz2,C\n"
    files["fare_leg_rules.txt"] = (
        "leg_group_id,network_id,from_area_id,to_area_id,fare_product_id\n"
        "leg_rail,rail,z2,,rail_2\n"
        "leg_rail,rail,,,rail_1\n"
    )
    fares = fares_of(files, ["cr1"])
    assert isinstance(fares, ZoneFares)
    assert fares.labels == ("", "z2")
    assert fare_charts(fares)[0].heads == ("Other stops", "z2")
    assert fare_badges(fares) == {"C": "z2"}


def test_v2_unknown_product_raises():
    files = fixture_files("fares-v2")
    files["fare_leg_rules.txt"] += "leg_bus,bus,,,missing,,,,\n"
    with pytest.raises(KeyError, match="missing"):
        fares_of(files, ["b1"])


def test_site_fares_v1_by_agency():
    fares = site_fares(read_feed(fixture_zip("fares-flat-v1")))
    assert fares == SiteFares(
        (
            FareGroup(
                "Test Transit",
                (
                    Fare(Decimal("1.25"), "USD", 0, Transfers(0, None)),
                    Fare(Decimal("2.00"), "USD", 0, Transfers(1, 3600)),
                ),
            ),
        )
    )


def test_site_fares_v1_skips_fares_for_other_routes():
    files = fixture_files("fares-flat-v1")
    files["fare_rules.txt"] = "fare_id,route_id\nlocal,R1\nexpress,GONE\n"
    fares = site_fares(read_feed(make_zip(files)))
    assert fares is not None
    assert [f.price for f in fares.groups[0].fares] == [Decimal("1.25")]


def test_site_fares_v1_zones_list_each_payment_method():
    fares = site_fares(read_feed(fixture_zip("fares-zone-v1")))
    assert fares is not None
    assert [(f.price, f.payment_method) for f in fares.groups[0].fares] == [
        (Decimal("190"), 1),
        (Decimal("200"), 0),
        (Decimal("280"), 1),
        (Decimal("300"), 0),
    ]
    assert fares.transfers == ()


def test_site_fares_v2_by_network_with_every_media():
    fares = site_fares(read_feed(fixture_zip("fares-v2")))
    assert fares is not None
    assert [group.name for group in fares.groups] == ["Commuter Rail", "Local Bus"]
    bus = fares.groups[1].fares
    assert [(f.price, f.media, f.product) for f in bus] == [
        (Decimal("1.70"), ("Smart card",), "Bus fare"),
        (Decimal("2.00"), ("Cash",), "Bus fare"),
    ]
    assert fares.groups[0].fares[-1].timeframe == "peak"
    rail = LegGroup(
        ("Commuter Rail",),
        ("Peak rail fare", "Zone 1 rail fare", "Zone 2 rail fare"),
        "Zone 1",
    )
    bus_legs = LegGroup(("Local Bus",), ("Bus fare",))
    assert fares.transfers[0] == TransferRule(bus_legs, rail, None, 7200, 0, ())
    assert fares.transfers[1].count == -1
    assert [f.price for f in fares.transfers[1].fee] == [
        Decimal("1.70"),
        Decimal("2.00"),
    ]


def test_site_fares_none_without_fare_files():
    assert site_fares(read_feed(fixture_zip("overnight"))) is None


def test_site_fares_unknown_leg_group_raises():
    files = fixture_files("fares-v2")
    files["fare_transfer_rules.txt"] += "missing,leg_bus,,,,0,\n"
    with pytest.raises(KeyError, match="missing"):
        site_fares(read_feed(make_zip(files)))


def test_transfer_text():
    fares = site_fares(read_feed(fixture_zip("fares-v2")))
    assert fares is not None
    assert [transfer_text(rule) for rule in fares.transfers] == [
        "Bus fare (Local Bus) to Peak rail fare / Zone 1 rail fare / Zone 2 rail"
        " fare (Commuter Rail, from Zone 1): free, within 120 min",
        "Bus fare (Local Bus) to Bus fare (Local Bus): both fares plus $1.70 with"
        " Smart card (Bus fare) / $2.00 with Cash (Bus fare), any number of"
        " transfers",
    ]
    fee = Fare(Decimal("0.70"), "USD", product="Subway transfer")
    rule = TransferRule(None, None, 3, 3600, 2, (fee,))
    assert transfer_text(rule) == (
        "any fare to any fare: $0.70 (Subway transfer) for both, up to 3"
        " transfers, within 60 min"
    )

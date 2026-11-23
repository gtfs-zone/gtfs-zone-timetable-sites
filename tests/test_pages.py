from dataclasses import replace
from datetime import date

from conftest import fixture_files, fixture_zip, make_zip
from gtfs_zone_vocab.route_types import ROUTE_TYPES

from gtfs_zone_timetable_sites.build import site_feed, site_timetables
from gtfs_zone_timetable_sites.config import Site
from gtfs_zone_timetable_sites.gtfs.reader import Route, read_feed
from gtfs_zone_timetable_sites.gtfs.service import day_types
from gtfs_zone_timetable_sites.i18n import UI_LOCALES, t
from gtfs_zone_timetable_sites.pages import (
    MODE_ORDER,
    ColumnHead,
    HeadsignSpan,
    badge_colors,
    booking_lines,
    booking_marks,
    booking_views,
    cell_view,
    clock,
    clock_label,
    day_views,
    dot_text,
    feed_amenities,
    flex_notes,
    google_link,
    headsign_spans,
    join_and,
    mode_name,
    modes_label,
    note_letter,
    route_labels,
    route_slugs,
    route_view,
    slugify,
    table_title,
)
from gtfs_zone_timetable_sites.timetable import (
    Cell,
    Column,
    Row,
    Timetable,
    route_timetables,
)


def route(route_id="r", short="", long="", color=None, text=None) -> Route:
    return Route(
        route_id=route_id,
        agency_id="",
        short_name=short,
        long_name=long,
        desc="",
        route_type=3,
        color=color,
        text_color=text,
        sort_order=None,
        url="",
    )


def test_clock_12h_marks_pm_and_next_day():
    assert clock(9 * 3600, "12h").text == "9:00"
    assert not clock(9 * 3600, "12h").pm
    assert clock(12 * 3600 + 5 * 60, "12h").text == "12:05"
    assert clock(12 * 3600, "12h").pm
    late = clock(24 * 3600 + 10 * 60, "12h")
    assert (late.text, late.pm, late.days) == ("12:10", False, 1)


def test_clock_24h():
    shown = clock(25 * 3600 + 30 * 60, "24h")
    assert (shown.text, shown.pm, shown.days) == ("01:30", False, 1)


def test_clock_multiple_days():
    assert clock(2 * 24 * 3600 + 10 * 60, "24h").days == 2
    before = clock(-30 * 60, "24h")
    assert (before.text, before.days) == ("23:30", -1)
    assert clock_label(49 * 3600, "12h") == "1:00 AM +2"


def test_badge_text_falls_back_to_contrasting_color():
    assert badge_colors(route(color="FFFF00", text="FFFFFF")) == ("FFFF00", "000000")
    assert badge_colors(route(color="002599")) == ("002599", "FFFFFF")
    assert badge_colors(route(color="002599", text="FFCC00")) == ("002599", "FFCC00")
    assert badge_colors(route("1")) == ("5B87C8", "000000")


def test_route_slugs_are_unique():
    routes = [route("a", long="Hudson <> Albany"), route("b", long="Hudson Albany")]
    assert route_slugs(routes, {}) == {"a": "hudson-albany", "b": "hudson-albany-2"}
    assert slugify("  Copake ") == "copake"


def test_route_slugs_skip_reserved_names():
    routes = [route("a", short="EN"), route("b", long="Fares")]
    assert route_slugs(routes, {}, {"en", "fares"}) == {"a": "en-2", "b": "fares-2"}


def test_headsign_spans_merge_adjacent_runs():
    heads = tuple(
        ColumnHead(number="", headsign=h, every="", shaded=shaded)
        for h, shaded in [
            ("A", True),
            ("A", True),
            ("B", False),
            ("A", True),
            ("A", False),
        ]
    )
    assert headsign_spans(heads) == (
        HeadsignSpan("A", 2, True),
        HeadsignSpan("B", 1, False),
        HeadsignSpan("A", 2, False),
    )


def test_dot_text_keeps_line_colors_readable_on_white():
    assert dot_text("2E7D32") == "2E7D32"
    assert dot_text("FFD100") == "1F2937"


def test_table_title_skips_to_for_services_and_loops():
    assert table_title(["Albany", "Hudson"]) == "To Albany / Hudson"
    assert table_title(["Patriots Game Train"]) == "Patriots Game Train"
    assert table_title(["↷ 41 Clockwise"]) == "↷ 41 Clockwise"


def test_route_labels_name_agencies_of_shared_route_names():
    feed = read_feed(
        make_zip(
            {
                "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\n"
                "a,Amtrak,https://example.org,America/New_York\n"
                "m,MARC,https://example.org,America/New_York\n",
                "routes.txt": "route_id,agency_id,route_long_name,route_type\n"
                "1,a,Acela,2\n2,a,Commuter Rail,2\n3,m,Commuter Rail,2\n",
                "stops.txt": "stop_id,stop_name\n",
                "trips.txt": "route_id,service_id,trip_id\n",
                "stop_times.txt": "trip_id,stop_id,stop_sequence\n",
                "calendar_dates.txt": "service_id,date,exception_type\n",
            }
        )
    )
    routes = list(feed.routes.values())
    labels = route_labels(feed, routes)
    assert labels == {"2": "Amtrak", "3": "MARC"}
    assert route_slugs(routes, labels)["3"] == "commuter-rail-marc"


def test_google_link():
    assert google_link(42.0, -73.5) == (
        "https://www.google.com/maps/search/?api=1&query=42.00000,-73.50000"
    )
    assert google_link(None, None) == ""


def test_rows_link_stops_to_google_maps():
    site = Site(slug="t", url="https://example.org/g.zip")
    feed = site_feed(fixture_zip("branching"), site, date(2026, 10, 5))
    [(_, tables, _, _)] = site_timetables(feed, site, date(2026, 10, 5))
    days = day_views(feed, tables, "12h")
    rows = [row for day in days for table in day.tables for row in table.rows]
    assert rows
    assert all(row.google.startswith("https://www.google.com/maps/") for row in rows)


def test_tables_sharing_a_title_name_their_ends():
    site = Site(slug="t", url="https://example.org/g.zip")
    feed = site_feed(fixture_zip("shared-terminus"), site, date(2026, 10, 5))
    [(_, tables, _, _)] = site_timetables(feed, site, date(2026, 10, 5))
    [day] = day_views(feed, tables, "12h")
    titles = [table.title for table in day.tables]
    assert sorted(titles) == ["Alpha to Golf", "Hotel to Golf", "To Alpha"]


def test_approximate_rows_are_dimmed_with_a_legend_key():
    site = Site(slug="t", url="https://example.org/g.zip")
    feed = site_feed(fixture_zip("end-timepoints"), site, date(2026, 10, 5))
    [(_, tables, _, _)] = site_timetables(feed, site, date(2026, 10, 5))
    [day] = day_views(feed, tables, "12h")
    [table] = day.tables
    assert [row.approx for row in table.rows] == [False, *[True] * 5, False]
    assert "Times in grey are approximate." in [key.text for key in table.legend]


def test_no_approximate_key_without_timepoint_values():
    site = Site(slug="t", url="https://example.org/g.zip")
    feed = site_feed(fixture_zip("missing-timepoints"), site, date(2026, 10, 5))
    [(_, tables, _, _)] = site_timetables(feed, site, date(2026, 10, 5))
    [day] = day_views(feed, tables, "12h")
    [table] = day.tables
    assert not any(row.approx for row in table.rows)
    assert "Times in grey are approximate." not in [k.text for k in table.legend]


def test_days_sorted_by_runs():
    site = Site(slug="t", url="https://example.org/g.zip")
    feed = site_feed(fixture_zip("calendar-dates-only"), site, date(2026, 10, 5))
    [(_, tables, _, _)] = site_timetables(feed, site, date(2026, 10, 5))
    views = day_views(feed, tables, "12h")
    assert [v.runs for v in views] == sorted((v.runs for v in views), reverse=True)
    assert views[0].runs == 3
    assert [t.anchor for t in views[0].tables] == [f"{views[0].anchor}-1"]


def test_mode_names():
    codes = (0, 2, 3, 4, 6, 11, 12, 109, 201, 401, 715, 1000, 1100, 1200, 1300, 99)
    assert [mode_name(t) for t in codes] == [
        "light rail",
        "train",
        "bus",
        "ferry",
        "gondola",
        "trolleybus",
        "monorail",
        "train",
        "coach",
        "subway",
        "bus",
        "ferry",
        "air",
        "ferry",
        "gondola",
        "transit",
    ]
    assert modes_label(["bus", "train", "bus"]) == "Bus and train"
    assert modes_label([]) == "Transit"
    assert join_and(["A", "B", "C"]) == "A, B, and C"


def test_every_mode_is_ordered_and_named():
    modes = {mode_name(rt.code) for rt in ROUTE_TYPES} | {"transit"}
    assert modes <= set(MODE_ORDER)
    for locale in UI_LOCALES:
        for mode in MODE_ORDER:
            t(locale, f"mode.{mode}")


def test_endpoints_and_first_and_last_trips():
    site = Site(slug="t", url="https://example.org/g.zip")
    feed = site_feed(fixture_zip("overnight"), site, date(2026, 10, 5))
    [(night_owl, tables, _, _)] = site_timetables(feed, site, date(2026, 10, 5))
    view = route_view(night_owl, "1", tables)
    assert view.mode == "bus"
    assert view.endpoints == ("Alpha", "Charlie")
    [day] = day_views(feed, tables, "12h")
    assert (day.first, day.last) == ("6:00 AM", "11:40 PM")


def test_frequency_trips_last_start_before_headway_end():
    site = Site(slug="t", url="https://example.org/g.zip")
    feed = site_feed(fixture_zip("frequencies"), site, date(2026, 10, 5))
    [(_, tables, _, _)] = site_timetables(feed, site, date(2026, 10, 5))
    [day] = day_views(feed, tables, "24h")
    assert (day.first, day.last) == ("07:00", "17:40")


def amenity_table():
    site = Site(slug="t", url="https://example.org/g.zip")
    feed = site_feed(fixture_zip("amenities"), site, date(2026, 10, 5))
    [(_, tables, _, _)] = site_timetables(feed, site, date(2026, 10, 5))
    _, marked = feed_amenities(tables)
    [day] = day_views(feed, tables, "12h", marked)
    [table] = day.tables
    return day, table


def test_flag_stop_marks():
    _, table = amenity_table()
    assert [cell.mark for cell in table.rows[1].cells] == ["", "f", "df"]
    texts = [key.text for key in table.legend]
    assert "f: flag stop, stops only on request." in texts
    assert "d: drop off only." in texts


def test_mixed_amenities_get_icons_and_keys():
    day, table = amenity_table()
    assert day.icons and table.show_amenities
    assert [head.bikes for head in table.heads] == [True, False, False]
    assert not any(head.wheelchair for head in table.heads)
    assert [row.wheelchair for row in table.rows] == [True, False, True]
    assert [(key.icon, key.text) for key in table.legend if key.icon] == [
        ("bike", "Bikes allowed"),
        ("wheelchair", "Wheelchair accessible stop"),
    ]


def timetable(stop, *trips):
    """One stop with `stop` as its wheelchair_boarding, one column per trip
    value used for both bikes and wheelchair."""
    return Timetable(
        route_id="R",
        direction_id=0,
        headsigns=(),
        day_type=None,
        rows=(Row("S", "S", True, wheelchair=stop),),
        columns=tuple(Column("t", "", "", (), bikes=v, wheelchair=v) for v in trips),
    )


def test_feed_amenities():
    assert feed_amenities([timetable(1, 1), timetable(1, 1)]) == (
        (
            "Bikes allowed on all trips.",
            "All trips are wheelchair accessible.",
            "All stops are wheelchair accessible.",
        ),
        frozenset(),
    )
    assert feed_amenities([timetable(2, 2)])[0] == (
        "No bikes on any trip.",
        "No trips are wheelchair accessible.",
        "No stops are wheelchair accessible.",
    )
    assert feed_amenities([timetable(0, 0, 2)]) == ((), frozenset())
    assert feed_amenities([timetable(1, 1), timetable(1, 0)]) == (
        ("All stops are wheelchair accessible.",),
        frozenset({"bikes", "trips"}),
    )


def test_note_letters_go_past_z():
    assert [note_letter(i) for i in (0, 25, 26, 27, 701, 702)] == [
        "A",
        "Z",
        "AA",
        "AB",
        "ZZ",
        "AAA",
    ]


def flex_feed_tables():
    feed = read_feed(fixture_zip("flex"))
    types = day_types(feed, feed.trips, date(2026, 10, 5), 7)
    return feed, route_timetables(feed, "D", types)


def test_window_cell_shows_both_ends():
    cell = Cell(None, None, window=(9 * 3600, 21 * 3600), booking=("x", "y"))
    view = cell_view(cell, "12h", {"x": "b1", "y": "b2"})
    assert view.time is None and not view.untimed
    assert view.window == (clock(9 * 3600, "12h"), clock(21 * 3600, "12h"))
    assert view.booking == "b1 b2"


def test_booking_marks_number_rules_only_when_several():
    _, tables = flex_feed_tables()
    assert booking_marks(tables) == {"br_day": "b1", "br_now": "b2"}
    one = [
        replace(
            tables[0],
            columns=tuple(
                replace(
                    column,
                    cells=tuple(
                        replace(c, booking=()) if c and "br_now" in c.booking else c
                        for c in column.cells
                    ),
                )
                for column in tables[0].columns
            ),
        )
    ]
    assert booking_marks(one) == {"br_day": "b"}


def test_booking_views_read_rules_as_given():
    feed, _ = flex_feed_tables()
    day, now, missing = booking_views(
        feed, {"br_day": "b1", "br_now": "b2", "gone": "b3"}, "12h"
    )
    assert day.lines == (
        "Book on an earlier day.",
        "Book by 5:00 PM, 1 day before travel.",
        "Call the day before.",
    )
    assert (day.phone, day.info_url, day.booking_url) == (
        "555-0100",
        "https://example.com/ride",
        "",
    )
    assert now.lines == ("Book in real time.",) and now.booking_url
    assert missing.lines == (
        "The feed names booking rule gone, which booking_rules.txt does not have.",
    )


def test_booking_lines_without_a_type_or_with_another():
    feed, _ = flex_feed_tables()
    rule = feed.flex.booking_rules["br_now"]
    assert booking_lines(replace(rule, booking_type=None), "24h")[0] == (
        "The feed gives no booking type."
    )
    assert booking_lines(replace(rule, booking_type=7), "24h")[0] == "booking_type 7."
    lines = booking_lines(
        replace(
            rule,
            booking_type=1,
            prior_notice_duration_min=1,
            prior_notice_duration_max=90,
        ),
        "24h",
    )
    assert lines[1:] == ["At least 1 minute ahead.", "At most 90 minutes ahead."]


def test_flex_notes_say_what_was_not_read():
    files = fixture_files("flex")
    files["location.geojson"] = files.pop("locations.geojson")
    files["booking_rules.txt"] = "booking_rule_id,booking_type\nbr,soon\n"
    assert flex_notes(read_feed(make_zip(files))) == (
        "The feed's GTFS-Flex data could not be read: "
        "booking_rules.txt: booking_type 'soon' is not valid",
        "The feed has location.geojson, which GTFS names locations.geojson; "
        "nothing is read from it.",
    )


def test_areas_given_in_stop_id_are_noted_and_shown_as_areas():
    files = fixture_files("flex")
    files["stop_times.txt"] = files["stop_times.txt"].replace(
        "d1,,,,area_2,,2,", "d1,,,area_2,,,2,"
    )
    feed = read_feed(make_zip(files))
    assert flex_notes(feed) == (
        "The feed gives GTFS-Flex areas in stop_times.stop_id, which GTFS keeps "
        "for stops.txt; they are shown as areas.",
    )
    types = day_types(feed, feed.trips, date(2026, 10, 5), 7)
    (table,) = route_timetables(feed, "D", types)
    assert (table.rows[1].stop_id, table.rows[1].kind) == ("area_2", "location")

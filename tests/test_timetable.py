from datetime import date

from conftest import fixture_files, fixture_zip, make_zip

from gtfs_zone_timetable_sites.gtfs.reader import read_feed
from gtfs_zone_timetable_sites.gtfs.service import day_types
from gtfs_zone_timetable_sites.timetable import (
    format_time,
    lcs_pairs,
    merge_sequences,
    route_timetables,
    stop_order,
    to_text,
)

MONDAY = date(2026, 10, 5)


def tables(name, **options):
    feed = read_feed(fixture_zip(name))
    types = day_types(feed, feed.trips, MONDAY, 7)
    return route_timetables(feed, "R", types, **options)


def times(table, time_format="12h"):
    return [
        [format_time(c.time, time_format) if c else "" for c in column.cells]
        for column in table.columns
    ]


def test_lcs_pairs():
    assert lcs_pairs("ABCD", "ACD") == [(0, 0), (2, 1), (3, 2)]
    assert lcs_pairs("ABCA", "A") == [(0, 0)]


def test_merge_inserts_missing_stops_in_order():
    assert merge_sequences(["ABD", "ACD"]) == list("ABCD")
    assert merge_sequences(["BCD", "ABC"]) == list("ABCD")
    assert merge_sequences(["ABCA", "BCA"]) == list("ABCA")
    assert merge_sequences([]) == []


def test_stop_order_keeps_each_stop_once_and_branches_contiguous():
    # A fold would repeat stops here; the topological order does not.
    patterns = ["ABCDE", "ACE", "BXYD", "ABCDEZ"]
    order = stop_order(patterns, [5, 3, 1, 1])
    assert sorted(order) == sorted(set("ABCDEXYZ"))
    for pattern in patterns:
        assert [s for s in order if s in pattern] == list(pattern)
    assert "".join(order).find("XY") >= 0
    # Opposite directions are a cycle: fall back to the fold.
    assert stop_order(["AB", "BA"], [1, 1]) == merge_sequences(["AB", "BA"])


def test_format_time():
    assert format_time(6 * 3600 + 5 * 60, "12h") == "6:05a"
    assert format_time(12 * 3600, "12h") == "12:00p"
    assert format_time(0, "12h") == "12:00a"
    assert format_time(24 * 3600 + 10 * 60, "12h") == "12:10a+1"
    assert format_time(25 * 3600 + 10 * 60 + 59, "24h") == "01:10+1"
    assert format_time(18 * 3600, "24h") == "18:00"
    assert format_time(49 * 3600 + 10 * 60, "24h") == "01:10+2"
    assert format_time(-30 * 60, "12h") == "11:30p-1"


def test_overnight_sorted_and_marked_next_day():
    (table,) = tables("overnight", timepoints="all")
    assert [c.trip_id for c in table.columns] == ["early", "noon", "late"]
    assert times(table)[-1][:2] == ["11:40p", "11:55p"]


def test_multiday_marks_each_day():
    (table,) = tables("multiday", timepoints="all")
    assert times(table) == [["10:00p", "11:30p+1", "2:10a+2"]]


def test_stop_timezone_shifts_times():
    (table,) = tables("overnight", timepoints="all")
    assert table.rows[2].timezone == "America/Chicago"
    assert table.rows[0].timezone is None
    assert [t[2] for t in times(table)] == ["5:30a", "11:30a", "11:10p"]


def test_frequencies():
    (table,) = tables("frequencies", timepoints="all")
    headway, *exact = table.columns
    assert headway.headway is not None
    assert headway.headway.headway_secs == 600
    assert times(table)[0] == ["7:00a", "7:05a"]
    assert [t[0] for t in times(table)[1:]] == ["5:00p", "5:20p", "5:40p"]
    assert all(column.headway is None for column in exact)


def test_branches_split_by_headsign_but_short_turns_stay():
    north, east, back = tables("branching", timepoints="all")
    assert north.headsigns == ("North",)
    assert [row.stop_id for row in north.rows] == ["A", "B", "C", "D"]
    assert [c.trip_id for c in north.columns] == ["north1", "north2"]
    assert east.headsigns == ("East",)
    assert back.direction_id == 1
    assert back.headsigns == ("Alpha", "Charlie")
    assert [row.stop_id for row in back.rows] == ["D", "E", "C", "B", "A"]
    by_trip = {c.trip_id: c for c in back.columns}
    assert by_trip["back_short"].cells[1] is None
    assert by_trip["back_short"].cells[3:] == (None, None)
    assert by_trip["back_skip"].cells[2] is None


def test_untimed_stops_are_served_but_blank():
    (table,) = tables("missing-timepoints", timepoints="all")
    cell = table.columns[0].cells[2]
    assert cell is not None and cell.time is None
    assert "|" in to_text(table)


def test_auto_timepoints_capped_and_spread_without_flags():
    (table,) = tables("missing-timepoints", cap=4)
    chosen = [row.stop_id for row in table.rows if row.timepoint]
    assert chosen[0] == "A" and chosen[-1] == "G"
    assert len(chosen) == 4
    assert "C" not in chosen and "E" not in chosen


def test_timepoint_flags_win_in_auto():
    files = fixture_files("missing-timepoints")
    header, *lines = files["stop_times.txt"].splitlines()
    flagged = [line + (",1" if line.split(",")[3] in "AD" else ",0") for line in lines]
    files["stop_times.txt"] = "\n".join([header + ",timepoint", *flagged])
    feed = read_feed(make_zip(files))
    types = day_types(feed, feed.trips, MONDAY, 7)
    (table,) = route_timetables(feed, "R", types, cap=4)
    # Flags plus the pattern's ends
    assert [row.stop_id for row in table.rows if row.timepoint] == ["A", "D", "G"]


def test_tables_under_the_cap_show_every_stop():
    out, loop, back = tables("shared-terminus")
    assert all(row.timepoint for table in (out, loop, back) for row in table.rows)
    assert [row.stop_id for row in back.rows] == ["G", "F", "C", "B", "A"]


def test_headsignless_trips_sharing_a_terminus_split():
    out, loop, back = tables("shared-terminus")
    assert [c.trip_id for c in out.columns] == ["out"]
    assert [row.stop_id for row in out.rows] == ["A", "B", "C", "F", "G"]
    assert [c.trip_id for c in loop.columns] == ["loop"]
    assert [row.stop_id for row in loop.rows] == ["H", "I", "J", "F", "G"]
    assert back.direction_id == 1


def test_platforms_merge_into_their_station():
    feed = read_feed(
        make_zip(
            {
                "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\n"
                "a,Test,https://example.org,America/New_York\n",
                "routes.txt": "route_id,route_short_name,route_type\nR,1,2\n",
                "stops.txt": "stop_id,stop_name,stop_lat,stop_lon,parent_station\n"
                "A,Alpha,42,-73,\nS,Central,42.1,-73,\n"
                "S1,Central Track 1,42.1,-73,S\nS2,Central Track 2,42.1,-73,S\n"
                "B,Bravo,42.2,-73,\nC,Charlie,42.2,-73.1,\n",
                "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,"
                "friday,saturday,sunday,start_date,end_date\n"
                "D,1,1,1,1,1,1,1,20260101,20261231\n",
                "trips.txt": "route_id,service_id,trip_id\nR,D,x\nR,D,y\n",
                "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,"
                "stop_sequence\n"
                "x,06:00:00,06:00:00,A,1\nx,06:10:00,06:10:00,S1,2\n"
                "x,06:20:00,06:20:00,B,3\n"
                "y,07:00:00,07:00:00,A,1\ny,07:10:00,07:10:00,S2,2\n"
                "y,07:20:00,07:20:00,C,3\n",
            }
        )
    )
    types = day_types(feed, feed.trips, MONDAY, 7)
    (table,) = route_timetables(feed, "R", types)
    assert [row.name for row in table.rows] == ["Alpha", "Central", "Bravo", "Charlie"]


def test_amenities_carried_to_columns_rows_and_cells():
    (table,) = tables("amenities", timepoints="all")
    assert [c.bikes for c in table.columns] == [1, 2, 0]
    assert [c.wheelchair for c in table.columns] == [1, 1, 1]
    assert [r.wheelchair for r in table.rows] == [1, 2, 1]
    requests = [[bool(c and c.request) for c in col.cells] for col in table.columns]
    assert requests == [[False] * 3, [False, True, False], [False, True, False]]


def test_station_with_mixed_platforms_is_unknown():
    files = fixture_files("amenities")
    files["stops.txt"] = files["stops.txt"].replace(
        "STA2,Station Track 2,42.0,-73.0,0,STA,0",
        "STA2,Station Track 2,42.0,-73.0,0,STA,2",
    )
    feed = read_feed(make_zip(files))
    (table,) = route_timetables(feed, "R", day_types(feed, feed.trips, MONDAY, 7))
    assert table.rows[0].wheelchair == 0

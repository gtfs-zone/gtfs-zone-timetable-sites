from datetime import date, timedelta

from conftest import fixture_files, fixture_zip, make_zip

from gtfs_zone_timetable_sites.gtfs.reader import read_feed
from gtfs_zone_timetable_sites.gtfs.service import (
    Services,
    day_name,
    day_types,
    exception_name,
    horizon_start,
    key_services,
    max_day_shift,
    route_windows,
    service_rule,
    span_label,
    start_day_offset,
    trip_note,
    weekday_name,
)

MONDAY = date(2026, 10, 5)


def test_weekday_names():
    assert weekday_name(range(5)) == "Weekday"
    assert weekday_name([5]) == "Saturday"
    assert weekday_name([5, 6]) == "Weekend"
    assert weekday_name(range(7)) == "Daily"
    assert weekday_name([0, 1, 2, 3]) == "Monday to Thursday"
    assert weekday_name([1, 4]) == "Tuesday and Friday"
    assert weekday_name([0, 2, 4]) == "Monday, Wednesday, and Friday"


def test_calendar_dates_only_day_types():
    feed = read_feed(fixture_zip("calendar-dates-only"))
    assert Services(feed).active(date(2026, 10, 10)) == {"SAT"}
    types = day_types(feed, feed.trips, MONDAY, 14)
    saturday, special = types
    assert day_name(saturday) == "Saturday" and saturday.regular
    assert saturday.dates == (
        date(2026, 10, 10),
        date(2026, 10, 14),
        date(2026, 10, 17),
    )
    assert saturday.extra == (date(2026, 10, 14),)
    assert saturday.missing == ()
    assert not special.regular
    assert special.dates == (date(2026, 10, 12),)
    assert day_name(special) == "Mon, Oct 12"
    assert special.trip_ids == {"special"}


def test_unused_services_are_dropped_and_change_nothing():
    """Calendars no trip uses, as in feeds with a calendar per train."""
    files = fixture_files("calendar-dates-only")
    unused = range(500)
    files["calendar.txt"] = (
        "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
        "start_date,end_date\n"
        + "".join(f"U{i},1,1,1,1,1,1,1,20250101,20271231\n" for i in unused)
    )
    files["calendar_dates.txt"] += "".join(
        f"U{i},20261012,2\nU{i},20261013,1\n" for i in unused
    )
    plain = read_feed(fixture_zip("calendar-dates-only"))
    feed = read_feed(make_zip(files))
    assert not feed.calendars
    assert feed.calendar_dates == plain.calendar_dates
    assert day_types(feed, feed.trips, MONDAY, 14) == day_types(
        plain, plain.trips, MONDAY, 14
    )
    assert horizon_start(feed, date(2026, 1, 1)) == date(2026, 10, 10)


def test_services_index_matches_a_scan():
    feed = read_feed(fixture_zip("overnight"))
    feed.calendar_dates["DAILY"] = {date(2026, 10, 12): False}
    feed.calendar_dates["EXTRA"] = {date(2026, 10, 13): True}
    services = Services(feed)
    for offset in range(14):
        day = MONDAY + timedelta(days=offset)
        expected = {
            sid
            for sid, c in feed.calendars.items()
            if c.start <= day <= c.end and c.weekdays[day.weekday()]
        }
        expected |= {
            sid for sid, dates in feed.calendar_dates.items() if dates.get(day)
        }
        expected -= {
            sid for sid, dates in feed.calendar_dates.items() if dates.get(day) is False
        }
        assert services.active(day) == expected
    assert services.first(["EXTRA"]) == services.last(["EXTRA"]) == date(2026, 10, 13)
    assert services.first(["NONE"]) is None


def test_removed_date_is_missing_from_regular_day_type():
    feed = read_feed(fixture_zip("overnight"))
    feed.calendar_dates["DAILY"] = {date(2026, 10, 12): False}
    (daily,) = day_types(feed, feed.trips, MONDAY, 14)
    assert day_name(daily) == "Daily"
    assert daily.missing == (date(2026, 10, 12),)
    assert len(daily.dates) == 13


def test_horizon_starts_at_first_service_date():
    feed = read_feed(fixture_zip("calendar-dates-only"))
    assert horizon_start(feed, date(2026, 1, 1)) == date(2026, 10, 10)
    # Sunday Nov 1 starts on Monday Oct 26.
    assert horizon_start(feed, date(2026, 11, 1)) == date(2026, 10, 26)


def test_horizon_starts_on_monday_all_week():
    feed = read_feed(fixture_zip("calendar-dates-only"))
    week = [date(2026, 11, 23) + timedelta(days=i) for i in range(7)]
    assert {horizon_start(feed, day) for day in week} == {date(2026, 11, 23)}
    assert horizon_start(feed, date(2026, 11, 30)) == date(2026, 11, 30)


def test_horizon_starts_on_first_service_date_mid_week():
    feed = read_feed(fixture_zip("calendar-dates-only"))
    # Service starts Saturday Oct 10; Monday Oct 5 is before it.
    assert horizon_start(feed, date(2026, 10, 7)) == date(2026, 10, 10)
    assert horizon_start(feed, date(2026, 10, 11)) == date(2026, 10, 10)


def weekday_feed() -> bytes:
    """Weekday trips, a Friday-only trip, a trip issued under two trip_ids,
    and a trip retimed at its last stop from Oct 12."""
    trips = {
        "w1": ("WK_A", "06:00", "06:10"),
        "w1b": ("WK_B", "06:00", "06:10"),
        "w2": ("WK", "07:00", "07:10"),
        "w3": ("WK", "08:00", "08:10"),
        "w4": ("WK", "09:00", "09:10"),
        "w5": ("WK", "10:00", "10:10"),
        "fri": ("FRI", "11:00", "11:10"),
        "old": ("WK_A", "12:00", "12:10"),
        "new": ("WK_B", "12:00", "12:15"),
    }
    return make_zip(
        {
            "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\n"
            "a,Test,https://example.org,America/New_York\n",
            "routes.txt": "route_id,route_short_name,route_type\nR,1,3\n",
            "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n"
            "A,A,42,-73\nB,B,42.1,-73\n",
            "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,"
            "saturday,sunday,start_date,end_date\n"
            "WK,1,1,1,1,1,0,0,20260101,20261231\n"
            "WK_A,1,1,1,1,1,0,0,20260101,20261011\n"
            "WK_B,1,1,1,1,1,0,0,20261012,20261231\n"
            "FRI,0,0,0,0,1,0,0,20260101,20261231\n",
            "trips.txt": "route_id,service_id,trip_id\n"
            + "".join(
                f"R,{service},{trip}\n" for trip, (service, _, _) in trips.items()
            ),
            "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,"
            "stop_sequence\n"
            + "".join(
                f"{trip},{a}:00,{a}:00,A,1\n{trip},{b}:00,{b}:00,B,2\n"
                for trip, (_, a, b) in trips.items()
            ),
        }
    )


def test_near_identical_days_merge_with_trip_notes():
    feed = read_feed(weekday_feed())
    (weekday,) = day_types(feed, feed.trips, MONDAY, 14)
    assert day_name(weekday) == "Weekday" and weekday.regular
    assert len(weekday.dates) == 10
    assert weekday.trip_ids == {"w1", "w2", "w3", "w4", "w5", "fri", "old", "new"}
    assert trip_note(weekday, "w2") == ""
    assert trip_note(weekday, "w1") == ""
    assert trip_note(weekday, "fri") == "Fridays only"
    assert trip_note(weekday, "old") == "Until Oct 9"
    assert trip_note(weekday, "new") == "From Oct 12"


def test_date_ranges():
    horizon = [MONDAY + timedelta(days=n) for n in range(21)]
    week2 = horizon[7:12]
    assert exception_name(week2, horizon) == "Weekday, Oct 12 to Oct 16"
    assert exception_name([horizon[5]], horizon) == "Sat, Oct 10"
    assert exception_name([horizon[5], horizon[19]], horizon) == (
        "Sat, Oct 10, Sat, Oct 24"
    )
    weekdays = [day for day in horizon if day.weekday() < 5]
    skipped = [*horizon[7:12], horizon[16]]
    assert span_label(skipped, weekdays) == "Oct 12 to Oct 16, Wed, Oct 21"
    sundays = [horizon[6], horizon[13], horizon[20]]
    assert exception_name(sundays, horizon) == "Sundays, Oct 11 to Oct 25"
    assert exception_name(horizon[5:7], horizon) == "Sat, Oct 10, Sun, Oct 11"


def test_trip_starting_before_local_midnight_moves_to_previous_day():
    feed = read_feed(
        make_zip(
            {
                "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\n"
                "a,Test,https://example.org,America/New_York\n",
                "routes.txt": "route_id,route_short_name,route_type\nR,1,2\n",
                "stops.txt": "stop_id,stop_name,stop_lat,stop_lon,stop_timezone\n"
                "S,S,34,-118,America/Los_Angeles\nT,T,34.1,-118,America/Los_Angeles\n",
                "calendar_dates.txt": "service_id,date,exception_type\n"
                "SAT,20261010,1\n",
                "trips.txt": "route_id,service_id,trip_id\nR,SAT,late\n",
                "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,"
                "stop_sequence\nlate,00:30:00,00:30:00,S,1\n"
                "late,01:30:00,01:30:00,T,2\n",
            }
        )
    )
    assert start_day_offset(feed, "late", MONDAY) == -1
    (friday,) = day_types(feed, feed.trips, MONDAY, 14)
    assert friday.dates == (date(2026, 10, 9),)


def test_start_day_offset_can_span_two_days():
    # Kiritimati (UTC+14) to Pago Pago (UTC-11) is 25 hours back.
    feed = read_feed(
        make_zip(
            {
                "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\n"
                "a,Test,https://example.org,Pacific/Kiritimati\n",
                "routes.txt": "route_id,route_short_name,route_type\nR,1,2\n",
                "stops.txt": "stop_id,stop_name,stop_lat,stop_lon,stop_timezone\n"
                "S,S,-14,-170,Pacific/Pago_Pago\n",
                "calendar_dates.txt": "service_id,date,exception_type\n"
                "SAT,20261010,1\n",
                "trips.txt": "route_id,service_id,trip_id\nR,SAT,late\n",
                "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,"
                "stop_sequence\nlate,00:30:00,00:30:00,S,1\n",
            }
        )
    )
    assert start_day_offset(feed, "late", MONDAY) == -2
    assert max_day_shift(feed, MONDAY) == 2


def test_max_day_shift():
    assert max_day_shift(read_feed(fixture_zip("branching")), MONDAY) == 0
    # A stop an hour behind the route's zone.
    assert max_day_shift(read_feed(fixture_zip("overnight")), MONDAY) == 1


def test_route_windows():
    feed = read_feed(weekday_feed())
    services = Services(feed)
    [horizon] = route_windows(services, ["WK"], MONDAY, 28)
    assert horizon.start == MONDAY and horizon.state == ""
    _, expired = route_windows(services, ["WK_A"], date(2027, 1, 4), 28)
    assert (expired.state, expired.state_date) == ("expired", date(2026, 10, 11))
    assert expired.start == date(2026, 9, 14)
    _, upcoming = route_windows(services, ["WK_B"], MONDAY, 28)
    assert (upcoming.state, upcoming.start) == ("upcoming", date(2026, 10, 12))
    assert route_windows(services, [], MONDAY, 28) == [horizon]


def test_key_services_join_trip_ids_of_one_trip():
    feed = read_feed(weekday_feed())
    trips = [t for t in feed.trips if feed.trips[t].route_id == "R"]
    services = key_services(feed, trips, ["w1", "w2", "old"])
    assert services == {"w1": ("WK_A", "WK_B"), "w2": ("WK",), "old": ("WK_A",)}


def test_service_rule():
    feed = read_feed(weekday_feed())
    assert service_rule(feed, "WK_A") == [20454, 20737, "1111100", [], []]

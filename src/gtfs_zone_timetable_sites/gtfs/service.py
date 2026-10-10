"""Service calendars to day types (Weekday / Saturday / Sunday / exceptions)."""

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from gtfs_zone_timetable_sites.gtfs.reader import Feed
from gtfs_zone_timetable_sites.i18n import (
    WEEKDAYS,
    Locale,
    capitalize,
    join_and,
    short_date,
    t,
    weekday_date,
)

DAY_SECONDS = 24 * 3600

# Date groups whose trip sets share at least this share of their trips (Jaccard)
# merge into one day type, with notes on the trips that skip some of its dates.
MERGE_SIMILARITY = 0.8


@dataclass(frozen=True, slots=True)
class DayType:
    """A set of trips that runs on (nearly) the same dates within the horizon.

    Regular day types are named by the weekdays they usually run on; `extra`
    lists dates they also run on and `missing` the usual weekdays they skip.
    Exceptions (regular=False) run only on their listed dates. `partial`
    gives the dates of trips that do not run on every one of `dates`.
    """

    name: str
    regular: bool
    weekdays: frozenset[int]
    dates: tuple[date, ...]
    trip_ids: frozenset[str]
    extra: tuple[date, ...] = ()
    missing: tuple[date, ...] = ()
    partial: tuple[tuple[str, tuple[date, ...]], ...] = ()

    @property
    def start(self) -> date:
        return self.dates[0]

    @property
    def end(self) -> date:
        return self.dates[-1]

    def trip_dates(self, trip_id: str) -> tuple[date, ...]:
        return dict(self.partial).get(trip_id, self.dates)


def active_services(feed: Feed, day: date) -> set[str]:
    services = {
        service_id
        for service_id, calendar in feed.calendars.items()
        if calendar.start <= day <= calendar.end and calendar.weekdays[day.weekday()]
    }
    for service_id, exceptions in feed.calendar_dates.items():
        added = exceptions.get(day)
        if added is True:
            services.add(service_id)
        elif added is False:
            services.discard(service_id)
    return services


def timezone_shift(stop_zone: str, route_zone: str, day: date) -> int:
    """Seconds to add to route-zone times to read them in the stop's zone."""
    noon = datetime.combine(day, time(12), ZoneInfo(route_zone))
    stop_offset = noon.astimezone(ZoneInfo(stop_zone)).utcoffset() or timedelta()
    route_offset = noon.utcoffset() or timedelta()
    return int((stop_offset - route_offset).total_seconds())


def start_day_offset(feed: Feed, trip_id: str, day: date) -> int:
    """Days (<= 0) from a trip's service date back to the local date of its
    first time, read in its first stop's zone (a 00:30 Eastern departure from
    a Pacific stop leaves at 21:30 the evening before: -1)."""
    times = feed.stop_times.get(trip_id, ())
    first = next((st for st in times if st.time is not None), None)
    if first is None or first.time is None:
        return 0
    route_zone = feed.timezone(feed.routes[feed.trips[trip_id].route_id])
    zone = feed.stop_timezone(first.stop_id)
    if not zone or zone == route_zone:
        return 0
    local = first.time + timezone_shift(zone, route_zone, day)
    return min(0, local // DAY_SECONDS)


def service_bounds(
    feed: Feed, service_ids: Iterable[str] | None = None
) -> tuple[list[date], list[date]]:
    """(calendar starts, calendar ends), each plus the added dates, of the
    given services or of all of them."""
    wanted = None if service_ids is None else set(service_ids)
    calendars = [
        c for sid, c in feed.calendars.items() if wanted is None or sid in wanted
    ]
    added = [
        day
        for sid, exceptions in feed.calendar_dates.items()
        if wanted is None or sid in wanted
        for day, on in exceptions.items()
        if on
    ]
    return [c.start for c in calendars] + added, [c.end for c in calendars] + added


def first_service_date(
    feed: Feed, service_ids: Iterable[str] | None = None
) -> date | None:
    return min(service_bounds(feed, service_ids)[0], default=None)


def last_service_date(
    feed: Feed, service_ids: Iterable[str] | None = None
) -> date | None:
    return max(service_bounds(feed, service_ids)[1], default=None)


def horizon_start(feed: Feed, today: date) -> date:
    """Today, or the feed's first service date when that is later."""
    first = first_service_date(feed)
    return max(today, first) if first is not None else today


def weekday_name(
    weekdays: Iterable[int], plural: bool = False, locale: Locale = "en"
) -> str:
    """'Weekday', 'Monday to Thursday', 'Tuesday and Friday'; with `plural`,
    'Weekdays', 'Tuesdays and Fridays'."""
    days = sorted(set(weekdays))
    suffix = "_other" if plural else ""
    special = {
        (0, 1, 2, 3, 4): f"days.weekday{suffix}",
        (5, 6): f"days.weekend{suffix}",
        (0, 1, 2, 3, 4, 5, 6): "days.daily",
    }
    if tuple(days) in special:
        return t(locale, special[tuple(days)])
    names = [WEEKDAYS[locale][day] for day in days]
    if len(days) >= 3 and days == list(range(days[0], days[-1] + 1)):
        return capitalize(t(locale, "days.range", first=names[0], last=names[-1]))
    if plural:
        names = [t(locale, "days.plural", day=name) for name in names]
    return capitalize(join_and(names, locale))


def dates_label(dates: Iterable[date], locale: Locale = "en") -> str:
    return ", ".join(weekday_date(day, locale) for day in dates)


def date_runs(dates: Iterable[date], within: Sequence[date]) -> list[list[date]]:
    """`dates` split into runs with no date of `within` missing in between."""
    wanted = set(dates)
    runs: list[list[date]] = []
    current: list[date] = []
    for day in sorted(within):
        if day in wanted:
            current.append(day)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    return runs


def span_label(
    dates: Iterable[date], within: Sequence[date], locale: Locale = "en"
) -> str:
    """Dates as ranges over `within`, e.g. 'Oct 12 to Oct 23, Sat Oct 31';
    runs of two are listed."""
    return ", ".join(
        dates_label(run, locale)
        if len(run) <= 2
        else t(
            locale,
            "range.both",
            start=short_date(run[0], locale),
            end=short_date(run[-1], locale),
        )
        for run in date_runs(dates, within)
    )


def exception_name(
    dates: Sequence[date], horizon: Sequence[date], locale: Locale = "en"
) -> str:
    """'Weekday, Oct 12 to Oct 16', 'Sundays, Oct 4 to Oct 18', or a list of
    dates when no run of them is longer than two."""
    weekdays = {day.weekday() for day in dates}
    within = [day for day in horizon if day.weekday() in weekdays]
    if all(len(run) <= 2 for run in date_runs(dates, within)):
        return capitalize(dates_label(dates, locale))
    name = weekday_name(weekdays, plural=len(weekdays) == 1, locale=locale)
    return f"{name}, {span_label(dates, within, locale)}"


def missing_label(day: DayType, locale: Locale = "en") -> str:
    """The regular weekdays a day type skips, as ranges where they run on."""
    within = sorted(
        {d for d in day.dates if d.weekday() in day.weekdays} | set(day.missing)
    )
    return span_label(day.missing, within, locale)


def trip_note(day: DayType, trip_id: str, locale: Locale = "en") -> str:
    """How a trip's dates differ from its day type's, e.g. 'Fridays only',
    'From Oct 8', 'Not Oct 12 to Oct 16'; empty when it runs on all of them."""
    runs = set(day.trip_dates(trip_id))
    if len(runs) == len(day.dates):
        return ""
    weekdays = {d.weekday() for d in runs}
    if runs == {d for d in day.dates if d.weekday() in weekdays}:
        days = weekday_name(weekdays, plural=True, locale=locale)
        return t(locale, "note.only_days", days=days)
    spans = date_runs(runs, day.dates)
    if len(spans) == 1 and spans[0][0] == day.start:
        return t(locale, "note.until", date=short_date(spans[0][-1], locale))
    if len(spans) == 1 and spans[0][-1] == day.end:
        return t(locale, "note.from", date=short_date(spans[0][0], locale))
    skipped = [d for d in day.dates if d not in runs]
    if len(skipped) <= len(runs):
        return t(locale, "note.not", dates=span_label(skipped, day.dates, locale))
    return t(locale, "note.only", dates=span_label(runs, day.dates, locale))


type TripKey = tuple[object, ...]


def trip_key(feed: Feed, trip_id: str) -> TripKey:
    """What a rider sees of a trip: two trip_ids with the same key are the
    same trip, as when a feed issues a new trip_id per date range."""
    trip = feed.trips[trip_id]
    return (
        trip.route_id,
        trip.direction_id,
        trip.short_name,
        trip.headsign,
        tuple(
            (st.stop_id, st.arrival, st.departure, st.pickup_type, st.drop_off_type)
            for st in feed.stop_times.get(trip_id, ())
        ),
        feed.frequencies.get(trip_id, ()),
    )


def trip_identity(key: TripKey) -> tuple[object, ...]:
    """Route, direction, number, headsign and first stop and time of a trip
    key: retimed later stops keep the identity, for comparing trip sets."""
    route_id, direction_id, short_name, headsign, times, _ = key
    first = times[0][:3] if times else ()
    return (route_id, direction_id, short_name, headsign, first)


def day_order(day: DayType) -> tuple[bool, int, date]:
    """Regular day types in weekday order, then exceptions by date."""
    return (not day.regular, min(day.weekdays) if day.regular else 0, day.start)


def similarity(a: frozenset[TripKey], b: frozenset[TripKey]) -> float:
    """Jaccard similarity of two trip sets by `trip_identity`."""
    ids_a = {trip_identity(key) for key in a}
    ids_b = {trip_identity(key) for key in b}
    union = len(ids_a | ids_b)
    return len(ids_a & ids_b) / union if union else 1.0


def day_types(
    feed: Feed,
    trip_ids: Iterable[str],
    start: date,
    days: int,
    locale: Locale = "en",
) -> tuple[DayType, ...]:
    """Group the horizon's dates by the set of trips running on each.

    Trips are compared by `trip_key`, and each day type keeps one trip_id per
    key. Dates with near-identical sets are clustered (see MERGE_SIMILARITY).
    The most common cluster on each weekday is that weekday's regular
    schedule (ties go to the earliest). Clusters that are regular on no
    weekday become exceptions named by their dates.
    """
    # Trips by service and by the day offset from service to local date.
    trips_by_service: dict[tuple[str, int], set[str]] = defaultdict(set)
    keys: dict[str, TripKey] = {}
    for trip_id in trip_ids:
        shift = start_day_offset(feed, trip_id, start)
        trips_by_service[feed.trips[trip_id].service_id, shift].add(trip_id)
        keys[trip_id] = trip_key(feed, trip_id)
    shifts = sorted({shift for _, shift in trips_by_service})

    signatures: dict[date, frozenset[TripKey]] = {}
    chosen: dict[TripKey, str] = {}
    for offset in range(days):
        day = start + timedelta(days=offset)
        running = [
            trip_id
            for shift in shifts
            for service_id in active_services(feed, day - timedelta(days=shift))
            for trip_id in trips_by_service.get((service_id, shift), ())
        ]
        for trip_id in sorted(running):
            chosen.setdefault(keys[trip_id], trip_id)
        signatures[day] = frozenset(keys[trip_id] for trip_id in running)

    def representatives(signature: Iterable[TripKey]) -> frozenset[str]:
        return frozenset(chosen[key] for key in signature)

    groups: dict[frozenset[TripKey], list[date]] = defaultdict(list)
    for day, signature in signatures.items():
        if signature:
            groups[signature].append(day)

    # Most dates first, so each cluster is seeded by its commonest trip set.
    clusters: list[list[frozenset[TripKey]]] = []
    for signature in sorted(groups, key=lambda s: (-len(groups[s]), groups[s][0])):
        for cluster in clusters:
            if similarity(cluster[0], signature) >= MERGE_SIMILARITY:
                cluster.append(signature)
                break
        else:
            clusters.append([signature])
    cluster_of = {
        signature: index
        for index, cluster in enumerate(clusters)
        for signature in cluster
    }

    by_weekday: dict[int, Counter[int]] = defaultdict(Counter)
    for day, signature in signatures.items():
        by_weekday[day.weekday()][cluster_of.get(signature, -1)] += 1
    regular: dict[int, set[int]] = defaultdict(set)
    for weekday, counts in sorted(by_weekday.items()):
        index = counts.most_common(1)[0][0]
        if index >= 0:
            regular[index].add(weekday)

    horizon = list(signatures)
    result = []
    for index, cluster in enumerate(clusters):
        dates = sorted(day for signature in cluster for day in groups[signature])
        key_dates: dict[TripKey, list[date]] = defaultdict(list)
        for day in dates:
            for key in signatures[day]:
                key_dates[key].append(day)
        partial = tuple(
            sorted(
                (chosen[key], tuple(runs))
                for key, runs in key_dates.items()
                if len(runs) < len(dates)
            )
        )
        common = {
            "dates": tuple(dates),
            "trip_ids": representatives(key_dates),
            "partial": partial,
        }
        weekdays = regular.get(index)
        if weekdays:
            member = set(dates)
            result.append(
                DayType(
                    name=weekday_name(weekdays, locale=locale),
                    regular=True,
                    weekdays=frozenset(weekdays),
                    extra=tuple(day for day in dates if day.weekday() not in weekdays),
                    missing=tuple(
                        day
                        for day in horizon
                        if day.weekday() in weekdays and day not in member
                    ),
                    **common,
                )
            )
        else:
            result.append(
                DayType(
                    name=exception_name(dates, horizon, locale),
                    regular=False,
                    weekdays=frozenset(day.weekday() for day in dates),
                    **common,
                )
            )
    result.sort(key=day_order)
    return tuple(result)

"""GTFS zip to typed rows, streaming each file and keeping only needed columns."""

import csv
import io
import logging
import zipfile
from collections import defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

REQUIRED_FILES = ("agency", "routes", "trips", "stop_times", "stops")
log = logging.getLogger(__name__)

HEX_DIGITS = frozenset("0123456789ABCDEF")


class MissingFiles(ValueError):
    """A GTFS zip without one of the files a timetable needs."""


@dataclass(frozen=True, slots=True)
class Agency:
    agency_id: str
    name: str
    url: str
    timezone: str
    phone: str
    lang: str = ""


@dataclass(frozen=True, slots=True)
class Route:
    route_id: str
    agency_id: str
    short_name: str
    long_name: str
    desc: str
    route_type: int
    color: str | None
    text_color: str | None
    sort_order: int | None
    url: str
    network_id: str = ""

    @property
    def name(self) -> str:
        return self.short_name or self.long_name or self.route_id


@dataclass(frozen=True, slots=True)
class Stop:
    stop_id: str
    name: str
    lat: float | None
    lon: float | None
    code: str
    timezone: str
    parent_station: str
    wheelchair_boarding: int
    platform: str = ""
    zone_id: str = ""


@dataclass(frozen=True, slots=True)
class Trip:
    trip_id: str
    route_id: str
    service_id: str
    headsign: str
    short_name: str
    direction_id: int | None
    shape_id: str
    bikes_allowed: int
    wheelchair_accessible: int
    block_id: str = ""


@dataclass(frozen=True, slots=True)
class StopTime:
    stop_id: str
    sequence: int
    arrival: int | None
    departure: int | None
    timepoint: int | None
    pickup_type: int
    drop_off_type: int

    @property
    def time(self) -> int | None:
        return self.departure if self.departure is not None else self.arrival


@dataclass(frozen=True, slots=True)
class Calendar:
    service_id: str
    weekdays: tuple[bool, ...]
    start: date
    end: date


@dataclass(frozen=True, slots=True)
class Frequency:
    start: int
    end: int
    headway_secs: int
    exact_times: bool


@dataclass(frozen=True, slots=True)
class FeedInfo:
    publisher_name: str
    publisher_url: str
    version: str
    start: date | None
    end: date | None
    lang: str = ""


@dataclass(frozen=True, slots=True)
class Translation:
    """One translations.txt row. A row matches its record by `record_id` (and
    `record_sub_id`) or, when those are empty, by `field_value`."""

    table_name: str
    field_name: str
    language: str
    translation: str
    record_id: str
    record_sub_id: str
    field_value: str


@dataclass(frozen=True, slots=True)
class FareAttribute:
    fare_id: str
    agency_id: str
    price: Decimal
    currency: str
    payment_method: int
    # None is unlimited transfers.
    transfers: int | None
    transfer_duration: int | None


@dataclass(frozen=True, slots=True)
class FareRule:
    fare_id: str
    route_id: str
    origin_id: str
    destination_id: str
    contains_id: str


@dataclass(frozen=True, slots=True)
class FareMedia:
    fare_media_id: str
    name: str
    media_type: int


@dataclass(frozen=True, slots=True)
class FareProduct:
    fare_product_id: str
    name: str
    fare_media_id: str
    amount: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class FareLegRule:
    leg_group_id: str
    network_id: str
    from_area_id: str
    to_area_id: str
    from_timeframe_group_id: str
    to_timeframe_group_id: str
    fare_product_id: str
    rule_priority: int | None
    # The rule prices only a leg reached by a transfer.
    transfer_only: bool


@dataclass(frozen=True, slots=True)
class FareTransferRule:
    from_leg_group_id: str
    to_leg_group_id: str
    transfer_count: int | None
    duration_limit: int | None
    duration_limit_type: int | None
    fare_transfer_type: int
    fare_product_id: str


@dataclass(frozen=True, slots=True)
class Timeframe:
    start: int | None
    end: int | None
    service_id: str


@dataclass(frozen=True, slots=True)
class Fares:
    """Fares v1 and v2 tables; each is empty when its file is missing or has
    no rows."""

    # v1
    attributes: dict[str, FareAttribute]
    rules: tuple[FareRule, ...]
    # v2
    areas: dict[str, str]  # area_id -> area_name
    stop_areas: dict[str, tuple[str, ...]]  # area_id -> stop_ids
    networks: dict[str, str]  # network_id -> network_name
    route_networks: dict[str, str]  # route_id -> network_id
    media: dict[str, FareMedia]
    products: dict[str, tuple[FareProduct, ...]]  # one row per media
    leg_rules: tuple[FareLegRule, ...]
    transfer_rules: tuple[FareTransferRule, ...]
    timeframes: dict[str, tuple[Timeframe, ...]]


@dataclass(frozen=True, slots=True)
class Feed:
    agencies: dict[str, Agency]
    routes: dict[str, Route]
    stops: dict[str, Stop]
    trips: dict[str, Trip]
    stop_times: dict[str, tuple[StopTime, ...]]
    calendars: dict[str, Calendar]
    calendar_dates: dict[str, dict[date, bool]]
    shapes: dict[str, tuple[tuple[float, float], ...]]
    frequencies: dict[str, tuple[Frequency, ...]]
    feed_info: FeedInfo | None
    fares: Fares
    translations: tuple[Translation, ...] = ()

    def timezone(self, route: Route) -> str:
        agency = self.agencies.get(route.agency_id)
        if agency is None:
            agency = next(iter(self.agencies.values()))
        return agency.timezone

    def station(self, stop_id: str) -> str:
        """The stop's parent station when the feed has it, else the stop."""
        stop = self.stops.get(stop_id)
        if stop is not None and stop.parent_station in self.stops:
            return stop.parent_station
        return stop_id

    def stop_timezone(self, stop_id: str) -> str:
        """The stop's own timezone, else its parent station's, else empty."""
        stop = self.stops.get(stop_id)
        if stop is None:
            return ""
        if stop.timezone or not stop.parent_station:
            return stop.timezone
        return self.stop_timezone(stop.parent_station)

    def wheelchair_boarding(self, stop_id: str) -> int:
        """The stop's wheelchair_boarding, its parent station's when it has 0."""
        stop = self.stops.get(stop_id)
        if stop is None:
            return 0
        if stop.wheelchair_boarding or stop.parent_station not in self.stops:
            return stop.wheelchair_boarding
        return self.stops[stop.parent_station].wheelchair_boarding


def parse_time(text: str) -> int | None:
    """HH:MM:SS (hours may pass 24) to seconds into the service day."""
    text = text.strip()
    if not text:
        return None
    hours, minutes, seconds = text.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds)


def parse_date(text: str) -> date | None:
    text = text.strip()
    if not text:
        return None
    return date(int(text[:4]), int(text[4:6]), int(text[6:8]))


def parse_int(text: str) -> int | None:
    text = text.strip()
    return int(text) if text else None


def parse_float(text: str) -> float | None:
    text = text.strip()
    return float(text) if text else None


def parse_color(text: str) -> str | None:
    text = text.strip().lstrip("#").upper()
    return text if len(text) == 6 and all(c in HEX_DIGITS for c in text) else None


class Archive:
    """GTFS members by file name, tolerating one wrapping directory."""

    def __init__(self, archive: zipfile.ZipFile) -> None:
        self.archive = archive
        self.members: dict[str, str] = {}
        for name in sorted(archive.namelist(), key=lambda n: n.count("/")):
            if name.startswith("__MACOSX/") or not name.endswith(".txt"):
                continue
            self.members.setdefault(name.rsplit("/", 1)[-1][:-4], name)

    def has(self, table: str) -> bool:
        return table in self.members

    def rows(self, table: str, columns: tuple[str, ...]) -> Iterator[dict[str, str]]:
        """Rows with only `columns`; a column missing from the file reads as ''."""
        if table not in self.members:
            return
        with self.archive.open(self.members[table]) as raw:
            reader = csv.reader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
            header = [name.strip() for name in next(reader, [])]
            index = {name: i for i, name in enumerate(header)}
            picks = [(column, index.get(column)) for column in columns]
            for row in reader:
                if not row:
                    continue
                yield {
                    column: row[i] if i is not None and i < len(row) else ""
                    for column, i in picks
                }


def read_agencies(archive: Archive) -> dict[str, Agency]:
    columns = (
        "agency_id",
        "agency_name",
        "agency_url",
        "agency_timezone",
        "agency_phone",
        "agency_lang",
    )
    return {
        row["agency_id"]: Agency(
            agency_id=row["agency_id"],
            name=row["agency_name"],
            url=row["agency_url"],
            timezone=row["agency_timezone"],
            phone=row["agency_phone"],
            lang=row["agency_lang"].strip(),
        )
        for row in archive.rows("agency", columns)
    }


def read_routes(archive: Archive) -> dict[str, Route]:
    columns = (
        "route_id",
        "agency_id",
        "route_short_name",
        "route_long_name",
        "route_desc",
        "route_type",
        "route_color",
        "route_text_color",
        "route_sort_order",
        "route_url",
        "network_id",
    )
    routes = {
        row["route_id"]: Route(
            route_id=row["route_id"],
            agency_id=row["agency_id"],
            short_name=row["route_short_name"],
            long_name=row["route_long_name"],
            desc=row["route_desc"],
            route_type=int(row["route_type"]),
            color=parse_color(row["route_color"]),
            text_color=parse_color(row["route_text_color"]),
            sort_order=parse_int(row["route_sort_order"]),
            url=row["route_url"],
            network_id=row["network_id"],
        )
        for row in archive.rows("routes", columns)
    }
    # A color shared by every route tells none apart, so it counts as no color.
    colors = {route.color for route in routes.values() if route.color is not None}
    if len(routes) >= 2 and len(colors) == 1:
        log.info(
            "All %d routes share #%s; using hashed colors", len(routes), colors.pop()
        )
        routes = {
            route_id: replace(route, color=None, text_color=None)
            for route_id, route in routes.items()
        }
    return routes


def read_stops(archive: Archive) -> dict[str, Stop]:
    columns = (
        "stop_id",
        "stop_name",
        "stop_lat",
        "stop_lon",
        "stop_code",
        "stop_timezone",
        "parent_station",
        "wheelchair_boarding",
        "platform_code",
        "zone_id",
    )
    return {
        row["stop_id"]: Stop(
            stop_id=row["stop_id"],
            name=row["stop_name"],
            lat=parse_float(row["stop_lat"]),
            lon=parse_float(row["stop_lon"]),
            code=row["stop_code"],
            timezone=row["stop_timezone"],
            parent_station=row["parent_station"],
            wheelchair_boarding=parse_int(row["wheelchair_boarding"]) or 0,
            platform=row["platform_code"],
            zone_id=row["zone_id"],
        )
        for row in archive.rows("stops", columns)
    }


def read_trips(archive: Archive, route_ids: set[str]) -> dict[str, Trip]:
    columns = (
        "trip_id",
        "route_id",
        "service_id",
        "trip_headsign",
        "trip_short_name",
        "direction_id",
        "shape_id",
        "bikes_allowed",
        "wheelchair_accessible",
        "block_id",
    )
    return {
        row["trip_id"]: Trip(
            trip_id=row["trip_id"],
            route_id=row["route_id"],
            service_id=row["service_id"],
            headsign=row["trip_headsign"],
            short_name=row["trip_short_name"],
            direction_id=parse_int(row["direction_id"]),
            shape_id=row["shape_id"],
            bikes_allowed=parse_int(row["bikes_allowed"]) or 0,
            wheelchair_accessible=parse_int(row["wheelchair_accessible"]) or 0,
            block_id=row["block_id"],
        )
        for row in archive.rows("trips", columns)
        if row["route_id"] in route_ids
    }


def read_stop_times(
    archive: Archive, trip_ids: set[str]
) -> dict[str, tuple[StopTime, ...]]:
    columns = (
        "trip_id",
        "arrival_time",
        "departure_time",
        "stop_id",
        "stop_sequence",
        "timepoint",
        "pickup_type",
        "drop_off_type",
    )
    by_trip: dict[str, list[StopTime]] = defaultdict(list)
    for row in archive.rows("stop_times", columns):
        if row["trip_id"] not in trip_ids:
            continue
        by_trip[row["trip_id"]].append(
            StopTime(
                stop_id=row["stop_id"],
                sequence=int(row["stop_sequence"]),
                arrival=parse_time(row["arrival_time"]),
                departure=parse_time(row["departure_time"]),
                timepoint=parse_int(row["timepoint"]),
                pickup_type=parse_int(row["pickup_type"]) or 0,
                drop_off_type=parse_int(row["drop_off_type"]) or 0,
            )
        )
    return {
        trip_id: tuple(sorted(times, key=lambda st: st.sequence))
        for trip_id, times in by_trip.items()
    }


def read_calendars(archive: Archive, service_ids: set[str]) -> dict[str, Calendar]:
    """Calendars of the given services."""
    days = (
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    )
    calendars = {}
    for row in archive.rows(
        "calendar", ("service_id", "start_date", "end_date", *days)
    ):
        if row["service_id"] not in service_ids:
            continue
        start, end = parse_date(row["start_date"]), parse_date(row["end_date"])
        if start is None or end is None:
            continue
        calendars[row["service_id"]] = Calendar(
            service_id=row["service_id"],
            weekdays=tuple(row[day].strip() == "1" for day in days),
            start=start,
            end=end,
        )
    return calendars


def read_calendar_dates(
    archive: Archive, service_ids: set[str]
) -> dict[str, dict[date, bool]]:
    """service_id -> date -> True when added, False when removed, of the given
    services."""
    exceptions: dict[str, dict[date, bool]] = defaultdict(dict)
    for row in archive.rows("calendar_dates", ("service_id", "date", "exception_type")):
        if row["service_id"] not in service_ids:
            continue
        day = parse_date(row["date"])
        if day is not None:
            exceptions[row["service_id"]][day] = row["exception_type"].strip() == "1"
    return dict(exceptions)


def read_shapes(
    archive: Archive, shape_ids: set[str]
) -> dict[str, tuple[tuple[float, float], ...]]:
    """shape_id -> (lat, lon) points in sequence order."""
    columns = ("shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence")
    points: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
    for row in archive.rows("shapes", columns):
        if row["shape_id"] in shape_ids:
            points[row["shape_id"]].append(
                (
                    int(row["shape_pt_sequence"]),
                    float(row["shape_pt_lat"]),
                    float(row["shape_pt_lon"]),
                )
            )
    return {
        shape_id: tuple((lat, lon) for _, lat, lon in sorted(rows))
        for shape_id, rows in points.items()
    }


def read_frequencies(
    archive: Archive, trip_ids: set[str]
) -> dict[str, tuple[Frequency, ...]]:
    columns = ("trip_id", "start_time", "end_time", "headway_secs", "exact_times")
    by_trip: dict[str, list[Frequency]] = defaultdict(list)
    for row in archive.rows("frequencies", columns):
        start, end = parse_time(row["start_time"]), parse_time(row["end_time"])
        if row["trip_id"] not in trip_ids or start is None or end is None:
            continue
        by_trip[row["trip_id"]].append(
            Frequency(
                start=start,
                end=end,
                headway_secs=int(row["headway_secs"]),
                exact_times=row["exact_times"].strip() == "1",
            )
        )
    return {
        trip_id: tuple(sorted(rows, key=lambda f: f.start))
        for trip_id, rows in by_trip.items()
    }


def read_feed_info(archive: Archive) -> FeedInfo | None:
    columns = (
        "feed_publisher_name",
        "feed_publisher_url",
        "feed_version",
        "feed_start_date",
        "feed_end_date",
        "feed_lang",
    )
    for row in archive.rows("feed_info", columns):
        return FeedInfo(
            publisher_name=row["feed_publisher_name"],
            publisher_url=row["feed_publisher_url"],
            version=row["feed_version"],
            start=parse_date(row["feed_start_date"]),
            end=parse_date(row["feed_end_date"]),
            lang=row["feed_lang"].strip(),
        )
    return None


def read_translations(archive: Archive) -> tuple[Translation, ...]:
    columns = (
        "table_name",
        "field_name",
        "language",
        "translation",
        "record_id",
        "record_sub_id",
        "field_value",
    )
    return tuple(
        Translation(
            table_name=row["table_name"],
            field_name=row["field_name"],
            language=row["language"].strip(),
            translation=row["translation"],
            record_id=row["record_id"],
            record_sub_id=row["record_sub_id"],
            field_value=row["field_value"],
        )
        for row in archive.rows("translations", columns)
    )


def read_fares(archive: Archive) -> Fares:
    attributes = {
        row["fare_id"]: FareAttribute(
            fare_id=row["fare_id"],
            agency_id=row["agency_id"],
            price=Decimal(row["price"].strip()),
            currency=row["currency_type"].strip(),
            payment_method=int(row["payment_method"]),
            transfers=parse_int(row["transfers"]),
            transfer_duration=parse_int(row["transfer_duration"]),
        )
        for row in archive.rows(
            "fare_attributes",
            (
                "fare_id",
                "agency_id",
                "price",
                "currency_type",
                "payment_method",
                "transfers",
                "transfer_duration",
            ),
        )
    }
    rules = tuple(
        FareRule(
            fare_id=row["fare_id"],
            route_id=row["route_id"],
            origin_id=row["origin_id"],
            destination_id=row["destination_id"],
            contains_id=row["contains_id"],
        )
        for row in archive.rows(
            "fare_rules",
            ("fare_id", "route_id", "origin_id", "destination_id", "contains_id"),
        )
    )
    stop_areas: dict[str, list[str]] = defaultdict(list)
    for row in archive.rows("stop_areas", ("area_id", "stop_id")):
        stop_areas[row["area_id"]].append(row["stop_id"])
    products: dict[str, list[FareProduct]] = defaultdict(list)
    for row in archive.rows(
        "fare_products",
        ("fare_product_id", "fare_product_name", "fare_media_id", "amount", "currency"),
    ):
        products[row["fare_product_id"]].append(
            FareProduct(
                fare_product_id=row["fare_product_id"],
                name=row["fare_product_name"],
                fare_media_id=row["fare_media_id"],
                amount=Decimal(row["amount"].strip()),
                currency=row["currency"].strip(),
            )
        )
    timeframes: dict[str, list[Timeframe]] = defaultdict(list)
    for row in archive.rows(
        "timeframes", ("timeframe_group_id", "start_time", "end_time", "service_id")
    ):
        timeframes[row["timeframe_group_id"]].append(
            Timeframe(
                start=parse_time(row["start_time"]),
                end=parse_time(row["end_time"]),
                service_id=row["service_id"],
            )
        )
    return Fares(
        attributes=attributes,
        rules=rules,
        areas={
            row["area_id"]: row["area_name"]
            for row in archive.rows("areas", ("area_id", "area_name"))
        },
        stop_areas={area: tuple(stops) for area, stops in stop_areas.items()},
        networks={
            row["network_id"]: row["network_name"]
            for row in archive.rows("networks", ("network_id", "network_name"))
        },
        route_networks={
            row["route_id"]: row["network_id"]
            for row in archive.rows("route_networks", ("network_id", "route_id"))
        },
        media={
            row["fare_media_id"]: FareMedia(
                fare_media_id=row["fare_media_id"],
                name=row["fare_media_name"],
                media_type=int(row["fare_media_type"]),
            )
            for row in archive.rows(
                "fare_media", ("fare_media_id", "fare_media_name", "fare_media_type")
            )
        },
        products={key: tuple(rows) for key, rows in products.items()},
        leg_rules=tuple(
            FareLegRule(
                leg_group_id=row["leg_group_id"],
                network_id=row["network_id"],
                from_area_id=row["from_area_id"],
                to_area_id=row["to_area_id"],
                from_timeframe_group_id=row["from_timeframe_group_id"],
                to_timeframe_group_id=row["to_timeframe_group_id"],
                fare_product_id=row["fare_product_id"],
                rule_priority=parse_int(row["rule_priority"]),
                transfer_only=row["transfer_only"] == "1",
            )
            for row in archive.rows(
                "fare_leg_rules",
                (
                    "leg_group_id",
                    "network_id",
                    "from_area_id",
                    "to_area_id",
                    "from_timeframe_group_id",
                    "to_timeframe_group_id",
                    "fare_product_id",
                    "rule_priority",
                    "transfer_only",
                ),
            )
        ),
        transfer_rules=tuple(
            FareTransferRule(
                from_leg_group_id=row["from_leg_group_id"],
                to_leg_group_id=row["to_leg_group_id"],
                transfer_count=parse_int(row["transfer_count"]),
                duration_limit=parse_int(row["duration_limit"]),
                duration_limit_type=parse_int(row["duration_limit_type"]),
                fare_transfer_type=int(row["fare_transfer_type"]),
                fare_product_id=row["fare_product_id"],
            )
            for row in archive.rows(
                "fare_transfer_rules",
                (
                    "from_leg_group_id",
                    "to_leg_group_id",
                    "transfer_count",
                    "duration_limit",
                    "duration_limit_type",
                    "fare_transfer_type",
                    "fare_product_id",
                ),
            )
        ),
        timeframes={key: tuple(rows) for key, rows in timeframes.items()},
    )


def read_feed(
    zip_bytes: bytes, keep_route: Callable[[Route], bool] | None = None
) -> Feed:
    """Parse a GTFS zip, keeping only routes accepted by `keep_route`."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as raw:
        archive = Archive(raw)
        missing = [table for table in REQUIRED_FILES if not archive.has(table)]
        if not (archive.has("calendar") or archive.has("calendar_dates")):
            missing.append("calendar or calendar_dates")
        if missing:
            raise MissingFiles(f"GTFS zip is missing {', '.join(missing)}")

        routes = read_routes(archive)
        if keep_route is not None:
            routes = {key: route for key, route in routes.items() if keep_route(route)}
        trips = read_trips(archive, set(routes))
        stop_times = read_stop_times(archive, set(trips))
        service_ids = {trip.service_id for trip in trips.values()}
        return Feed(
            agencies=read_agencies(archive),
            routes=routes,
            stops=read_stops(archive),
            trips=trips,
            stop_times=stop_times,
            calendars=read_calendars(archive, service_ids),
            calendar_dates=read_calendar_dates(archive, service_ids),
            shapes=read_shapes(archive, {t.shape_id for t in trips.values()} - {""}),
            frequencies=read_frequencies(archive, set(trips)),
            feed_info=read_feed_info(archive),
            fares=read_fares(archive),
            translations=read_translations(archive),
        )

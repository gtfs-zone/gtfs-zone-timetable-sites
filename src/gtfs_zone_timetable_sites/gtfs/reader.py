"""GTFS zip to typed rows, streaming each file and keeping only needed columns."""

import csv
import io
import json
import logging
from array import array
from collections import defaultdict
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal

from gtfs_zone_feed_fetch.reader import (
    Agency,
    Archive,
    Calendar,
    FeedInfo,
    MissingFiles,
    Stop,
    ZipSource,
    open_zip,
    parse_int,
    read_agencies,
    read_calendar_dates,
    read_calendars,
    read_feed_info,
    read_stops,
)

type Point = tuple[float, float]
# Which of a path's points to keep.
type PathFilter = Callable[[Sequence[Point]], Sequence[bool]]

log = logging.getLogger(__name__)

HEX_DIGITS = frozenset("0123456789ABCDEF")


class FaresError(ValueError):
    """Fares tables that cannot be read or linked, with the file and field."""


class FlexError(ValueError):
    """GTFS-Flex files that cannot be read, with the file and field."""


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
class FlexStop:
    """A stop_time's GTFS-Flex fields, as given."""

    location_id: str = ""
    location_group_id: str = ""
    start_window: int | None = None
    end_window: int | None = None
    pickup_booking_rule_id: str = ""
    drop_off_booking_rule_id: str = ""


@dataclass(frozen=True, slots=True)
class StopTime:
    stop_id: str
    sequence: int
    arrival: int | None
    departure: int | None
    timepoint: int | None
    pickup_type: int
    drop_off_type: int
    # None when the row has no GTFS-Flex field.
    flex: FlexStop | None = None

    @property
    def time(self) -> int | None:
        return self.departure if self.departure is not None else self.arrival

    @property
    def place_id(self) -> str:
        """The stop_id, else the location_id or location_group_id; GTFS keeps
        the three unique among each other."""
        if self.stop_id or self.flex is None:
            return self.stop_id
        return self.flex.location_id or self.flex.location_group_id


@dataclass(frozen=True, slots=True)
class Frequency:
    start: int
    end: int
    headway_secs: int
    exact_times: bool


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
    no rows. When a fares file cannot be read, every table is empty and
    `error` says why."""

    # v1
    attributes: dict[str, FareAttribute] = field(default_factory=dict)
    rules: tuple[FareRule, ...] = ()
    # v2
    areas: dict[str, str] = field(default_factory=dict)  # area_id -> area_name
    # area_id -> stop_ids
    stop_areas: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # network_id -> network_name
    networks: dict[str, str] = field(default_factory=dict)
    # route_id -> network_id
    route_networks: dict[str, str] = field(default_factory=dict)
    media: dict[str, FareMedia] = field(default_factory=dict)
    # one row per media
    products: dict[str, tuple[FareProduct, ...]] = field(default_factory=dict)
    leg_rules: tuple[FareLegRule, ...] = ()
    transfer_rules: tuple[FareTransferRule, ...] = ()
    timeframes: dict[str, tuple[Timeframe, ...]] = field(default_factory=dict)
    error: str = ""


type Ring = tuple[Point, ...]
# Rings of one polygon, the outer ring first; points as (lon, lat).
type Polygon = tuple[Ring, ...]


@dataclass(frozen=True, slots=True)
class Location:
    """A locations.geojson feature."""

    location_id: str
    name: str
    desc: str
    polygons: tuple[Polygon, ...]


@dataclass(frozen=True, slots=True)
class LocationGroup:
    location_group_id: str
    name: str
    stop_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BookingRule:
    booking_type: int | None
    prior_notice_duration_min: int | None
    prior_notice_duration_max: int | None
    prior_notice_last_day: int | None
    prior_notice_last_time: int | None
    prior_notice_start_day: int | None
    prior_notice_start_time: int | None
    prior_notice_service_id: str
    message: str
    pickup_message: str
    drop_off_message: str
    phone_number: str
    info_url: str
    booking_url: str


@dataclass(frozen=True, slots=True)
class Flex:
    """GTFS-Flex locations, location groups and booking rules. A file that
    cannot be read leaves its table empty and its reason in `errors`.
    `misnamed` lists (name in the feed, name in GTFS) for Flex files and
    columns the feed names otherwise, which are not read."""

    locations: dict[str, Location] = field(default_factory=dict)
    groups: dict[str, LocationGroup] = field(default_factory=dict)
    booking_rules: dict[str, BookingRule] = field(default_factory=dict)
    misnamed: tuple[tuple[str, str], ...] = ()
    errors: tuple[str, ...] = ()


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
    flex: Flex = field(default_factory=Flex)
    # Each route's service_ids and the trip count, before trips are dropped
    # by date.
    route_services: dict[str, frozenset[str]] = field(default_factory=dict)
    trip_count: int = 0

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

    def flex_place(self, place_id: str) -> bool:
        """Whether the id is a GTFS-Flex location or location group."""
        return place_id in self.flex.locations or place_id in self.flex.groups

    def place_name(self, place_id: str) -> str:
        """The name of a stop, location or location group; "" for a location
        the feed gives no name, the id when the feed has no such place."""
        if stop := self.stops.get(place_id):
            return stop.name
        if location := self.flex.locations.get(place_id):
            return location.name
        if group := self.flex.groups.get(place_id):
            return group.name
        return place_id

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


def parse_color(text: str) -> str | None:
    text = text.strip().lstrip("#").upper()
    return text if len(text) == 6 and all(c in HEX_DIGITS for c in text) else None


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


def read_route_services(
    archive: Archive, route_ids: set[str]
) -> tuple[dict[str, frozenset[str]], int]:
    """Each route's service_ids and the number of trips of the routes."""
    services: dict[str, set[str]] = defaultdict(set)
    count = 0
    for row in archive.rows("trips", ("route_id", "service_id")):
        if row["route_id"] in route_ids:
            services[row["route_id"]].add(row["service_id"])
            count += 1
    return {key: frozenset(ids) for key, ids in services.items()}, count


def read_trips(
    archive: Archive, route_ids: set[str], service_ids: Collection[str] | None = None
) -> dict[str, Trip]:
    """Trips of the routes, and of the services when given."""
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
    # Trips repeat the same routes, services, headsigns and shapes, so each
    # distinct string is kept once.
    strings: dict[str, str] = {}

    def shared(text: str) -> str:
        return strings.setdefault(text, text)

    return {
        row["trip_id"]: Trip(
            trip_id=row["trip_id"],
            route_id=shared(row["route_id"]),
            service_id=shared(row["service_id"]),
            headsign=shared(row["trip_headsign"]),
            short_name=shared(row["trip_short_name"]),
            direction_id=parse_int(row["direction_id"]),
            shape_id=shared(row["shape_id"]),
            bikes_allowed=parse_int(row["bikes_allowed"]) or 0,
            wheelchair_accessible=parse_int(row["wheelchair_accessible"]) or 0,
            block_id=shared(row["block_id"]),
        )
        for row in archive.rows("trips", columns)
        if row["route_id"] in route_ids
        and (service_ids is None or row["service_id"] in service_ids)
    }


FLEX_STOP_COLUMNS = (
    "location_id",
    "location_group_id",
    "start_pickup_drop_off_window",
    "end_pickup_drop_off_window",
    "pickup_booking_rule_id",
    "drop_off_booking_rule_id",
)
# Names seen in feeds for Flex files and columns, by the name in GTFS.
FLEX_MISNAMED = {
    "stop_times.txt start_pickup_dropoff_window": (
        "stop_times.txt start_pickup_drop_off_window"
    ),
    "stop_times.txt end_pickup_dropoff_window": (
        "stop_times.txt end_pickup_drop_off_window"
    ),
    "location.geojson": "locations.geojson",
}


def header(archive: Archive, table: str) -> list[str]:
    """The column names of `table`, empty when the file is missing."""
    if not archive.has(table):
        return []
    with archive.archive.open(archive.members[table]) as raw:
        reader = csv.reader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
        return [name.strip() for name in next(reader, [])]


def read_stop_times(
    archive: Archive, trip_ids: set[str]
) -> dict[str, tuple[StopTime, ...]]:
    present = set(header(archive, "stop_times"))
    flex_columns = tuple(column for column in FLEX_STOP_COLUMNS if column in present)
    columns = (
        "trip_id",
        "arrival_time",
        "departure_time",
        "stop_id",
        "stop_sequence",
        "timepoint",
        "pickup_type",
        "drop_off_type",
        *flex_columns,
    )
    # Rows repeat the same stop_ids, times and Flex fields, so each distinct
    # value is kept once.
    stop_ids: dict[str, str] = {}
    times: dict[str, int | None] = {}
    flex_stops: dict[tuple[str, ...], FlexStop | None] = {}

    def shared_time(text: str) -> int | None:
        if text not in times:
            times[text] = parse_time(text)
        return times[text]

    def flex_stop(row: dict[str, str]) -> FlexStop | None:
        values = tuple(row.get(column, "") for column in FLEX_STOP_COLUMNS)
        if values not in flex_stops:
            location, group, start, end, pickup, drop_off = values
            flex_stops[values] = (
                FlexStop(
                    location_id=location,
                    location_group_id=group,
                    start_window=shared_time(start),
                    end_window=shared_time(end),
                    pickup_booking_rule_id=pickup,
                    drop_off_booking_rule_id=drop_off,
                )
                if any(values)
                else None
            )
        return flex_stops[values]

    by_trip: dict[str, list[StopTime]] = defaultdict(list)
    for row in archive.rows("stop_times", columns):
        if row["trip_id"] not in trip_ids:
            continue
        by_trip[row["trip_id"]].append(
            StopTime(
                stop_id=stop_ids.setdefault(row["stop_id"], row["stop_id"]),
                sequence=int(row["stop_sequence"]),
                arrival=shared_time(row["arrival_time"]),
                departure=shared_time(row["departure_time"]),
                timepoint=parse_int(row["timepoint"]),
                pickup_type=parse_int(row["pickup_type"]) or 0,
                drop_off_type=parse_int(row["drop_off_type"]) or 0,
                flex=flex_stop(row) if flex_columns else None,
            )
        )
    return {
        trip_id: tuple(sorted(times, key=lambda st: st.sequence))
        for trip_id, times in by_trip.items()
    }


def read_shapes(
    archive: Archive, shape_ids: set[str], thin: PathFilter | None = None
) -> dict[str, tuple[Point, ...]]:
    """shape_id -> (lat, lon) points in sequence order, each run of a shape's
    rows passed through `thin` as it ends, and again once all are read when
    a shape came in several runs."""
    columns = ("shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence")
    shapes: dict[str, tuple[Point, ...]] = {}
    # The sequence number of each point kept, for a shape seen again later.
    sequences: dict[str, array[int]] = {}
    split: dict[str, list[tuple[int, float, float]]] = {}

    def thinned(rows: list[tuple[int, float, float]]) -> list[tuple[int, float, float]]:
        rows.sort()
        if thin is None:
            return rows
        keep = thin([(lat, lon) for _, lat, lon in rows])
        return [row for row, kept in zip(rows, keep, strict=True) if kept]

    def end_run(shape_id: str, run: list[tuple[int, float, float]]) -> None:
        rows = thinned(run)
        if shape_id in split:
            split[shape_id] += rows
        elif shape_id in shapes:
            points = shapes.pop(shape_id)
            seqs = sequences.pop(shape_id)
            split[shape_id] = [
                (seq, lat, lon) for seq, (lat, lon) in zip(seqs, points, strict=True)
            ] + rows
        else:
            shapes[shape_id] = tuple((lat, lon) for _, lat, lon in rows)
            sequences[shape_id] = array("q", (seq for seq, _, _ in rows))

    current, run = "", []
    for row in archive.rows("shapes", columns):
        shape_id = row["shape_id"]
        if shape_id not in shape_ids:
            continue
        if shape_id != current:
            if run:
                end_run(current, run)
            current, run = shape_id, []
        run.append(
            (
                int(row["shape_pt_sequence"]),
                float(row["shape_pt_lat"]),
                float(row["shape_pt_lon"]),
            )
        )
    if run:
        end_run(current, run)
    sequences.clear()
    for shape_id, rows in split.items():
        shapes[shape_id] = tuple((lat, lon) for _, lat, lon in thinned(rows))
    return shapes


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


def fare_field[T](
    table: str, row: dict[str, str], column: str, parse: Callable[[str], T]
) -> T:
    """`parse` of a fares field; a value it cannot read raises FaresError
    naming the file, field and value."""
    value = row[column]
    try:
        return parse(value)
    except (ValueError, ArithmeticError) as error:
        what = "is empty" if not value.strip() else f"{value!r} is not valid"
        raise FaresError(f"{table}.txt: {column} {what}") from error


def payment_method(text: str) -> int:
    """fare_attributes.payment_method, 0 or 1."""
    value = int(text)
    if value not in (0, 1):
        raise ValueError(value)
    return value


def parse_decimal(text: str) -> Decimal:
    return Decimal(text.strip())


def read_fares(archive: Archive) -> Fares:
    """The fares tables; a value that cannot be read raises FaresError."""
    attributes = {
        row["fare_id"]: FareAttribute(
            fare_id=row["fare_id"],
            agency_id=row["agency_id"],
            price=fare_field("fare_attributes", row, "price", parse_decimal),
            currency=row["currency_type"].strip(),
            payment_method=fare_field(
                "fare_attributes", row, "payment_method", payment_method
            ),
            transfers=fare_field("fare_attributes", row, "transfers", parse_int),
            transfer_duration=fare_field(
                "fare_attributes", row, "transfer_duration", parse_int
            ),
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
                amount=fare_field("fare_products", row, "amount", parse_decimal),
                currency=row["currency"].strip(),
            )
        )
    timeframes: dict[str, list[Timeframe]] = defaultdict(list)
    for row in archive.rows(
        "timeframes", ("timeframe_group_id", "start_time", "end_time", "service_id")
    ):
        timeframes[row["timeframe_group_id"]].append(
            Timeframe(
                start=fare_field("timeframes", row, "start_time", parse_time),
                end=fare_field("timeframes", row, "end_time", parse_time),
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
                media_type=fare_field("fare_media", row, "fare_media_type", int),
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
                rule_priority=fare_field(
                    "fare_leg_rules", row, "rule_priority", parse_int
                ),
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
                transfer_count=fare_field(
                    "fare_transfer_rules", row, "transfer_count", parse_int
                ),
                duration_limit=fare_field(
                    "fare_transfer_rules", row, "duration_limit", parse_int
                ),
                duration_limit_type=fare_field(
                    "fare_transfer_rules", row, "duration_limit_type", parse_int
                ),
                fare_transfer_type=fare_field(
                    "fare_transfer_rules", row, "fare_transfer_type", int
                ),
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


def flex_field[T](
    table: str, row: dict[str, str], column: str, parse: Callable[[str], T]
) -> T:
    """`parse` of a Flex field; a value it cannot read raises FlexError
    naming the file, field and value."""
    value = row[column]
    try:
        return parse(value)
    except ValueError as error:
        raise FlexError(f"{table}.txt: {column} {value!r} is not valid") from error


def geojson_member(archive: Archive, filename: str) -> str | None:
    """The zip member `filename` next to stops.txt, or None."""
    folder = archive.members["stops"].rpartition("/")[0]
    name = f"{folder}/{filename}" if folder else filename
    return name if name in archive.archive.namelist() else None


def read_polygons(location_id: str, geometry: object) -> tuple[Polygon, ...]:
    """A Polygon or MultiPolygon geometry's polygons."""
    if not isinstance(geometry, dict):
        raise FlexError(f"locations.geojson: {location_id!r} has no geometry")
    kind, coordinates = geometry.get("type"), geometry.get("coordinates")
    if kind not in ("Polygon", "MultiPolygon") or not isinstance(coordinates, list):
        raise FlexError(
            f"locations.geojson: {location_id!r} geometry {kind!r} is not a "
            "Polygon or MultiPolygon"
        )
    polygons = coordinates if kind == "MultiPolygon" else [coordinates]
    try:
        return tuple(
            tuple(
                tuple((float(lon), float(lat)) for lon, lat, *_ in ring)
                for ring in polygon
            )
            for polygon in polygons
        )
    except (TypeError, ValueError) as error:
        raise FlexError(
            f"locations.geojson: {location_id!r} coordinates are not valid"
        ) from error


def read_locations(archive: Archive) -> dict[str, Location]:
    """locations.geojson features by id."""
    member = geojson_member(archive, "locations.geojson")
    if member is None:
        return {}
    try:
        collection = json.loads(archive.archive.read(member))
    except ValueError as error:
        raise FlexError(f"locations.geojson is not JSON: {error}") from error
    features = collection.get("features") if isinstance(collection, dict) else None
    if not isinstance(features, list):
        raise FlexError("locations.geojson: features is not a list")
    locations: dict[str, Location] = {}
    for feature in features:
        location_id = str(feature.get("id", "")) if isinstance(feature, dict) else ""
        if not location_id:
            raise FlexError("locations.geojson: a feature has no id")
        properties = feature.get("properties") or {}
        locations[location_id] = Location(
            location_id=location_id,
            name=str(properties.get("stop_name") or ""),
            desc=str(properties.get("stop_desc") or ""),
            polygons=read_polygons(location_id, feature.get("geometry")),
        )
    return locations


def read_location_groups(archive: Archive) -> dict[str, LocationGroup]:
    stop_ids: dict[str, list[str]] = defaultdict(list)
    for group_id, stop_id in archive.values(
        "location_group_stops", ("location_group_id", "stop_id")
    ):
        stop_ids[group_id].append(stop_id)
    return {
        group_id: LocationGroup(group_id, name, tuple(stop_ids[group_id]))
        for group_id, name in archive.values(
            "location_groups", ("location_group_id", "location_group_name")
        )
    }


def read_booking_rules(archive: Archive) -> dict[str, BookingRule]:
    columns = (
        "booking_rule_id",
        "booking_type",
        "prior_notice_duration_min",
        "prior_notice_duration_max",
        "prior_notice_last_day",
        "prior_notice_last_time",
        "prior_notice_start_day",
        "prior_notice_start_time",
        "prior_notice_service_id",
        "message",
        "pickup_message",
        "drop_off_message",
        "phone_number",
        "info_url",
        "booking_url",
    )
    rules: dict[str, BookingRule] = {}
    for row in archive.rows("booking_rules", columns):
        ints = {
            column: flex_field("booking_rules", row, column, parse_int)
            for column in (
                "booking_type",
                "prior_notice_duration_min",
                "prior_notice_duration_max",
                "prior_notice_last_day",
                "prior_notice_start_day",
            )
        }
        times = {
            column: flex_field("booking_rules", row, column, parse_time)
            for column in ("prior_notice_last_time", "prior_notice_start_time")
        }
        texts = {
            column: row[column].strip()
            for column in columns[1:]
            if column not in ints and column not in times
        }
        rules[row["booking_rule_id"]] = BookingRule(**texts, **ints, **times)
    return rules


def flex_misnamed(archive: Archive) -> tuple[tuple[str, str], ...]:
    """(name in the feed, name in GTFS) for Flex files and columns named
    otherwise."""
    present = {f"stop_times.txt {column}" for column in header(archive, "stop_times")}
    if geojson_member(archive, "location.geojson") is not None:
        present.add("location.geojson")
    return tuple(
        (given, spec) for given, spec in FLEX_MISNAMED.items() if given in present
    )


def read_flex(archive: Archive) -> Flex:
    """The Flex tables, each empty when its file cannot be read."""
    errors: list[str] = []

    def read[T](reader: Callable[[Archive], dict[str, T]]) -> dict[str, T]:
        try:
            return reader(archive)
        except FlexError as error:
            errors.append(str(error))
            return {}

    return Flex(
        locations=read(read_locations),
        groups=read(read_location_groups),
        booking_rules=read(read_booking_rules),
        misnamed=flex_misnamed(archive),
        errors=tuple(errors),
    )


def read_feed(
    source: ZipSource,
    keep_route: Callable[[Route], bool] | None = None,
    keep_services: Callable[[Feed], Collection[str]] | None = None,
    thin_shapes: PathFilter | None = None,
) -> Feed:
    """Parse a GTFS zip, keeping only routes accepted by `keep_route`.

    With `keep_services`, trips are read twice: first each route's
    service_ids, then, from the feed without trips (calendars of every
    service), `keep_services` names the services whose trips, stop_times,
    frequencies and shapes are read. `thin_shapes` drops shape points as
    they are read."""
    with open_zip(source) as raw:
        archive = Archive(raw)
        if missing := archive.missing():
            raise MissingFiles(f"GTFS zip is missing {', '.join(missing)}")

        routes = read_routes(archive)
        if keep_route is not None:
            routes = {key: route for key, route in routes.items() if keep_route(route)}
        trips: dict[str, Trip] = {}
        if keep_services is None:
            trips = read_trips(archive, set(routes))
            by_route: dict[str, set[str]] = defaultdict(set)
            for trip in trips.values():
                by_route[trip.route_id].add(trip.service_id)
            route_services = {key: frozenset(ids) for key, ids in by_route.items()}
            trip_count = len(trips)
        else:
            route_services, trip_count = read_route_services(archive, set(routes))
        service_ids = {sid for ids in route_services.values() for sid in ids}
        try:
            fares = read_fares(archive)
        except FaresError as error:
            fares = Fares(error=str(error))
        feed = Feed(
            agencies=read_agencies(archive),
            routes=routes,
            stops=read_stops(archive),
            trips={},
            stop_times={},
            calendars=read_calendars(archive, service_ids),
            calendar_dates=read_calendar_dates(archive, service_ids),
            shapes={},
            frequencies={},
            feed_info=read_feed_info(archive),
            fares=fares,
            translations=read_translations(archive),
            flex=read_flex(archive),
            route_services=route_services,
            trip_count=trip_count,
        )
        if keep_services is not None:
            trips = read_trips(archive, set(routes), set(keep_services(feed)))
        shape_ids = {t.shape_id for t in trips.values()} - {""}
        return replace(
            feed,
            trips=trips,
            stop_times=read_stop_times(archive, set(trips)),
            shapes=read_shapes(archive, shape_ids, thin_shapes),
            frequencies=read_frequencies(archive, set(trips)),
        )

"""What a route costs, from Fares v1 and v2 tables."""

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from gtfs_zone_timetable_sites.gtfs.reader import FareAttribute, Feed, Trip
from gtfs_zone_timetable_sites.timetable import stop_order


@dataclass(frozen=True, slots=True)
class Transfers:
    """v1 transfers included in a fare."""

    count: int | None  # None: unlimited
    duration: int | None  # seconds


@dataclass(frozen=True, slots=True)
class Fare:
    price: Decimal
    currency: str
    # v1: 0 paid on board, 1 paid before boarding.
    payment_method: int | None = None
    transfers: Transfers | None = None
    # v2: fare_product_name, fare_media_name and the timeframe group's id.
    product: str = ""
    media: str = ""
    timeframe: str = ""


@dataclass(frozen=True, slots=True)
class FlatFare:
    """The same fares wherever the rider boards and alights, cheapest first."""

    fares: tuple[Fare, ...]


@dataclass(frozen=True, slots=True)
class ZoneFares:
    """Fares by boarding and alighting place: `cells[i][j]` is the fares from
    `labels[i]` to `labels[j]`, places in route order. `zones` is the zone id
    behind each label, empty when each place is one stop."""

    labels: tuple[str, ...]
    cells: tuple[tuple[tuple[Fare, ...], ...], ...]
    zones: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NoFares:
    pass


RouteFares = FlatFare | ZoneFares | NoFares


def v1_fare(attribute: FareAttribute) -> Fare:
    return Fare(
        price=attribute.price,
        currency=attribute.currency,
        payment_method=attribute.payment_method,
        transfers=Transfers(attribute.transfers, attribute.transfer_duration),
    )


def distinct_prices(fares: Iterable[Fare]) -> tuple[Fare, ...]:
    """One fare per price, cheapest first; fields that differ between fares
    at one price are dropped."""
    by_price: dict[tuple[Decimal, str], list[Fare]] = {}
    for fare in fares:
        by_price.setdefault((fare.price, fare.currency), []).append(fare)
    out = []
    for (price, currency), same in sorted(by_price.items()):
        methods = {fare.payment_method for fare in same}
        transfers = {fare.transfers for fare in same}
        out.append(
            Fare(
                price=price,
                currency=currency,
                payment_method=methods.pop() if len(methods) == 1 else None,
                transfers=transfers.pop() if len(transfers) == 1 else None,
            )
        )
    return tuple(out)


def zone_order(feed: Feed, trips: Sequence[Trip]) -> list[str]:
    """The trips' fare zones in route order, direction 1 read backwards."""
    sequences: Counter[tuple[str, ...]] = Counter()
    for trip in trips:
        zones = [
            zone
            for st in feed.stop_times.get(trip.trip_id, ())
            if (zone := feed.stops[st.stop_id].zone_id)
        ]
        if trip.direction_id == 1:
            zones.reverse()
        # One entry per run of stops in a zone.
        runs = tuple(z for k, z in enumerate(zones) if k == 0 or z != zones[k - 1])
        if runs:
            sequences[runs] += 1
    patterns = sorted(sequences, key=lambda s: (-sequences[s], -len(s)))
    order = stop_order(patterns, [sequences[p] for p in patterns]) if patterns else []
    # A zone the route enters twice keeps its first place.
    return list(dict.fromkeys(order))


def route_fares(feed: Feed, trip_ids: Iterable[str]) -> RouteFares:
    """The v1 fares on the trips' routes: flat when every ride costs the
    same, else by boarding and alighting zone. Rules apply to the route (or
    any route when `route_id` is empty); origin and destination zones match
    the places of a ride, contains zones every zone it passes through. Rules
    naming zones or routes not on the trips are skipped, unknown fare ids
    raise KeyError."""
    fares = feed.fares
    if not fares.attributes:
        return NoFares()
    trips = [feed.trips[trip_id] for trip_id in trip_ids]
    route_ids = {trip.route_id for trip in trips}
    order = zone_order(feed, trips)
    index = {zone: k for k, zone in enumerate(order)}
    size = len(order)
    # Fares for any ride, and fares by (boarding, alighting) place.
    anywhere: set[str] = set()
    pairs: dict[tuple[int, int], set[str]] = {}
    if not fares.rules:
        # Without fare_rules a fare applies to every route of its agency.
        agencies = {feed.routes[route_id].agency_id for route_id in route_ids}
        anywhere = {
            fare_id
            for fare_id, attribute in fares.attributes.items()
            if not attribute.agency_id or attribute.agency_id in agencies
        }
    contains: dict[tuple[str, str], set[str]] = {}
    for rule in fares.rules:
        if rule.fare_id not in fares.attributes:
            raise KeyError(f"fare_rules: unknown fare_id {rule.fare_id!r}")
        if rule.route_id and rule.route_id not in route_ids:
            continue
        if rule.contains_id:
            contains.setdefault((rule.fare_id, rule.route_id), set()).add(
                rule.contains_id
            )
            continue
        if not rule.origin_id and not rule.destination_id:
            anywhere.add(rule.fare_id)
            continue
        if {rule.origin_id, rule.destination_id} - {""} - index.keys():
            continue
        starts = [index[rule.origin_id]] if rule.origin_id else range(size)
        ends = [index[rule.destination_id]] if rule.destination_id else range(size)
        for i in starts:
            for j in ends:
                pairs.setdefault((i, j), set()).add(rule.fare_id)
    # A ride passes through every zone between its two places.
    for (fare_id, _), listed in contains.items():
        for i in range(size):
            for j in range(size):
                if set(order[min(i, j) : max(i, j) + 1]) == listed:
                    pairs.setdefault((i, j), set()).add(fare_id)
    if not pairs:
        if not anywhere:
            return NoFares()
        return FlatFare(distinct_prices(v1_fare(fares.attributes[f]) for f in anywhere))
    cells = tuple(
        tuple(
            distinct_prices(
                v1_fare(fares.attributes[f])
                for f in anywhere | pairs.get((i, j), set())
            )
            for j in range(size)
        )
        for i in range(size)
    )
    flat = flat_cell(cells)
    if flat:
        return FlatFare(flat)
    stops = zone_stops(feed, trips, order)
    if stops:
        return ZoneFares(tuple(feed.stops[s].name for s in stops), cells)
    return ZoneFares(tuple(order), cells, tuple(order))


def flat_cell(
    cells: tuple[tuple[tuple[Fare, ...], ...], ...],
) -> tuple[Fare, ...] | None:
    """The fares of every ride when they are all the same: every ride between
    two places, and rides within a place when the feed prices them."""
    between = {
        cell for i, row in enumerate(cells) for j, cell in enumerate(row) if i != j
    }
    within = {row[i] for i, row in enumerate(cells)} - {()}
    same = between | within
    if len(same) == 1 and () not in same:
        return same.pop()
    return None


def zone_stops(feed: Feed, trips: Sequence[Trip], order: Sequence[str]) -> list[str]:
    """The station of each zone in `order` when every zone holds one station
    and every station one zone, else empty."""
    stations: dict[str, set[str]] = {}
    zones: dict[str, set[str]] = {}
    for trip in trips:
        for st in feed.stop_times.get(trip.trip_id, ()):
            zone = feed.stops[st.stop_id].zone_id
            if zone:
                station = feed.station(st.stop_id)
                stations.setdefault(zone, set()).add(station)
                zones.setdefault(station, set()).add(zone)
    if len(order) < 2 or any(
        len(v) != 1 for v in [*stations.values(), *zones.values()]
    ):
        return []
    return [next(iter(stations[zone])) for zone in order]

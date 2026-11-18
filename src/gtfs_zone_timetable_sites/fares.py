"""What a route costs, from Fares v1 and v2 tables."""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from gtfs_zone_timetable_sites.gtfs.reader import FareAttribute, Feed


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
    `labels[i]` to `labels[j]`, places in route order."""

    labels: tuple[str, ...]
    cells: tuple[tuple[tuple[Fare, ...], ...], ...]


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


def route_fares(feed: Feed, trip_ids: Iterable[str]) -> RouteFares:
    """The v1 fares that apply to every ride on the trips' routes: rules for
    the route (or any route) with no zones, or whose zones are the one zone
    all the trips' stops are in. Unknown fare ids raise KeyError."""
    fares = feed.fares
    if not fares.attributes:
        return NoFares()
    trips = [feed.trips[trip_id] for trip_id in trip_ids]
    route_ids = {trip.route_id for trip in trips}
    zones = {
        feed.stops[st.stop_id].zone_id
        for trip in trips
        for st in feed.stop_times.get(trip.trip_id, ())
    }
    zone = next(iter(zones)) if len(zones) == 1 and "" not in zones else None
    if not fares.rules:
        # Without fare_rules a fare applies to every route of its agency.
        agencies = {feed.routes[route_id].agency_id for route_id in route_ids}
        matched = {
            fare_id
            for fare_id, attribute in fares.attributes.items()
            if not attribute.agency_id or attribute.agency_id in agencies
        }
    else:
        matched = set()
        # contains_id rules match when the trip passes through exactly the
        # fare's listed zones, here the one zone of every stop.
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
            ends = {rule.origin_id, rule.destination_id} - {""}
            if not ends or ends == {zone}:
                matched.add(rule.fare_id)
        matched |= {
            fare_id for (fare_id, _), listed in contains.items() if listed == {zone}
        }
    if not matched:
        return NoFares()
    return FlatFare(distinct_prices(v1_fare(fares.attributes[f]) for f in matched))

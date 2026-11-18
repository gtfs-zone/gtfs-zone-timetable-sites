"""What a route costs, from Fares v1 and v2 tables."""

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from gtfs_zone_timetable_sites.gtfs.reader import (
    FareAttribute,
    FareLegRule,
    Fares,
    Feed,
    Trip,
)
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
    # v2: fare_product_name, the fare_media_names at this price when the
    # product's price depends on the media, and the timeframe group's id when
    # it is not all day.
    product: str = ""
    media: tuple[str, ...] = ()
    timeframe: str = ""


@dataclass(frozen=True, slots=True)
class FlatFare:
    """The same fares wherever the rider boards and alights, cheapest first."""

    fares: tuple[Fare, ...]


@dataclass(frozen=True, slots=True)
class ZoneFares:
    """Fares by boarding and alighting place: `cells[i][j]` is the fares from
    `labels[i]` to `labels[j]`, places in route order. `zones` is the zone
    key behind each label, empty when each place is one stop; `stations`
    maps each station in a single zone to its key. Labels are v1 zone ids,
    or with `named` v2 area names (empty for stops in no area)."""

    labels: tuple[str, ...]
    cells: tuple[tuple[tuple[Fare, ...], ...], ...]
    zones: tuple[str, ...] = ()
    stations: Mapping[str, str] = field(default_factory=dict)
    named: bool = False


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


ZoneOf = Callable[[str], str | None]


def stop_zone(feed: Feed) -> ZoneOf:
    """A stop's v1 zone_id, None without one."""
    return lambda stop_id: feed.stops[stop_id].zone_id or None


def zone_order(feed: Feed, trips: Sequence[Trip], zone_of: ZoneOf) -> list[str]:
    """The trips' fare zones in route order, direction 1 read backwards;
    stops whose zone is None are skipped."""
    sequences: Counter[tuple[str, ...]] = Counter()
    for trip in trips:
        zones = [
            zone
            for st in feed.stop_times.get(trip.trip_id, ())
            if (zone := zone_of(st.stop_id)) is not None
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
    """The fares on the trips' routes: Fares v2 when the feed has leg rules,
    else v1."""
    if feed.fares.leg_rules:
        return v2_route_fares(feed, [feed.trips[trip_id] for trip_id in trip_ids])
    if not feed.fares.attributes:
        return NoFares()
    return v1_route_fares(feed, [feed.trips[trip_id] for trip_id in trip_ids])


def v1_route_fares(feed: Feed, trips: Sequence[Trip]) -> RouteFares:
    """The v1 fares on the trips' routes: flat when every ride costs the
    same, else by boarding and alighting zone. Rules apply to the route (or
    any route when `route_id` is empty); origin and destination zones match
    the places of a ride, contains zones every zone it passes through. Rules
    naming zones or routes not on the trips are skipped, unknown fare ids
    raise KeyError."""
    fares = feed.fares
    route_ids = {trip.route_id for trip in trips}
    zone_of = stop_zone(feed)
    order = zone_order(feed, trips, zone_of)
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
    return ZoneFares(
        tuple(order), cells, tuple(order), station_zones(feed, trips, zone_of)
    )


def flat_cell[T](
    cells: tuple[tuple[tuple[T, ...], ...], ...],
) -> tuple[T, ...] | None:
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


def same_prices(
    cells: tuple[tuple[tuple[Fare, ...], ...], ...],
) -> tuple[Fare, ...] | None:
    """Every fare in `cells` when every ride costs the same prices, in the
    sense of `flat_cell`, whatever the products."""
    priced = tuple(
        tuple(tuple(sorted({(f.price, f.currency) for f in cell})) for cell in row)
        for row in cells
    )
    if flat_cell(priced) is None:
        return None
    fares = {fare for row in cells for cell in row for fare in cell}
    return tuple(
        sorted(fares, key=lambda f: (f.price, f.currency, f.product, f.timeframe))
    )


def station_zones(feed: Feed, trips: Sequence[Trip], zone_of: ZoneOf) -> dict[str, str]:
    """The zone of each station on the trips whose stops are all in one."""
    zones: dict[str, set[str | None]] = {}
    for trip in trips:
        for st in feed.stop_times.get(trip.trip_id, ()):
            zones.setdefault(feed.station(st.stop_id), set()).add(zone_of(st.stop_id))
    return {
        station: zone
        for station, keys in zones.items()
        if len(keys) == 1 and (zone := next(iter(keys)))
    }


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


def all_day(fares: Fares, group: str) -> bool:
    """Whether a timeframe group (empty: none) covers the whole day."""
    if not group:
        return True
    if group not in fares.timeframes:
        raise KeyError(f"fare_leg_rules: unknown timeframe_group_id {group!r}")
    return all(
        (frame.start or 0) == 0 and frame.end in (None, 24 * 3600)
        for frame in fares.timeframes[group]
    )


def rule_timeframe(fares: Fares, rule: FareLegRule) -> str:
    """The rule's timeframe group when it is not all day, else empty."""
    if not all_day(fares, rule.to_timeframe_group_id):
        raise ValueError(
            f"fare_leg_rules: to_timeframe_group_id {rule.to_timeframe_group_id!r}"
            " is not all day"
        )
    group = rule.from_timeframe_group_id
    return "" if all_day(fares, group) else group


def network_rules(fares: Fares, network: str, priority: bool) -> list[FareLegRule]:
    """The leg rules that can price a leg on `network`, transfer-only rules
    left out. With `priority` an empty network_id matches any network, else
    only when no rule names the network."""
    legs = [rule for rule in fares.leg_rules if not rule.transfer_only]
    if priority:
        return [rule for rule in legs if rule.network_id in ("", network)]
    exact = [rule for rule in legs if rule.network_id == network]
    return exact or [rule for rule in legs if not rule.network_id]


def leg_rules(
    by_from: Mapping[str, Sequence[FareLegRule]],
    starts: frozenset[str],
    ends: frozenset[str],
    priority: bool,
    timeframe: Callable[[FareLegRule], str],
) -> list[FareLegRule]:
    """The rules pricing a leg from a stop in the `starts` areas to one in
    the `ends` areas. Without `priority` an empty area matches only when no
    rule names one of the leg's areas; with it, an empty area matches any,
    and per timeframe only the highest `rule_priority` is kept."""
    named = [rule for area in sorted(starts) for rule in by_from.get(area, ())]
    if priority:
        hits = [
            rule
            for rule in [*by_from.get("", ()), *named]
            if not rule.to_area_id or rule.to_area_id in ends
        ]
        best: dict[str, int] = {}
        for rule in hits:
            key = timeframe(rule)
            best[key] = max(best.get(key, 0), rule.rule_priority or 0)
        return [
            rule for rule in hits if (rule.rule_priority or 0) == best[timeframe(rule)]
        ]
    hits = named or list(by_from.get("", ()))
    return [rule for rule in hits if rule.to_area_id in ends] or [
        rule for rule in hits if not rule.to_area_id
    ]


def product_fares(
    fares: Fares,
    rules: Iterable[FareLegRule],
    timeframe: Callable[[FareLegRule], str],
    all_media: bool = False,
) -> tuple[Fare, ...]:
    """The products of `rules`, cheapest first, as `priced_products`."""
    return priced_products(
        fares, ((rule.fare_product_id, timeframe(rule)) for rule in rules), all_media
    )


def priced_products(
    fares: Fares, products: Iterable[tuple[str, str]], all_media: bool = False
) -> tuple[Fare, ...]:
    """The (fare_product_id, timeframe) products, cheapest first: one fare
    per product name and timeframe, or per price with its media when the
    price depends on the media (or always with `all_media`). Unknown product
    or media ids raise KeyError."""
    prices: dict[tuple[str, str], dict[tuple[Decimal, str], set[str]]] = {}
    for product_id, frame in products:
        if product_id not in fares.products:
            raise KeyError(f"unknown fare_product_id {product_id!r}")
        for product in fares.products[product_id]:
            media_id = product.fare_media_id
            if media_id and media_id not in fares.media:
                raise KeyError(f"fare_products: unknown fare_media_id {media_id!r}")
            media = fares.media[media_id].name if media_id else ""
            key = (product.name, frame)
            by_price = prices.setdefault(key, {})
            by_price.setdefault((product.amount, product.currency), set()).add(media)
    out = set()
    for (name, frame), by_price in prices.items():
        several = all_media or len(by_price) > 1
        for (amount, currency), media in by_price.items():
            out.add(
                Fare(
                    price=amount,
                    currency=currency,
                    product=name,
                    media=tuple(sorted(media - {""})) if several else (),
                    timeframe=frame,
                )
            )
    return tuple(
        sorted(out, key=lambda f: (f.price, f.currency, f.product, f.timeframe))
    )


def route_network(feed: Feed, route_id: str) -> str:
    """The route's network: route_networks.txt, else routes.network_id."""
    return feed.fares.route_networks.get(route_id, feed.routes[route_id].network_id)


def v2_route_fares(feed: Feed, trips: Sequence[Trip]) -> RouteFares:
    """The v2 fares on the trips: leg rules of their networks, by the areas
    of the boarding and alighting stops (a stop is in its own and its
    station's areas). Flat when every ride costs the same, else by area,
    the places being the sets of areas the rules name."""
    fares = feed.fares
    priority = any(rule.rule_priority is not None for rule in fares.leg_rules)
    networks = sorted({route_network(feed, trip.route_id) for trip in trips})
    rule_sets = [network_rules(fares, network, priority) for network in networks]
    named = {
        area
        for rules in rule_sets
        for rule in rules
        for area in (rule.from_area_id, rule.to_area_id)
        if area
    }
    stop_areas: dict[str, set[str]] = {}
    for area, stop_ids in fares.stop_areas.items():
        if area in named:
            for stop_id in stop_ids:
                stop_areas.setdefault(stop_id, set()).add(area)
    places: dict[str, frozenset[str]] = {}

    def zone_of(stop_id: str) -> str:
        parent = feed.stops[stop_id].parent_station
        areas = frozenset(
            stop_areas.get(stop_id, set()) | stop_areas.get(parent, set())
        )
        key = "\t".join(sorted(areas))
        places[key] = areas
        return key

    def timeframe(rule: FareLegRule) -> str:
        return rule_timeframe(fares, rule)

    indexes = []
    for rules in rule_sets:
        by_from: dict[str, list[FareLegRule]] = {}
        for rule in rules:
            by_from.setdefault(rule.from_area_id, []).append(rule)
        indexes.append(by_from)
    order = zone_order(feed, trips, zone_of)
    cells = tuple(
        tuple(
            product_fares(
                fares,
                (
                    rule
                    for by_from in indexes
                    for rule in leg_rules(
                        by_from, places[start], places[end], priority, timeframe
                    )
                ),
                timeframe,
            )
            for end in order
        )
        for start in order
    )
    # Places with no fare from or to them.
    keep = [
        i for i in range(len(order)) if any(cells[i]) or any(row[i] for row in cells)
    ]
    order = [order[i] for i in keep]
    cells = tuple(tuple(cells[i][j] for j in keep) for i in keep)
    if not order:
        return NoFares()
    flat = same_prices(cells)
    if flat:
        return FlatFare(flat)
    labels = tuple(
        " / ".join(sorted(fares.areas.get(area) or area for area in places[key]))
        for key in order
    )
    return ZoneFares(
        labels, cells, tuple(order), station_zones(feed, trips, zone_of), named=True
    )


@dataclass(frozen=True, slots=True)
class FareGroup:
    """The fares of one v2 network or v1 agency, cheapest first. `name` is
    the network or agency name, empty for fares of any network or agency."""

    name: str
    fares: tuple[Fare, ...]


@dataclass(frozen=True, slots=True)
class LegGroup:
    networks: tuple[str, ...]  # network names, empty for any network
    products: tuple[str, ...]
    # The area every leg of the group starts in, empty when they differ.
    from_area: str = ""


@dataclass(frozen=True, slots=True)
class TransferRule:
    """A v2 transfer from a leg of one leg group to one of another; each leg
    group is named by its networks and products, empty for any leg."""

    from_legs: LegGroup | None
    to_legs: LegGroup | None
    # None or 1: one transfer; -1: unlimited.
    count: int | None
    duration: int | None  # seconds
    # 0: first fare plus fee, 1: both fares plus fee, 2: the fee only.
    fare_transfer_type: int
    fee: tuple[Fare, ...]


@dataclass(frozen=True, slots=True)
class SiteFares:
    groups: tuple[FareGroup, ...]
    transfers: tuple[TransferRule, ...] = ()


def site_fares(feed: Feed) -> SiteFares | None:
    """Every fare of the feed's routes: v2 products by network when the feed
    has leg rules, else v1 fares by agency. None without fares."""
    if feed.fares.leg_rules:
        return v2_site_fares(feed)
    if feed.fares.attributes:
        return v1_site_fares(feed)
    return None


def v1_site_fares(feed: Feed) -> SiteFares | None:
    """The v1 fares that apply to a route of the feed, grouped by agency:
    fares with no rules, or a rule with an empty or known `route_id`.
    Unknown fare ids raise KeyError."""
    fares = feed.fares
    ruled: set[str] = set()
    used: set[str] = set()
    for rule in fares.rules:
        if rule.fare_id not in fares.attributes:
            raise KeyError(f"fare_rules: unknown fare_id {rule.fare_id!r}")
        ruled.add(rule.fare_id)
        if not rule.route_id or rule.route_id in feed.routes:
            used.add(rule.fare_id)
    agencies = {route.agency_id for route in feed.routes.values()}
    by_agency: dict[str, list[Fare]] = {}
    for fare_id, attribute in fares.attributes.items():
        if fare_id in ruled and fare_id not in used:
            continue
        agency = attribute.agency_id
        if agency and agency not in agencies:
            continue
        if not agency and len(agencies) == 1:
            agency = next(iter(agencies))
        by_agency.setdefault(agency, []).append(v1_fare(attribute))
    groups = tuple(
        FareGroup(
            feed.agencies[agency].name if agency in feed.agencies else agency,
            distinct_prices(listed),
        )
        for agency, listed in by_agency.items()
    )
    groups = tuple(sorted(groups, key=lambda g: (g.name != "", g.name)))
    return SiteFares(groups) if groups else None


def v2_site_fares(feed: Feed) -> SiteFares | None:
    """The v2 products of each network the feed's routes are on, with every
    media at each price, and the transfer rules between their leg groups.
    Transfer-only leg rules price no network's fares."""
    fares = feed.fares
    networks = {route_network(feed, route_id) for route_id in feed.routes}

    def timeframe(rule: FareLegRule) -> str:
        return rule_timeframe(fares, rule)

    by_network: dict[str, list[FareLegRule]] = {}
    for rule in fares.leg_rules:
        if rule.transfer_only:
            continue
        if not rule.network_id or rule.network_id in networks:
            by_network.setdefault(rule.network_id, []).append(rule)
    groups = tuple(
        FareGroup(
            fares.networks.get(network) or network,
            product_fares(fares, rules, timeframe, all_media=True),
        )
        for network, rules in by_network.items()
    )
    groups = tuple(sorted(groups, key=lambda g: (g.name != "", g.name)))
    if not groups:
        return None
    return SiteFares(groups, transfer_rules(feed))


def transfer_rules(feed: Feed) -> tuple[TransferRule, ...]:
    """The feed's transfer rules, leg groups named by their networks,
    product names and the area all their legs start in; rules that read the
    same are listed once. Unknown leg
    groups or products raise KeyError."""
    fares = feed.fares
    networks: dict[str, set[str]] = {}
    names: dict[str, set[str]] = {}
    starts: dict[str, set[str]] = {}
    for rule in fares.leg_rules:
        if rule.leg_group_id:
            starts.setdefault(rule.leg_group_id, set()).add(rule.from_area_id)
            if rule.fare_product_id not in fares.products:
                raise KeyError(f"unknown fare_product_id {rule.fare_product_id!r}")
            names.setdefault(rule.leg_group_id, set()).update(
                product.name for product in fares.products[rule.fare_product_id]
            )
            networks.setdefault(rule.leg_group_id, set()).add(
                fares.networks.get(rule.network_id) or rule.network_id
            )

    def legs(group: str) -> LegGroup | None:
        if not group:
            return None
        if group not in names:
            raise KeyError(f"fare_transfer_rules: unknown leg_group_id {group!r}")
        area = next(iter(starts[group])) if len(starts[group]) == 1 else ""
        return LegGroup(
            tuple(sorted(networks[group] - {""})),
            tuple(sorted(names[group])),
            fares.areas.get(area) or area,
        )

    out: dict[TransferRule, None] = {}
    for rule in fares.transfer_rules:
        fee = ()
        if rule.fare_product_id:
            fee = priced_products(fares, [(rule.fare_product_id, "")])
        transfer = TransferRule(
            from_legs=legs(rule.from_leg_group_id),
            to_legs=legs(rule.to_leg_group_id),
            count=rule.transfer_count,
            duration=rule.duration_limit,
            fare_transfer_type=rule.fare_transfer_type,
            fee=fee,
        )
        out[transfer] = None
    return tuple(out)

"""Route + direction + day type to a Timetable: stops down, trips across."""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from functools import cmp_to_key
from itertools import pairwise

from gtfs_zone_timetable_sites.config import TimeFormat, Timepoints
from gtfs_zone_timetable_sites.gtfs.reader import Feed, StopTime, Trip
from gtfs_zone_timetable_sites.gtfs.service import (
    DAY_SECONDS,
    DayType,
    start_day_offset,
    timezone_shift,
    trip_note,
)

# Basic GTFS route types, then extended ones as (first, last) ranges.
MODES = {
    0: "light rail",
    1: "subway",
    2: "train",
    3: "bus",
    4: "ferry",
    5: "cable car",
    6: "gondola",
    7: "funicular",
    11: "trolleybus",
    12: "monorail",
}
EXTENDED_MODES = (
    (100, 199, "train"),
    (200, 299, "bus"),
    (400, 499, "subway"),
    (700, 799, "bus"),
    (800, 800, "trolleybus"),
    (900, 999, "light rail"),
    (1000, 1099, "ferry"),
    (1300, 1399, "gondola"),
    (1400, 1499, "funicular"),
)
# Modes shown with every stop.
ALL_STOPS_MODES = frozenset(
    {
        "light rail",
        "subway",
        "train",
        "ferry",
        "cable car",
        "gondola",
        "funicular",
        "monorail",
    }
)
# Headsign groups sharing less than this share of the smaller one's stops get
# separate tables.
BRANCH_OVERLAP = 0.5


def mode_name(route_type: int) -> str:
    """'bus', 'train' and so on for a route type, else 'transit'."""
    if route_type in MODES:
        return MODES[route_type]
    for first, last, name in EXTENDED_MODES:
        if first <= route_type <= last:
            return name
    return "transit"


@dataclass(frozen=True, slots=True)
class Cell:
    """A trip at a stop; both times None when the feed gives no time there."""

    arrival: int | None
    departure: int | None
    pickup: bool = True
    drop_off: bool = True
    # Stops only on request: a flag stop.
    request: bool = False

    @property
    def time(self) -> int | None:
        return self.departure if self.departure is not None else self.arrival


@dataclass(frozen=True, slots=True)
class Headway:
    """A frequency-based trip: runs every `headway_secs` from start to end."""

    start: int
    end: int
    headway_secs: int


@dataclass(frozen=True, slots=True)
class Column:
    trip_id: str
    short_name: str
    headsign: str
    cells: tuple[Cell | None, ...]
    headway: Headway | None = None
    # GTFS bikes_allowed and wheelchair_accessible: 0 unknown, 1 yes, 2 no.
    bikes: int = 0
    wheelchair: int = 0

    def first_time(self) -> int | None:
        return next((c.time for c in self.cells if c and c.time is not None), None)


@dataclass(frozen=True, slots=True)
class Row:
    stop_id: str
    name: str
    timepoint: bool
    timezone: str | None = None
    # wheelchair_boarding shared by every stop served here, else 0.
    wheelchair: int = 0


@dataclass(frozen=True, slots=True)
class Timetable:
    route_id: str
    direction_id: int | None
    headsigns: tuple[str, ...]
    day_type: DayType
    rows: tuple[Row, ...]
    columns: tuple[Column, ...]


def lcs_pairs(a: Sequence[str], b: Sequence[str]) -> list[tuple[int, int]]:
    """Index pairs of a longest common subsequence, matching early in `a`."""
    n, m = len(a), len(b)
    suffix = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            if a[i] == b[j]:
                suffix[i][j] = suffix[i + 1][j + 1] + 1
            else:
                suffix[i][j] = max(suffix[i + 1][j], suffix[i][j + 1])
    pairs = []
    i = j = 0
    while i < n and j < m:
        if a[i] == b[j] and suffix[i][j] == suffix[i + 1][j + 1] + 1:
            pairs.append((i, j))
            i, j = i + 1, j + 1
        elif suffix[i + 1][j] >= suffix[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


def merge_sequences(sequences: Sequence[Sequence[str]]) -> list[str]:
    """Shortest-ish supersequence: fold each in, inserting unmatched stops in order."""
    merged: list[str] = list(sequences[0]) if sequences else []
    for sequence in sequences[1:]:
        result: list[str] = []
        i = j = 0
        for mi, sj in [*lcs_pairs(merged, sequence), (len(merged), len(sequence))]:
            result += merged[i:mi]
            result += sequence[j:sj]
            if mi < len(merged):
                result.append(merged[mi])
            i, j = mi + 1, sj + 1
        merged = result
    return merged


def topo_order(
    sequences: Sequence[Sequence[str]], weights: Sequence[int]
) -> list[str] | None:
    """Kahn's algorithm over consecutive-stop edges, as gtfs-zone-web-common's
    route-sequence; None when patterns run opposite ways (a cycle).

    Among ready stops, one the previous stop leads to comes first, so a
    branch stays contiguous; then lowest trip-weighted mean position."""
    outgoing: dict[str, dict[str, None]] = {}
    indegree: dict[str, int] = {}
    position_sum: dict[str, float] = {}
    position_weight: dict[str, int] = {}
    for sequence, weight in zip(sequences, weights, strict=True):
        span = len(sequence) - 1
        for j, stop in enumerate(sequence):
            outgoing.setdefault(stop, {})
            indegree.setdefault(stop, 0)
            position_sum[stop] = position_sum.get(stop, 0) + (
                j / span * weight if span else 0
            )
            position_weight[stop] = position_weight.get(stop, 0) + weight
        for a, b in pairwise(sequence):
            if b not in outgoing[a]:
                outgoing[a][b] = None
                indegree[b] += 1

    def mean(stop: str) -> float:
        return position_sum[stop] / position_weight[stop]

    ready = [stop for stop, degree in indegree.items() if degree == 0]
    order: list[str] = []
    previous: str | None = None
    while ready:
        follows = outgoing[previous] if previous is not None else {}
        pick = min(ready, key=lambda stop: (stop not in follows, mean(stop), stop))
        ready.remove(pick)
        order.append(pick)
        previous = pick
        for stop in outgoing[pick]:
            indegree[stop] -= 1
            if indegree[stop] == 0:
                ready.append(stop)
    return order if len(order) == len(indegree) else None


def stop_order(patterns: Sequence[Sequence[str]], weights: Sequence[int]) -> list[str]:
    """Every pattern's stops in one order, each stop once where possible."""
    return topo_order(patterns, weights) or merge_sequences(patterns)


def compare_columns(a: Column, b: Column) -> int:
    """Order by time at the first stop both serve, else by first time."""
    for ca, cb in zip(a.cells, b.cells, strict=True):
        if ca and cb and ca.time is not None and cb.time is not None:
            return (ca.time > cb.time) - (ca.time < cb.time)
    ta, tb = a.first_time() or 0, b.first_time() or 0
    return (ta > tb) - (ta < tb)


def spread(candidates: list[int], chosen: set[int], count: int) -> set[int]:
    """Pick `count` of `candidates` as far as possible from each other and `chosen`."""
    picked = set(chosen)
    pool = sorted(candidates)
    for _ in range(min(count, len(pool))):
        best = max(pool, key=lambda p: min((abs(p - q) for q in picked), default=0))
        picked.add(best)
        pool.remove(best)
    return picked - chosen


def auto_timepoints(
    columns: Sequence[Column], patterns: Sequence[list[int]], size: int, cap: int
) -> set[int]:
    """Ends of the table, then ends of each pattern, then rows served by at
    least half as many trips as the busiest, then the rest, spread evenly."""
    served = Counter(
        row
        for column in columns
        for row, cell in enumerate(column.cells)
        if cell and cell.time is not None
    )
    busiest = max(served.values(), default=0)
    tiers: list[list[int]] = [
        [0, size - 1],
        sorted({p for pattern in patterns for p in (pattern[0], pattern[-1])}),
        [row for row, n in served.items() if 2 * n >= busiest],
        [row for row, n in served.items() if 2 * n < busiest],
    ]

    chosen: set[int] = set()
    for tier in tiers:
        fresh = [row for row in tier if row not in chosen]
        room = cap - len(chosen)
        if room <= 0:
            break
        chosen |= set(fresh) if len(fresh) <= room else spread(fresh, chosen, room)
    return chosen


def shifted(cell: Cell, seconds: int) -> Cell:
    return replace(
        cell,
        arrival=None if cell.arrival is None else cell.arrival + seconds,
        departure=None if cell.departure is None else cell.departure + seconds,
    )


def trip_columns(feed: Feed, trip: Trip) -> list[tuple[Headway | None, int]]:
    """(headway, time shift) per column: one per exact_times departure or window."""
    frequencies = feed.frequencies.get(trip.trip_id)
    if not frequencies:
        return [(None, 0)]
    first = next(
        (st.time for st in feed.stop_times[trip.trip_id] if st.time is not None), 0
    )
    columns: list[tuple[Headway | None, int]] = []
    for frequency in frequencies:
        if frequency.exact_times:
            columns += [
                (None, departure - first)
                for departure in range(
                    frequency.start, frequency.end, frequency.headway_secs
                )
            ]
        else:
            headway = Headway(frequency.start, frequency.end, frequency.headway_secs)
            columns.append((headway, frequency.start - first))
    return columns


def headsign(feed: Feed, trip: Trip) -> str:
    if trip.headsign:
        return trip.headsign
    last = feed.stop_times[trip.trip_id][-1].stop_id
    stop = feed.stops.get(last)
    return stop.name if stop else last


def branch_key(feed: Feed, trip: Trip) -> str:
    """The trip's headsign, or its station pattern when the feed gives none."""
    if trip.headsign:
        return trip.headsign
    return "\0".join(feed.station(st.stop_id) for st in feed.stop_times[trip.trip_id])


def branches(feed: Feed, trips: Sequence[Trip]) -> list[list[Trip]]:
    """Split trips into groups of headsigns (or patterns, without headsigns)
    whose stop sets overlap enough."""
    stops_by_headsign: dict[str, set[str]] = {}
    for trip in trips:
        stops = {feed.station(st.stop_id) for st in feed.stop_times[trip.trip_id]}
        stops_by_headsign.setdefault(branch_key(feed, trip), set()).update(stops)

    names = list(stops_by_headsign)
    parent = {name: name for name in names}

    def root(name: str) -> str:
        while parent[name] != name:
            name = parent[name]
        return name

    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            sa, sb = stops_by_headsign[a], stops_by_headsign[b]
            if len(sa & sb) / min(len(sa), len(sb)) >= BRANCH_OVERLAP:
                parent[root(b)] = root(a)

    groups: dict[str, list[Trip]] = {}
    for trip in trips:
        groups.setdefault(root(branch_key(feed, trip)), []).append(trip)
    return list(groups.values())


def build_timetable(
    feed: Feed,
    trips: Sequence[Trip],
    day_type: DayType,
    timepoints: Timepoints,
    cap: int,
) -> Timetable:
    route = feed.routes[trips[0].route_id]
    stop_times: dict[str, tuple[StopTime, ...]] = {
        trip.trip_id: feed.stop_times[trip.trip_id] for trip in trips
    }
    sequences = [
        tuple(feed.station(st.stop_id) for st in times) for times in stop_times.values()
    ]
    counts = Counter(sequences)
    patterns = sorted(counts, key=lambda s: (-counts[s], -len(s)))
    merged = stop_order(patterns, [counts[p] for p in patterns])
    positions = {
        pattern: [mi for mi, _ in lcs_pairs(merged, pattern)] for pattern in patterns
    }

    route_zone = feed.timezone(route)
    shifts = [0] * len(merged)
    zones: list[str | None] = [None] * len(merged)
    for row, stop_id in enumerate(merged):
        zone = feed.stop_timezone(stop_id)
        if zone and zone != route_zone:
            shifts[row] = timezone_shift(zone, route_zone, day_type.start)
            zones[row] = zone

    columns = []
    flagged: set[int] = set()
    boarding: list[set[int]] = [set() for _ in merged]
    for trip in trips:
        times = stop_times[trip.trip_id]
        rows = positions[tuple(feed.station(st.stop_id) for st in times)]
        for row, st in zip(rows, times, strict=True):
            if st.timepoint == 1:
                flagged.add(row)
            boarding[row].add(feed.wheelchair_boarding(st.stop_id))
        # Trips starting before local midnight run on the previous local day.
        day_shift = -start_day_offset(feed, trip.trip_id, day_type.start) * DAY_SECONDS
        for headway, offset in trip_columns(feed, trip):
            cells: list[Cell | None] = [None] * len(merged)
            for row, st in zip(rows, times, strict=True):
                cell = Cell(
                    arrival=st.arrival,
                    departure=st.departure,
                    pickup=st.pickup_type != 1,
                    drop_off=st.drop_off_type != 1,
                    request=3 in (st.pickup_type, st.drop_off_type),
                )
                cells[row] = shifted(cell, offset + shifts[row] + day_shift)
            columns.append(
                Column(
                    trip_id=trip.trip_id,
                    short_name=trip.short_name,
                    headsign=headsign(feed, trip),
                    cells=tuple(cells),
                    headway=headway,
                    bikes=trip.bikes_allowed,
                    wheelchair=trip.wheelchair_accessible,
                )
            )
    columns.sort(key=cmp_to_key(compare_columns))

    every_row = set(range(len(merged)))
    if timepoints == "all":
        chosen = every_row
    elif timepoints == "timepoint-flag":
        chosen = flagged or every_row
    elif mode_name(route.route_type) in ALL_STOPS_MODES or len(merged) <= cap:
        chosen = every_row
    elif flagged:
        ends = {p for pattern in positions.values() for p in (pattern[0], pattern[-1])}
        chosen = flagged | ends
    else:
        chosen = auto_timepoints(columns, list(positions.values()), len(merged), cap)

    names = Counter(column.headsign for column in columns)
    return Timetable(
        route_id=route.route_id,
        direction_id=trips[0].direction_id,
        headsigns=tuple(name for name, _ in names.most_common()),
        day_type=day_type,
        rows=tuple(
            Row(
                stop_id=stop_id,
                name=feed.stops[stop_id].name if stop_id in feed.stops else stop_id,
                timepoint=row in chosen,
                timezone=zones[row],
                wheelchair=next(iter(boarding[row])) if len(boarding[row]) == 1 else 0,
            )
            for row, stop_id in enumerate(merged)
        ),
        columns=tuple(columns),
    )


def route_timetables(
    feed: Feed,
    route_id: str,
    day_types: Sequence[DayType],
    timepoints: Timepoints = "auto",
    cap: int = 8,
) -> list[Timetable]:
    """Timetables per direction, then branch, then day type."""
    trips = [
        trip
        for trip in feed.trips.values()
        if trip.route_id == route_id and feed.stop_times.get(trip.trip_id)
    ]
    by_direction: dict[int, list[Trip]] = {}
    for trip in trips:
        by_direction.setdefault(trip.direction_id or 0, []).append(trip)

    tables = []
    for _, direction_trips in sorted(by_direction.items()):
        for branch in branches(feed, direction_trips):
            for day_type in day_types:
                running = [t for t in branch if t.trip_id in day_type.trip_ids]
                if running:
                    tables.append(
                        build_timetable(feed, running, day_type, timepoints, cap)
                    )
    return tables


def format_time(seconds: int, time_format: TimeFormat) -> str:
    """Clock time with a day marker: '+n' past midnight, '-n' before."""
    days, rest = divmod(seconds // 60, 24 * 60)
    hours, minutes = divmod(rest, 60)
    marker = f"{days:+d}" if days else ""
    if time_format == "24h":
        return f"{hours:02d}:{minutes:02d}{marker}"
    suffix = "a" if hours < 12 else "p"
    return f"{(hours - 1) % 12 + 1}:{minutes:02d}{suffix}{marker}"


def format_cell(cell: Cell | None, time_format: TimeFormat) -> str:
    if cell is None:
        return ""
    if cell.time is None:
        return "|"
    return format_time(cell.time, time_format)


def to_text(
    table: Timetable, time_format: TimeFormat = "12h", all_rows: bool = False
) -> str:
    """Plain-text grid for checking a timetable against a printed one."""
    rows = [(i, row) for i, row in enumerate(table.rows) if all_rows or row.timepoint]
    day = table.day_type
    title = f"To {' / '.join(table.headsigns)} - {day.name}"
    lines = [title, f"{day.start} to {day.end}"]
    if day.extra:
        lines.append("Also " + ", ".join(str(d) for d in day.extra))
    if day.missing:
        lines.append("Not " + ", ".join(str(d) for d in day.missing))
    notes = [trip_note(day, column.trip_id) for column in table.columns]
    letters = dict.fromkeys(note for note in notes if note)
    letters = {note: chr(ord("A") + i) for i, note in enumerate(letters)}
    lines += [f"{letter}: {note}" for note, letter in letters.items()]

    headers = []
    for column in table.columns:
        if column.headway:
            minutes = column.headway.headway_secs // 60
            until = format_time(column.headway.end, time_format)
            headers.append(f"every {minutes}m to {until}")
        else:
            headers.append(column.short_name)
    grid = [
        [format_cell(column.cells[i], time_format) for column in table.columns]
        for i, _ in rows
    ]
    width = max([7, *(len(h) for h in headers if len(h) <= 12)])
    name_width = max((len(row.name) for _, row in rows), default=0)
    if any(headers):
        lines.append(
            " " * name_width + "".join(f" {h[:width]:>{width}}" for h in headers)
        )
    if letters:
        marks = [letters.get(note, "") for note in notes]
        lines.append(" " * name_width + "".join(f" {m:>{width}}" for m in marks))
    for (_, row), cells in zip(rows, grid, strict=True):
        lines.append(
            f"{row.name:<{name_width}}" + "".join(f" {c:>{width}}" for c in cells)
        )
    return "\n".join(lines)

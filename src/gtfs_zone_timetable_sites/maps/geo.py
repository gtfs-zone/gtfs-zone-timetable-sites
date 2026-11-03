"""Route and system map data for map.js: simplified lines as [lon, lat]
arrays, numbered stops, and routes deduplicated by geometry."""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise

from gtfs_zone_timetable_sites.gtfs.reader import Feed
from gtfs_zone_timetable_sites.strip import endpoint_threshold
from gtfs_zone_timetable_sites.timetable import Timetable

# Douglas-Peucker tolerance in metres: a share of the map's extent, at least
# a floor; for a route map and for the system map.
ROUTE_TOLERANCE = 6.0
ROUTE_SHARE = 1 / 20_000
SYSTEM_TOLERANCE = 20.0
SYSTEM_SHARE = 1 / 5_000
# Decimal places kept: 5 is about 1 m.
DIGITS = 5
M_PER_DEG = 111_320.0

type Point = tuple[float, float]


@dataclass(frozen=True, slots=True)
class SystemRoute:
    """One route on the system map: what map.js shows for it and its tables."""

    slug: str
    badge: str
    title: str
    colors: tuple[str, str] | None
    mode: str
    tables: Sequence[Timetable]
    # The feed's route ids behind the route, for matching vehicles to it.
    route_ids: Sequence[str]


def simplify(points: Sequence[Point], tolerance: float) -> list[Point]:
    """Douglas-Peucker, iterative so long rail shapes cannot hit recursion limits."""
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        (ax, ay), (bx, by) = points[first], points[last]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        worst, index = 0.0, -1
        for i in range(first + 1, last):
            px, py = points[i]
            if length == 0:
                distance = math.hypot(px - ax, py - ay)
            else:
                distance = abs(dy * (px - ax) - dx * (py - ay)) / length
            if distance > worst:
                worst, index = distance, i
        if worst > tolerance:
            keep[index] = True
            stack.append((first, index))
            stack.append((index, last))
    return [p for p, kept in zip(points, keep, strict=True) if kept]


def tolerance(paths: Sequence[Sequence[Point]], floor: float, share: float) -> float:
    """`share` of the diagonal of the (lat, lon) paths' box in metres, at
    least `floor`."""
    lats = [lat for path in paths for lat, _ in path]
    lons = [lon for path in paths for _, lon in path]
    if not lats:
        return floor
    kx = math.cos(math.radians((min(lats) + max(lats)) / 2)) * M_PER_DEG
    diagonal = math.hypot(
        (max(lons) - min(lons)) * kx, (max(lats) - min(lats)) * M_PER_DEG
    )
    return max(floor, diagonal * share)


def cells(a: Point, b: Point, size: float) -> set[tuple[int, int]]:
    """Grid cells of `size` metres that points every half cell along a to b
    fall in."""
    n = max(1, math.ceil(math.dist(a, b) / (size / 2)))
    return {
        (
            math.floor((a[0] + (b[0] - a[0]) * i / n) / size),
            math.floor((a[1] + (b[1] - a[1]) * i / n) / size),
        )
        for i in range(n + 1)
    }


def undrawn(paths: Sequence[Sequence[Point]], size: float) -> list[list[Point]]:
    """Plane paths, longest first, cut to their runs of segments not already
    drawn: a segment is drawn when each cell along it holds an earlier one."""
    drawn: set[tuple[int, int]] = set()
    runs = []

    def length(path: Sequence[Point]) -> float:
        return sum(math.dist(a, b) for a, b in pairwise(path))

    for path in sorted(paths, key=length, reverse=True):
        if len(path) == 1:
            cell = cells(path[0], path[0], size)
            if not cell <= drawn:
                runs.append(list(path))
                drawn |= cell
            continue
        run: list[Point] = []
        for a, b in pairwise(path):
            along = cells(a, b, size)
            if along <= drawn:
                if len(run) > 1:
                    runs.append(run)
                run = []
                continue
            run = run or [a]
            run.append(b)
            drawn |= along
        if len(run) > 1:
            runs.append(run)
    return runs


def lines_of(paths: Sequence[Sequence[Point]], tol: float) -> list[tuple[Point, ...]]:
    """(lat, lon) paths to [lon, lat] lines: simplified in metres on a local
    plane, parts that retrace an earlier line dropped, rounded, repeats after
    rounding dropped."""
    if not paths:
        return []
    kx = math.cos(math.radians(paths[0][0][0])) * M_PER_DEG
    planes = [
        simplify([(lon * kx, lat * M_PER_DEG) for lat, lon in path], tol)
        for path in paths
    ]
    lines = []
    for run in undrawn(planes, 2 * tol):
        points = [(round(x / kx, DIGITS), round(y / M_PER_DEG, DIGITS)) for x, y in run]
        lines.append(
            tuple(p for i, p in enumerate(points) if i == 0 or p != points[i - 1])
        )
    return lines


def trip_ids(tables: Sequence[Timetable]) -> list[str]:
    ids = dict.fromkeys(column.trip_id for table in tables for column in table.columns)
    return list(ids)


def route_paths(feed: Feed, tables: Sequence[Timetable]) -> list[tuple[Point, ...]]:
    """The route's distinct shapes as (lat, lon), or its stop sequences for
    trips without one."""
    shape_ids: dict[str, None] = {}
    sequences: dict[tuple[str, ...], None] = {}
    for trip_id in trip_ids(tables):
        trip = feed.trips[trip_id]
        if trip.shape_id in feed.shapes:
            shape_ids[trip.shape_id] = None
        else:
            sequences[tuple(st.stop_id for st in feed.stop_times[trip_id])] = None
    paths = [feed.shapes[shape_id] for shape_id in shape_ids]
    for sequence in sequences:
        path = tuple(
            (stop.lat, stop.lon)
            for stop_id in sequence
            if (stop := feed.stops.get(stop_id))
            and stop.lat is not None
            and stop.lon is not None
        )
        if path:
            paths.append(path)
    return [p for p in paths if p]


def timepoint_stops(tables: Sequence[Timetable]) -> tuple[list[str], set[str]]:
    """Timepoint stop ids in table order, and the timepoints where enough
    trips start or end to count as an endpoint."""
    stops: dict[str, None] = {}
    starts: dict[str, int] = {}
    ends: dict[str, int] = {}
    trips = 0
    for table in tables:
        rows = [(k, row.stop_id) for k, row in enumerate(table.rows) if row.timepoint]
        stops.update(dict.fromkeys(stop_id for _, stop_id in rows))
        for column in table.columns:
            served = [stop_id for k, stop_id in rows if column.cells[k] is not None]
            if served:
                trips += 1
                starts[served[0]] = starts.get(served[0], 0) + 1
                ends[served[-1]] = ends.get(served[-1], 0) + 1
    threshold = endpoint_threshold(trips)
    endpoints = {s for s in stops if max(starts.get(s, 0), ends.get(s, 0)) >= threshold}
    return list(stops), endpoints


def stop_point(feed: Feed, stop_id: str) -> Point | None:
    stop = feed.stops.get(stop_id)
    if stop is None or stop.lat is None or stop.lon is None:
        return None
    return round(stop.lon, DIGITS), round(stop.lat, DIGITS)


def stop_name(feed: Feed, stop_id: str) -> str:
    return " ".join(feed.stops[stop_id].name.split())


def hex_color(color: str | None) -> str | None:
    return f"#{color}" if color else None


def route_data(
    feed: Feed,
    tables: Sequence[Timetable],
    numbers: Mapping[str, int],
    colors: tuple[str, str] | None,
    vehicles: dict | None = None,
) -> dict:
    """A route map's data: lines, and stops as [lon, lat, number, name, end]
    in number order, end 1 where enough trips start or end."""
    _, ends = timepoint_stops(tables)
    paths = route_paths(feed, tables)
    stops = []
    for stop_id, num in sorted(numbers.items(), key=lambda item: item[1]):
        if point := stop_point(feed, stop_id):
            stops.append([*point, num, stop_name(feed, stop_id), int(stop_id in ends)])
    return {
        "color": hex_color(colors[0]) if colors else None,
        "text": hex_color(colors[1]) if colors else None,
        "lines": lines_of(paths, tolerance(paths, ROUTE_TOLERANCE, ROUTE_SHARE)),
        "stops": stops,
        "rt": vehicles,
    }


def system_data(
    feed: Feed, routes: Sequence[SystemRoute], vehicles: str | None = None
) -> dict:
    """The system map's data: its routes, lines shared by several routes once
    with the indexes of their routes, and stops served by two or more routes
    as [lon, lat, name, [route indexes]]."""
    paths = [route_paths(feed, route.tables) for route in routes]
    tol = tolerance(
        [path for each in paths for path in each], SYSTEM_TOLERANCE, SYSTEM_SHARE
    )
    lines: dict[tuple[Point, ...], list[int]] = {}
    served: dict[str, list[int]] = {}
    for i, route in enumerate(routes):
        for line in lines_of(paths[i], tol):
            key = line if line in lines or line[::-1] not in lines else line[::-1]
            owners = lines.setdefault(key, [])
            if i not in owners:
                owners.append(i)
        stops, _ = timepoint_stops(route.tables)
        for stop_id in stops:
            owners = served.setdefault(stop_id, [])
            if i not in owners:
                owners.append(i)
    return {
        "routes": [
            {
                "slug": r.slug,
                "badge": r.badge,
                "title": r.title,
                "color": hex_color(r.colors[0]) if r.colors else None,
                "text": hex_color(r.colors[1]) if r.colors else None,
                "mode": r.mode,
                "ids": list(r.route_ids),
            }
            for r in routes
        ],
        "lines": [{"r": owners, "c": line} for line, owners in lines.items()],
        "stops": [
            [*point, stop_name(feed, stop_id), owners]
            for stop_id, owners in served.items()
            if len(owners) > 1 and (point := stop_point(feed, stop_id))
        ],
        "rt": vehicles,
    }

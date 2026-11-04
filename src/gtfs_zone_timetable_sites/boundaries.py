"""Country and region (ADM1) boundaries bundled in boundaries/, written by
scripts/boundaries.py, and the region a point is in."""

import json
import math
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from itertools import pairwise

Ring = list[list[float]]

DATA = files("gtfs_zone_timetable_sites") / "boundaries"
# How far outside a region's simplified outline a point still counts as in it,
# in degrees of latitude (about 30 km).
NEAR = 0.3


@dataclass(frozen=True, slots=True)
class Region:
    id: str
    name: str
    # Each polygon's rings, outer first; lon, lat pairs.
    polygons: tuple[tuple[Ring, ...], ...]
    # min lon, min lat, max lon, max lat
    bbox: tuple[float, float, float, float]


@cache
def world() -> dict:
    """Natural Earth countries as GeoJSON, each with `key` (lowercase alpha-2)
    and `name`."""
    return json.loads((DATA / "world.json").read_text())


@cache
def country(code: str) -> dict | None:
    """A country's ADM1 regions as GeoJSON, each with `id` and `name`, plus the
    data's `license` and `source`; None for a country without them."""
    path = DATA / f"{code.lower()}.json"
    return json.loads(path.read_text()) if path.is_file() else None


def polygons(geometry: dict) -> tuple[tuple[Ring, ...], ...]:
    if geometry["type"] == "Polygon":
        return (tuple(geometry["coordinates"]),)
    return tuple(tuple(p) for p in geometry["coordinates"])


@cache
def regions(code: str) -> tuple[Region, ...]:
    doc = country(code)
    found = []
    for feature in doc["features"] if doc else ():
        rings = polygons(feature["geometry"])
        xs = [x for poly in rings for x, _ in poly[0]]
        ys = [y for poly in rings for _, y in poly[0]]
        found.append(
            Region(
                id=feature["properties"]["id"],
                name=feature["properties"]["name"],
                polygons=rings,
                bbox=(min(xs), min(ys), max(xs), max(ys)),
            )
        )
    return tuple(found)


def inside(lon: float, lat: float, rings: tuple[Ring, ...]) -> bool:
    """Whether the point is inside a polygon with holes, by even-odd ray casting
    over all its rings."""
    hit = False
    for ring in rings:
        for (x0, y0), (x1, y1) in pairwise(ring):
            if (y0 > lat) != (y1 > lat) and lon < x0 + (lat - y0) * (x1 - x0) / (
                y1 - y0
            ):
                hit = not hit
    return hit


def distance(lon: float, lat: float, rings: tuple[Ring, ...]) -> float:
    """Degrees of latitude from the point to the nearest edge of the rings,
    with longitude scaled to match at the point's latitude."""
    scale = math.cos(math.radians(lat))
    best = math.inf
    for ring in rings:
        for (x0, y0), (x1, y1) in pairwise(ring):
            ax, ay = (x0 - lon) * scale, y0 - lat
            dx, dy = (x1 - x0) * scale, y1 - y0
            length = dx * dx + dy * dy
            t = max(0.0, min(1.0, -(ax * dx + ay * dy) / length)) if length else 0.0
            best = min(best, math.hypot(ax + t * dx, ay + t * dy))
    return best


def region_of(code: str, lat: float, lon: float) -> Region | None:
    """The ADM1 region of country `code` holding the point, else the nearest
    one within NEAR degrees, as simplified coasts leave out coastal cities."""
    near = []
    for region in regions(code):
        x0, y0, x1, y1 = region.bbox
        if not (x0 - NEAR <= lon <= x1 + NEAR and y0 - NEAR <= lat <= y1 + NEAR):
            continue
        if any(inside(lon, lat, rings) for rings in region.polygons):
            return region
        near.append(
            (min(distance(lon, lat, rings) for rings in region.polygons), region)
        )
    gap, region = min(near, default=(math.inf, None), key=lambda item: item[0])
    return region if gap <= NEAR else None

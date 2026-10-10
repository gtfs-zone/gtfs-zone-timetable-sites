"""Route and system maps as inline SVG: Web Mercator, simplified, with an
optional raster basemap laid under the SVG as lazy-loaded tile images."""

import math
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import pairwise

from markupsafe import Markup, escape

from gtfs_zone_timetable_sites import i18n
from gtfs_zone_timetable_sites.config import Basemap, TileStyle
from gtfs_zone_timetable_sites.gtfs.reader import Feed
from gtfs_zone_timetable_sites.maps.split import split_routes
from gtfs_zone_timetable_sites.pages import contrast
from gtfs_zone_timetable_sites.strip import endpoint_threshold
from gtfs_zone_timetable_sites.timetable import Timetable

# viewBox width; the height follows the map's shape within these ratios.
WIDTH = 600
MIN_RATIO = 0.5
MAX_RATIO = 1.2
PAD = 16
# Douglas-Peucker tolerance in viewBox units.
TOLERANCE = 1.0
FONT_SIZE = 20
# Average glyph width of the medium-weight system font stack as a share of
# the font size.
GLYPH = 0.58
DOT = 5
# Side of the grid cells used to count how much line a label would cover.
CELL = 6
MAJOR_DOT = 7
# Radius of the invisible circle that takes hovers and taps on a dot.
HIT = 14
# Route colors closer than this to the page background are drawn in ink.
MIN_LINE_CONTRAST = 1.6
LIGHT_BG = "FFFFFF"
DARK_BG = "141414"
# CSS width the map is laid out at on desktop, and the tile size aimed for there.
DISPLAY_WIDTH = 512
TILE_CSS = 256
MAX_ZOOM = 18


@dataclass(frozen=True, slots=True)
class TileSource:
    """A Stadia raster style: its tile URL and the credits it needs."""

    style: str
    credits: tuple[str, ...]
    ext: str = "png"
    retina: bool = True
    max_zoom: int = MAX_ZOOM

    def url(self, z: int, x: int, y: int) -> str:
        scale = "@2x" if self.retina else ""
        return (
            f"https://tiles.stadiamaps.com/tiles/{self.style}"
            f"/{z}/{x}/{y}{scale}.{self.ext}"
        )


STADIA = ("stadia", "openmaptiles", "osm")
STAMEN = ("stadia", "stamen", "openmaptiles", "osm")
TILE_SOURCES: dict[TileStyle, TileSource] = {
    "stadia-alidade-smooth": TileSource("alidade_smooth", STADIA),
    "stadia-alidade-smooth-dark": TileSource("alidade_smooth_dark", STADIA),
    "stadia-alidade-bright": TileSource("alidade_bright", STADIA),
    "stadia-alidade-satellite": TileSource(
        "alidade_satellite", ("satellite", *STADIA), ext="jpg"
    ),
    "stadia-outdoors": TileSource("outdoors", STADIA),
    "stadia-osm-bright": TileSource("osm_bright", STADIA),
    "stadia-toner": TileSource("stamen_toner", STAMEN),
    "stadia-toner-lite": TileSource("stamen_toner_lite", STAMEN),
    "stadia-toner-dark": TileSource("stamen_toner_dark", STAMEN),
    "stadia-toner-blacklite": TileSource("stamen_toner_blacklite", STAMEN),
    "stadia-toner-background": TileSource("stamen_toner_background", STAMEN),
    "stadia-terrain": TileSource("stamen_terrain", STAMEN),
    "stadia-terrain-background": TileSource("stamen_terrain_background", STAMEN),
    "stadia-watercolor": TileSource(
        "stamen_watercolor",
        ("stadia", "stamen", "osm"),
        ext="jpg",
        retina=False,
        max_zoom=16,
    ),
}
CREDITS = {
    "satellite": "&copy; CNES, Distribution Airbus DS, &copy; Airbus DS,"
    " &copy; PlanetObserver (Contains Copernicus Data)",
    "stadia": '&copy; <a href="https://stadiamaps.com/" target="_blank"'
    ' rel="noopener">Stadia Maps</a>',
    "stamen": '&copy; <a href="https://stamen.com/" target="_blank"'
    ' rel="noopener">Stamen Design</a>',
    "openmaptiles": '&copy; <a href="https://openmaptiles.org/" target="_blank"'
    ' rel="noopener">OpenMapTiles</a>',
    "osm": '&copy; <a href="https://www.openstreetmap.org/copyright"'
    ' target="_blank" rel="noopener">OpenStreetMap</a>',
}

type Point = tuple[float, float]
type Box = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class Line:
    """One route: its paths as (lat, lon) points, linked to href if set."""

    title: str
    color: str | None
    paths: tuple[tuple[Point, ...], ...]
    href: str | None = None


@dataclass(frozen=True, slots=True)
class Mark:
    """A stop dot with a caption shown on hover; major marks get a bigger dot,
    end marks a label, routes are listed in the caption, and hrefs are the
    lines of those routes, highlighted on hover."""

    lat: float
    lon: float
    label: str
    major: bool = False
    rank: int = 0
    end: bool = False
    routes: tuple[str, ...] = ()
    hrefs: tuple[str, ...] = ()


def mercator(lat: float, lon: float) -> Point:
    """Web Mercator with y growing down, in radians of longitude."""
    lat = max(min(lat, 85.0), -85.0)
    y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    return math.radians(lon), -y


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


class Projection:
    """Fits a set of (lat, lon) points into a WIDTH-wide viewBox."""

    def __init__(self, points: Iterable[tuple[float, float]]) -> None:
        projected = [mercator(lat, lon) for lat, lon in points]
        xs = [x for x, _ in projected] or [0.0]
        ys = [y for _, y in projected] or [0.0]
        self.min_x, self.min_y = min(xs), min(ys)
        # A floor on the extent so a single stop or a tiny loop still gets a frame.
        span_x = max(max(xs) - self.min_x, 1e-4)
        span_y = max(max(ys) - self.min_y, 1e-4)
        inner = WIDTH - 2 * PAD
        ratio = min(max(span_y / span_x, MIN_RATIO), MAX_RATIO)
        self.height = round(inner * ratio + 2 * PAD)
        self.scale = min(inner / span_x, (self.height - 2 * PAD) / span_y)
        self.off_x = (WIDTH - span_x * self.scale) / 2
        self.off_y = (self.height - span_y * self.scale) / 2

    def __call__(self, lat: float, lon: float) -> Point:
        return self.to_viewbox(*mercator(lat, lon))

    def to_viewbox(self, x: float, y: float) -> Point:
        """A mercator point to viewBox units."""
        return (
            (x - self.min_x) * self.scale + self.off_x,
            (y - self.min_y) * self.scale + self.off_y,
        )

    def to_mercator(self, vx: float, vy: float) -> Point:
        return (
            (vx - self.off_x) / self.scale + self.min_x,
            (vy - self.off_y) / self.scale + self.min_y,
        )


@dataclass(frozen=True, slots=True)
class Tile:
    """A slippy-map tile and where it sits, as percentages of the map box."""

    z: int
    x: int
    y: int
    left: float
    top: float
    width: float
    height: float


def tiles(project: Projection, max_zoom: int = MAX_ZOOM) -> list[Tile]:
    """Tiles covering the viewBox, at the zoom whose tiles show about
    TILE_CSS pixels wide when the map is DISPLAY_WIDTH pixels wide."""
    world = 2 * math.pi
    target = world * project.scale * DISPLAY_WIDTH / (WIDTH * TILE_CSS)
    z = min(max(round(math.log2(target)), 0), max_zoom)
    count = 1 << z
    size = world / count

    def tile_index(m: float) -> int:
        return min(max(math.floor((m + math.pi) / size), 0), count - 1)

    left, top = project.to_mercator(0, 0)
    right, bottom = project.to_mercator(WIDTH, project.height)
    result = []
    for ty in range(tile_index(top), tile_index(bottom) + 1):
        for tx in range(tile_index(left), tile_index(right) + 1):
            x0, y0 = project.to_viewbox(tx * size - math.pi, ty * size - math.pi)
            side = size * project.scale
            result.append(
                Tile(
                    z,
                    tx,
                    ty,
                    100 * x0 / WIDTH,
                    100 * y0 / project.height,
                    100 * side / WIDTH,
                    100 * side / project.height,
                )
            )
    return result


def tile_sources(basemap: Basemap) -> tuple[TileSource, TileSource]:
    """The light and dark tile sources; a single style serves both."""
    if isinstance(basemap, str):
        return TILE_SOURCES[basemap], TILE_SOURCES[basemap]
    return TILE_SOURCES[basemap.light], TILE_SOURCES[basemap.dark]


def attribution(basemap: Basemap) -> str:
    """Credits needed by either tile source, each once."""
    keys = dict.fromkeys(key for s in tile_sources(basemap) for key in s.credits)
    return " ".join(CREDITS[key] for key in keys)


def tile_layer(project: Projection, basemap: Basemap) -> str:
    """Tile images, as <picture> with a dark source when the styles differ."""
    light, dark = tile_sources(basemap)
    parts = ['<div class="tiles" aria-hidden="true">']
    for t in tiles(project, min(light.max_zoom, dark.max_zoom)):
        style = (
            f"left:{t.left:.3f}%;top:{t.top:.3f}%;"
            f"width:{t.width:.3f}%;height:{t.height:.3f}%"
        )
        img = (
            f'<img src="{light.url(t.z, t.x, t.y)}" alt=""'
            f' loading="lazy" decoding="async" style="{style}">'
        )
        if dark != light:
            img = (
                f'<picture><source media="(prefers-color-scheme: dark)"'
                f' srcset="{dark.url(t.z, t.x, t.y)}">{img}</picture>'
            )
        parts.append(img)
    parts.append("</div>")
    return "".join(parts)


def number(value: int, first: bool) -> str:
    """A path number; negatives need no separating space."""
    return str(value) if first or value < 0 else f" {value}"


def rounded_path(points: Sequence[Point]) -> list[tuple[int, int]]:
    return [(round(x), round(y)) for x, y in simplify(points, TOLERANCE)]


def path_data(rounded: Sequence[tuple[int, int]]) -> str:
    """'M x y l dx dy ...' from integer points, dropping zero steps."""
    x0, y0 = rounded[0]
    parts = [f"M{x0}{number(y0, False)}l"]
    first = True
    px, py = x0, y0
    for x, y in rounded[1:]:
        if (x, y) == (px, py):
            continue
        parts.append(number(x - px, first) + number(y - py, False))
        first = False
        px, py = x, y
    if first:
        # A path of one point: draw a zero-length step so round caps show a dot.
        parts.append("0 0")
    return "".join(parts)


def line_class(color: str | None) -> str:
    """CSS classes swapping a route color for ink where it would vanish."""
    if color is None:
        return "ink"
    classes = []
    if contrast(color, LIGHT_BG) < MIN_LINE_CONTRAST:
        classes.append("pale")
    if contrast(color, DARK_BG) < MIN_LINE_CONTRAST:
        classes.append("deep")
    return " ".join(classes)


def line_cells(paths: Iterable[Sequence[tuple[int, int]]]) -> set[tuple[int, int]]:
    """Grid cells the drawn lines pass through, sampled every half cell."""
    cells = set()
    for path in paths:
        for (ax, ay), (bx, by) in pairwise(path):
            steps = max(1, math.ceil(math.hypot(bx - ax, by - ay) / (CELL / 2)))
            for i in range(steps + 1):
                t = i / steps
                cells.add(
                    (
                        int((ax + (bx - ax) * t) // CELL),
                        int((ay + (by - ay) * t) // CELL),
                    )
                )
        if len(path) == 1:
            cells.add((path[0][0] // CELL, path[0][1] // CELL))
    return cells


def covered(box: Box, cells: set[tuple[int, int]]) -> int:
    """How many line cells fall under a box."""
    return sum(
        (cx, cy) in cells
        for cx in range(int(box[0] // CELL), int(box[2] // CELL) + 1)
        for cy in range(int(box[1] // CELL), int(box[3] // CELL) + 1)
    )


def overlaps(a: Box, b: Box) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def label_box(x: float, y: float, text: str, side: str, gap: float) -> Box:
    """Bounding box of a label beside a dot at (x, y)."""
    width = len(text) * FONT_SIZE * GLYPH
    half = FONT_SIZE / 2
    if side == "right":
        return (x + gap, y - half, x + gap + width, y + half)
    if side == "left":
        return (x - gap - width, y - half, x - gap, y + half)
    if side == "above":
        return (x - width / 2, y - gap - FONT_SIZE, x + width / 2, y - gap)
    return (x - width / 2, y + gap, x + width / 2, y + gap + FONT_SIZE)


ANCHORS = {"right": "start", "left": "end", "above": "middle", "below": "middle"}


def place_labels(
    dots: list[tuple[float, float, Mark]], height: int, cells: set[tuple[int, int]]
) -> list[tuple[Box, str, str]]:
    """Greedy placement, most important first, on the side covering the least
    line and fewest other dots; labels that fit nowhere are dropped."""
    circles: list[Box] = [
        (x - r, y - r, x + r, y + r)
        for x, y, mark in dots
        for r in [MAJOR_DOT if mark.major else DOT]
    ]
    taken: list[Box] = []
    placed = []
    for x, y, mark in sorted(dots, key=lambda d: (not d[2].major, -d[2].rank)):
        gap = (MAJOR_DOT if mark.major else DOT) + 4
        options = []
        for order, side in enumerate(ANCHORS):
            box = label_box(x, y, mark.label, side, gap)
            inside = (
                box[0] >= 0 and box[1] >= 0 and box[2] <= WIDTH and box[3] <= height
            )
            if inside and not any(overlaps(box, other) for other in taken):
                hidden = sum(overlaps(box, circle) for circle in circles)
                options.append((hidden, covered(box, cells), order, box, side))
        if options:
            *_, box, side = min(options)
            taken.append(box)
            placed.append((box, side, mark.label))
    return placed


def render_map(
    title: str,
    lines: Sequence[Line],
    marks: Sequence[Mark],
    basemap: Basemap = "none",
) -> Markup:
    """An inline <svg> of the lines with their marked stops in a `.map` box,
    over tiles when there is a basemap, empty when no geometry. Stops and
    linked lines carry `data-cap`, which map.html's script shows in `.cap`;
    stops also carry `data-routes`, the hrefs of the lines it highlights and,
    when there is only one, opens on click."""
    lines = [line for line in lines if line.paths]
    if not lines:
        return Markup("")
    points = [p for line in lines for path in line.paths for p in path]
    project = Projection(points + [(m.lat, m.lon) for m in marks])

    tiled = basemap != "none"
    parts = [
        f'<div class="map{" basemap" if tiled else ""}"'
        f' style="aspect-ratio:{WIDTH}/{project.height}">'
    ]
    if tiled:
        parts.append(tile_layer(project, basemap))
    parts.append(
        f'<svg class="over" viewBox="0 0 {WIDTH} {project.height}" role="img"'
        f' aria-label="{escape(title)}">'
    )
    drawn = [
        [rounded_path([project(lat, lon) for lat, lon in path]) for path in line.paths]
        for line in lines
    ]
    # Drawn last on top, so the first route listed ends up most visible.
    for line, paths in reversed(list(zip(lines, drawn, strict=True))):
        data = " ".join(path_data(path) for path in paths)
        stroke = f' stroke="#{line.color}"' if line.color else ""
        css = line_class(line.color)
        title = escape(line.title)
        tag, attrs = (
            (
                "a",
                f' href="{escape(line.href)}" aria-label="{title}" data-cap="{title}"',
            )
            if line.href
            else ("g", "")
        )
        parts.append(
            f'<{tag}{attrs}><path class="case" d="{data}"/>'
            f'<path class="line{" " + css if css else ""}"{stroke} d="{data}"/></{tag}>'
        )

    # One dot per name and per spot, keeping the most important mark.
    dots: list[tuple[float, float, Mark]] = []
    seen: set[str] = set()
    for mark in sorted(marks, key=lambda m: (not m.end, not m.major, -m.rank)):
        x, y = project(mark.lat, mark.lon)
        near = any(math.hypot(x - dx, y - dy) < 2 * DOT for dx, dy, _ in dots)
        if mark.label in seen or near:
            continue
        seen.add(mark.label)
        dots.append((x, y, mark))
    for x, y, mark in dots:
        r = MAJOR_DOT if mark.major else DOT
        cx, cy = round(x), round(y)
        text = " - ".join(
            [mark.label, ", ".join(mark.routes)] if mark.routes else [mark.label]
        )
        hrefs = f' data-routes="{escape(" ".join(mark.hrefs))}"' if mark.hrefs else ""
        parts.append(
            f'<g class="pin" data-cap="{escape(text)}"{hrefs}>'
            f'<circle class="hit" cx="{cx}" cy="{cy}" r="{HIT}"/>'
            f'<circle class="stop" cx="{cx}" cy="{cy}" r="{r}"/></g>'
        )
    placed = place_labels(
        [dot for dot in dots if dot[2].end],
        project.height,
        line_cells(p for paths in drawn for p in paths),
    )
    for box, side, label in placed:
        x = {"right": box[0], "left": box[2]}.get(side, (box[0] + box[2]) / 2)
        # Baseline sits a little under the box middle for cap-height text.
        y = box[3] - FONT_SIZE * 0.22
        anchor = ANCHORS[side]
        attr = "" if anchor == "start" else f' text-anchor="{anchor}"'
        parts.append(
            f'<text x="{round(x)}" y="{round(y)}"{attr}>{escape(label)}</text>'
        )
    parts.append('</svg><p class="cap" hidden></p></div>')
    if tiled:
        parts.append(f'<p class="attribution">{attribution(basemap)}</p>')
    return Markup("".join(parts))


def trip_ids(tables: Sequence[Timetable]) -> list[str]:
    ids = dict.fromkeys(column.trip_id for table in tables for column in table.columns)
    return list(ids)


def route_line(
    feed: Feed,
    title: str,
    color: str | None,
    ids: Sequence[str],
    href: str | None = None,
) -> Line:
    """The route's distinct shapes, or its stop sequences for trips without one."""
    shape_ids: dict[str, None] = {}
    sequences: dict[tuple[str, ...], None] = {}
    for trip_id in ids:
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
    return Line(title=title, color=color, paths=tuple(p for p in paths if p), href=href)


def timepoint_stops(tables: Sequence[Timetable]) -> tuple[list[str], set[str]]:
    """Timepoint stop ids in table order, and the timepoints where enough
    trips start or end to count as an endpoint."""
    stops: dict[str, None] = {}
    starts: Counter[str] = Counter()
    ends: Counter[str] = Counter()
    trips = 0
    for table in tables:
        rows = [(k, row.stop_id) for k, row in enumerate(table.rows) if row.timepoint]
        stops.update(dict.fromkeys(stop_id for _, stop_id in rows))
        for column in table.columns:
            served = [stop_id for k, stop_id in rows if column.cells[k] is not None]
            if served:
                trips += 1
                starts[served[0]] += 1
                ends[served[-1]] += 1
    threshold = endpoint_threshold(trips)
    endpoints = {s for s in stops if max(starts[s], ends[s]) >= threshold}
    return list(stops), endpoints


def mark(
    feed: Feed,
    stop_id: str,
    major: bool,
    rank: int = 0,
    end: bool = False,
    routes: tuple[str, ...] = (),
    hrefs: tuple[str, ...] = (),
) -> Mark | None:
    stop = feed.stops.get(stop_id)
    if stop is None or stop.lat is None or stop.lon is None:
        return None
    name = " ".join(stop.name.split())
    return Mark(stop.lat, stop.lon, name, major, rank, end, routes, hrefs)


def route_marks(feed: Feed, tables: Sequence[Timetable]) -> list[Mark]:
    stops, ends = timepoint_stops(tables)
    return [m for s in stops if (m := mark(feed, s, s in ends, end=s in ends))]


def route_map(
    feed: Feed,
    title: str,
    color: str | None,
    tables: Sequence[Timetable],
    basemap: Basemap = "none",
    locale: i18n.Locale = "en",
) -> Markup:
    """One route with its ends labeled and its other timepoints captioned."""
    line = route_line(feed, title, color, trip_ids(tables))
    return render_map(
        i18n.t(locale, "map.title", title=title),
        [line],
        route_marks(feed, tables),
        basemap,
    )


SystemRoute = tuple[str, str, str | None, list[Timetable], str | None]


def system_marks(feed: Feed, routes: Sequence[SystemRoute]) -> list[Mark]:
    """Every route's timepoints as marks listing the badges and hrefs of the
    routes serving them; stops on two or more routes or at a route's end are
    major, ranked by routes served."""
    served: dict[str, list[tuple[str, str | None]]] = {}
    ends: set[str] = set()
    for _, badge, _, tables, href in routes:
        stops, route_ends = timepoint_stops(tables)
        for stop_id in stops:
            served.setdefault(stop_id, []).append((badge, href))
        ends |= route_ends
    return [
        m
        for stop_id, routes in served.items()
        if (
            m := mark(
                feed,
                stop_id,
                len(routes) > 1 or stop_id in ends,
                len(routes),
                stop_id in ends,
                tuple(dict.fromkeys(badge for badge, _ in routes)),
                tuple(dict.fromkeys(href for _, href in routes if href)),
            )
        )
    ]


def system_line(feed: Feed, route: SystemRoute) -> Line:
    name, _, color, tables, href = route
    return route_line(feed, name, color, trip_ids(tables), href)


def system_maps(
    feed: Feed,
    title: str,
    routes: Sequence[SystemRoute],
    basemap: Basemap = "none",
    locale: i18n.Locale = "en",
) -> list[tuple[list[int], Markup]]:
    """The routes split into local and long-route maps, each with its route
    indexes and its map of every route with ends labeled and timepoints
    captioned; maps after the first are titled with their badges."""
    lines = [system_line(feed, r) for r in routes]
    drawn = [i for i, line in enumerate(lines) if line.paths]
    if not drawn:
        return []
    maps = []
    for n, (_, ix) in enumerate(split_routes([lines[i].paths for i in drawn])):
        ix = [drawn[i] for i in ix]
        badges = ", ".join(routes[i][1] for i in ix)
        svg = render_map(
            i18n.t(locale, "map.title", title=f"{title}{f': {badges}' if n else ''}"),
            [lines[i] for i in ix],
            system_marks(feed, [routes[i] for i in ix]),
            basemap,
        )
        maps.append((ix, svg))
    return maps

"""Timetables and routes to plain data the templates render."""

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, time
from itertools import groupby
from zoneinfo import ZoneInfo

from gtfs_zone_feed_fetch.places import Place

from gtfs_zone_timetable_sites.colors import route_color
from gtfs_zone_timetable_sites.config import TimeFormat
from gtfs_zone_timetable_sites.fares import (
    Fare,
    FlatFare,
    LegGroup,
    RouteFares,
    SiteFares,
    TransferRule,
    Transfers,
    ZoneFares,
)
from gtfs_zone_timetable_sites.gtfs.reader import Feed, Route, Timeframe
from gtfs_zone_timetable_sites.gtfs.service import (
    DayType,
    dates_label,
    day_name,
    day_order,
    first_service_date,
    last_service_date,
    missing_label,
    trip_note,
)
from gtfs_zone_timetable_sites.i18n import (
    Locale,
    capitalize,
    join_and,
    long_date,
    price,
    t,
)
from gtfs_zone_timetable_sites.strip import (
    RailRow,
    endpoint_threshold,
    gutter_width,
    is_endpoint,
    lane_x,
    route_graph,
    row_paths,
    stop_stats,
)
from gtfs_zone_timetable_sites.timetable import (
    Cell,
    Column,
    Timetable,
    headsign,
    mode_name,
)

DAY_SECONDS = 24 * 3600
# Minimum WCAG contrast for badge text on the route color.
MIN_CONTRAST = 4.5
# Stop numbers sit in white dots; a line color fainter than this against
# white gives way to DOT_INK
DOT_CONTRAST = 3
DOT_INK = "1F2937"
# Arrival and departure are both shown when they differ by more than this.
DWELL_SECONDS = 60


def tidy(text: str) -> str:
    """Collapse runs of whitespace and trim."""
    return " ".join(text.split())


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def luminance(color: str) -> float:
    """WCAG relative luminance of an RRGGBB color."""
    channels = []
    for i in (0, 2, 4):
        c = int(color[i : i + 2], 16) / 255
        channels.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def text_color(background: str, text: str | None = None) -> str:
    """`text` when it contrasts enough with `background`, else black or white,
    whichever contrasts more."""
    if text is None or contrast(background, text) < MIN_CONTRAST:
        text = max(("000000", "FFFFFF"), key=lambda t: contrast(background, t))
    return text


def dot_text(line: str) -> str:
    """Stop number color in a white dot: the line color, or DOT_INK when the
    line is too faint against white."""
    return line if contrast(line, "FFFFFF") >= DOT_CONTRAST else DOT_INK


def badge_colors(route: Route) -> tuple[str, str]:
    """(background, text) for a route badge: the feed's color or a hashed one,
    keeping the feed's text color only when it contrasts enough."""
    color = route_color(route.route_id, route.color)
    return color, text_color(color, route.text_color)


# Order of the mode sections on a site's index.
MODE_ORDER = (
    "subway",
    "light rail",
    "monorail",
    "train",
    "coach",
    "trolleybus",
    "bus",
    "ferry",
    "cable car",
    "gondola",
    "funicular",
    "air",
    "taxi",
    "misc",
    "transit",
)


def mode_label(mode: str, locale: Locale = "en") -> str:
    """A mode name from MODE_ORDER in the page's language."""
    return t(locale, f"mode.{mode}")


def modes_text(modes: list[str], locale: Locale = "en") -> str:
    """'bus', 'bus and train', 'bus, ferry and train' from mode names."""
    names = [mode_label(mode, locale) for mode in sorted(set(modes)) or ["transit"]]
    return join_and(names, locale)


def modes_label(modes: list[str], locale: Locale = "en") -> str:
    """'Bus', 'Bus and train', 'Bus, ferry and train' from mode names."""
    return capitalize(modes_text(modes, locale))


def breadcrumbs(items: list[tuple[str, str]]) -> dict:
    """schema.org BreadcrumbList from (name, url) pairs, outermost first."""
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i, "name": name, "item": url}
            for i, (name, url) in enumerate(items, 1)
        ],
    }


def place_jsonld(place: Place | None, label: str, url: str) -> list[dict]:
    """A schema.org WebPage whose spatialCoverage is the feed's place, or
    nothing without one."""
    if place is None or not label:
        return []
    address = {"@type": "PostalAddress", "addressCountry": place.country_code}
    if place.region:
        address["addressRegion"] = place.region
    return [
        {
            "@context": "https://schema.org",
            "@type": "WebPage",
            "url": url,
            "spatialCoverage": {
                "@type": "Place",
                "name": label,
                "address": address,
                "geo": {
                    "@type": "GeoCoordinates",
                    "latitude": place.lat,
                    "longitude": place.lon,
                },
            },
        }
    ]


@dataclass(frozen=True, slots=True)
class TimeText:
    """A time as shown in a cell: clock text plus how to style it."""

    text: str
    pm: bool
    # Days past the service day: +1 after midnight, -1 the evening before.
    days: int


def clock(seconds: int, time_format: TimeFormat) -> TimeText:
    """Clock time without a/p; PM is marked for bolding in 12h tables."""
    minutes = seconds // 60
    days, rest = divmod(minutes, 24 * 60)
    hours, minute = divmod(rest, 60)
    if time_format == "24h":
        return TimeText(f"{hours:02d}:{minute:02d}", False, days)
    return TimeText(f"{(hours - 1) % 12 + 1}:{minute:02d}", hours >= 12, days)


def clock_label(seconds: int, time_format: TimeFormat) -> str:
    """Clock time with a/p for prose, e.g. '7:30 PM'."""
    shown = clock(seconds, time_format)
    label = shown.text
    if time_format == "12h":
        label += " PM" if shown.pm else " AM"
    if shown.days:
        label += f" {shown.days:+d}"
    return label


@dataclass(frozen=True, slots=True)
class CellView:
    time: TimeText | None
    untimed: bool
    mark: str
    arrival: TimeText | None = None


@dataclass(frozen=True, slots=True)
class ColumnHead:
    number: str
    headsign: str
    every: str
    shaded: bool
    note: str = ""
    bikes: bool = False
    wheelchair: bool = False


@dataclass(frozen=True, slots=True)
class HeadsignSpan:
    text: str
    span: int
    shaded: bool


@dataclass(frozen=True, slots=True)
class RowView:
    name: str
    zone: str
    cells: tuple[CellView, ...]
    google: str = ""
    paths: tuple[str, ...] = ()
    rail: str = ""
    dot_x: int = 0
    solid: bool = False
    wheelchair: bool = False
    num: int = 0
    # Times here are approximate (timepoint=0), dimmed.
    approx: bool = False
    code: str = ""
    platform: str = ""
    # Fare zone badge text, shown when the route's fares are by zone.
    fare_zone: str = ""
    # The name's reading (kana) and its language code.
    reading: str = ""
    reading_lang: str = ""


@dataclass(frozen=True, slots=True)
class Note:
    """A lettered column note: text, and a link when it names another route."""

    text: str
    href: str = ""


@dataclass(frozen=True, slots=True)
class Key:
    """A legend entry, led by an icon when `icon` is set, or by a column note
    letter when `letter` is; `href` links the text."""

    icon: str
    text: str
    letter: str = ""
    href: str = ""


@dataclass(frozen=True, slots=True)
class TableView:
    title: str
    heads: tuple[ColumnHead, ...]
    headsigns: tuple[HeadsignSpan, ...]
    show_numbers: bool
    show_headsigns: bool
    show_notes: bool
    rows: tuple[RowView, ...]
    legend: tuple[Key, ...]
    anchor: str = ""
    trips: int = 0
    gutter: int = 0
    # For live times: each column's trip id ("" for frequency columns), each
    # row's stop id, each column's scheduled times in the route's zone from
    # the start of its service day (see `minute_steps`), and the minutes each
    # row's zone adds to them.
    trip_ids: tuple[str, ...] = ()
    stop_ids: tuple[str, ...] = ()
    sched: tuple[tuple[int | None, ...], ...] = ()
    shifts: tuple[int, ...] = ()

    @property
    def runs(self) -> bool:
        """Some column is a trip whose next stop map.js can mark."""
        return any(self.trip_ids)

    @property
    def live(self) -> dict[str, object]:
        """The live-times data map.js reads for this table, in page order."""
        data: dict[str, object] = {
            "trips": self.trip_ids,
            "stops": self.stop_ids,
            "sched": self.sched,
        }
        if any(self.shifts):
            data["shifts"] = self.shifts
        return data

    @property
    def show_amenities(self) -> bool:
        return any(head.bikes or head.wheelchair for head in self.heads)


@dataclass(frozen=True, slots=True)
class DayView:
    anchor: str
    name: str
    notes: tuple[str, ...]
    tables: tuple[TableView, ...]
    runs: int = 0
    first: str = ""
    last: str = ""
    # The dates the day's tables are valid for.
    valid: str = ""

    @property
    def trips(self) -> int:
        return sum(table.trips for table in self.tables)

    @property
    def icons(self) -> bool:
        return any(key.icon for table in self.tables for key in table.legend)


@dataclass(frozen=True, slots=True)
class RouteView:
    route_id: str
    slug: str
    badge: str
    name: str
    colors: tuple[str, str] | None
    days: tuple[str, ...]
    url: str
    desc: str
    mode: str = "transit"
    endpoints: tuple[str, str] | None = None
    # "expired" (ended on state_date), "upcoming" (starts on it) or empty.
    state: str = ""
    state_date: date | None = None
    locale: Locale = "en"

    @property
    def state_label(self) -> str:
        """'Ended Dec 18, 2020', 'Starts Jan 5, 2027' or empty."""
        if self.state_date is None:
            return ""
        key = {"expired": "index.ended", "upcoming": "index.starts"}.get(self.state)
        if key is None:
            return ""
        return t(self.locale, key, date=long_date(self.state_date, self.locale))

    @property
    def mode_label(self) -> str:
        return mode_label(self.mode, self.locale)

    @property
    def dot_text(self) -> str:
        return dot_text(self.colors[0]) if self.colors else DOT_INK

    @property
    def title(self) -> str:
        return f"{self.badge} {self.name}" if self.name else self.badge

    @property
    def days_label(self) -> str:
        return join_and(self.days, self.locale)


@dataclass(frozen=True, slots=True)
class ModeGroup:
    """One mode's routes on a site's index."""

    mode: str
    routes: tuple[RouteView, ...]
    locale: Locale = "en"

    @property
    def heading(self) -> str:
        return capitalize(mode_label(self.mode, self.locale))

    @property
    def anchor(self) -> str:
        return slugify(self.mode)


def mode_groups(routes: Sequence[RouteView], locale: Locale = "en") -> list[ModeGroup]:
    """Routes by mode in MODE_ORDER, each keeping its order within the mode."""
    by_mode: dict[str, list[RouteView]] = {}
    for route in routes:
        by_mode.setdefault(route.mode, []).append(route)
    return [
        ModeGroup(mode, tuple(by_mode[mode]), locale)
        for mode in MODE_ORDER
        if mode in by_mode
    ]


def cell_view(cell: Cell | None, time_format: TimeFormat) -> CellView:
    if cell is None:
        return CellView(None, False, "")
    mark = ""
    if not cell.pickup and cell.drop_off:
        mark = "d"
    elif cell.pickup and not cell.drop_off:
        mark = "p"
    if cell.request:
        mark += "f"
    if cell.time is None:
        return CellView(None, True, mark)
    arrival = None
    if (
        cell.arrival is not None
        and cell.departure is not None
        and cell.departure - cell.arrival > DWELL_SECONDS
    ):
        arrival = clock(cell.arrival, time_format)
    return CellView(clock(cell.time, time_format), False, mark, arrival)


def column_head(
    column: Column, time_format: TimeFormat, shaded: bool, locale: Locale = "en"
) -> ColumnHead:
    every = ""
    if column.headway is not None:
        minutes = column.headway.headway_secs // 60
        until = clock_label(column.headway.end, time_format)
        every = t(locale, "table.every", minutes=minutes, until=until)
    return ColumnHead(
        number=tidy(column.short_name),
        headsign=tidy(column.headsign),
        every=every,
        shaded=shaded,
    )


def zone_label(zone: str | None, day: date) -> str:
    """The zone's abbreviation on `day`, e.g. 'CDT', else its name."""
    if not zone:
        return ""
    name = datetime.combine(day, time(12), ZoneInfo(zone)).tzname()
    return name if name and not name.startswith(("+", "-")) else zone


def hour_shading(columns: tuple[Column, ...]) -> list[bool]:
    """Alternate shading each time the hour of the first time changes."""
    shading = []
    shaded, hour = True, None
    for column in columns:
        first = column.first_time()
        this_hour = None if first is None else first // 3600
        if this_hour != hour:
            shaded, hour = not shaded, this_hour
        shading.append(shaded)
    return shading


# Headsigns naming a service or a loop rather than a place to head to.
NOT_A_PLACE = re.compile(
    r"\b(loop|clockwise|counterclockwise|inbound|outbound|train|special)\b", re.I
)


def headsign_spans(heads: tuple[ColumnHead, ...]) -> tuple[HeadsignSpan, ...]:
    """Adjacent columns with the same headsign share one header cell."""
    spans = []
    for text, group in groupby(heads, key=lambda head: head.headsign):
        run = list(group)
        spans.append(HeadsignSpan(text, len(run), all(head.shaded for head in run)))
    return tuple(spans)


def table_title(headsigns: list[str], locale: Locale = "en") -> str:
    """'To A / B', without 'To' when a headsign is not a destination."""
    text = " / ".join(headsigns)
    if any(NOT_A_PLACE.search(h) or not h[:1].isalnum() for h in headsigns):
        return text
    return t(locale, "table.to", places=text)


def google_link(lat: float | None, lon: float | None) -> str:
    """Google Maps URL for a point, empty without coordinates."""
    if lat is None or lon is None:
        return ""
    return f"https://www.google.com/maps/search/?api=1&query={lat:.5f},{lon:.5f}"


def stop_link(feed: Feed, stop_id: str) -> str:
    stop = feed.stops.get(stop_id)
    return google_link(stop.lat, stop.lon) if stop else ""


def straight(row: RailRow) -> bool:
    """Whether the row is only lane 0 running straight, drawn by CSS."""
    return (
        row.lane == 0
        and not row.through
        and row.merges in ((), (0,))
        and row.branches in ((), (0,))
    )


def rail_class(row: RailRow) -> str:
    """CSS halves of a straight row's rail: 'u' above the dot, 'd' below."""
    if not straight(row):
        return ""
    return " ".join(half for half, on in (("u", row.merges), ("d", row.branches)) if on)


# name -> icon; its legend text is `amenity.<name>`, its feed notes when all
# are 1 or all 2 `amenity.<name>_all` and `amenity.<name>_none`.
AMENITIES = {"bikes": "bike", "trips": "wheelchair", "stops": "wheelchair"}


def feed_amenities(
    tables: list[Timetable], locale: Locale = "en"
) -> tuple[tuple[str, ...], frozenset[str]]:
    """(feed notes, amenities to mark with icons) over every table: a note
    when all trips or stops are 1 or all 2, icons when they differ."""
    values = {
        "bikes": {column.bikes for table in tables for column in table.columns},
        "trips": {column.wheelchair for table in tables for column in table.columns},
        "stops": {row.wheelchair for table in tables for row in table.rows},
    }
    notes = []
    marked = set()
    for name, found in values.items():
        if found == {1}:
            notes.append(t(locale, f"amenity.{name}_all"))
        elif found == {2}:
            notes.append(t(locale, f"amenity.{name}_none"))
        elif 1 in found:
            marked.add(name)
    return tuple(notes), frozenset(marked)


def minute_steps(times: Sequence[int | None]) -> tuple[int | None, ...]:
    """Times in seconds as minutes: the first time, then minutes since the
    previous time, None where there is none. Trips of a pattern share their
    steps, which keeps the page small once compressed."""
    steps: list[int | None] = []
    last = None
    for seconds in times:
        if seconds is None:
            steps.append(None)
            continue
        minute = seconds // 60
        steps.append(minute if last is None else minute - last)
        last = minute
    return tuple(steps)


def note_letter(index: int) -> str:
    """A to Z, then AA, AB and on, like spreadsheet columns."""
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(ord("A") + rest) + letters
    return letters


def table_view(
    feed: Feed,
    table: Timetable,
    time_format: TimeFormat,
    anchor: str = "",
    marked: frozenset[str] = frozenset(),
    locale: Locale = "en",
    numbers: Mapping[str, int] | None = None,
    routes: Mapping[str, "RouteView"] | None = None,
    fare_zones: Mapping[str, str] | None = None,
) -> TableView:
    # Per column, its date note and the trip on another route it continues
    # as, each lettered by its first column.
    notes = [
        [
            note
            for note in (
                Note(trip_note(table.day_type, column.trip_id, locale)),
                continuation(feed, table.route_id, column, routes or {}, locale),
            )
            if note and note.text
        ]
        for column in table.columns
    ]
    letters = {
        note: note_letter(i)
        for i, note in enumerate(dict.fromkeys(n for col in notes for n in col))
    }
    heads = tuple(
        replace(
            column_head(column, time_format, shaded, locale),
            note=" ".join(letters[n] for n in note),
            bikes="bikes" in marked and column.bikes == 1,
            wheelchair="trips" in marked and column.wheelchair == 1,
        )
        for column, shaded, note in zip(
            table.columns, hour_shading(table.columns), notes, strict=True
        )
    )
    # Each column's served rows.
    patterns = [
        [k for k, cell in enumerate(column.cells) if cell is not None]
        for column in table.columns
    ]
    size = len(table.rows)
    graph = route_graph(list(dict.fromkeys(map(tuple, patterns))), size)
    stats = stop_stats(patterns, size)
    total = len(table.columns)
    threshold = endpoint_threshold(total)
    rows = tuple(
        RowView(
            name=tidy(row.name),
            zone=zone_label(row.timezone, table.day_type.start),
            cells=tuple(
                cell_view(column.cells[k], time_format) for column in table.columns
            ),
            google=stop_link(feed, row.stop_id),
            paths=() if straight(graph.rows[k]) else tuple(row_paths(graph, k)),
            rail=rail_class(graph.rows[k]),
            dot_x=lane_x(graph.rows[k].lane),
            solid=is_endpoint(stats[k], threshold),
            wheelchair="stops" in marked and row.wheelchair == 1,
            num=(numbers or {}).get(row.stop_id, 0),
            approx=not row.exact,
            code=tidy(row.code),
            platform=tidy(row.platform),
            fare_zone=(fare_zones or {}).get(row.stop_id, ""),
            reading=tidy(row.reading) if row.reading != row.name else "",
            reading_lang=row.reading_lang,
        )
        for k, row in enumerate(table.rows)
    )
    cells = [cell for row in rows for cell in row.cells]
    keys = []
    if time_format == "12h" and any(c.time and c.time.pm for c in cells):
        keys.append("pm")
    shown_times = [time for c in cells for time in (c.time, c.arrival) if time]
    days = {time.days for time in shown_times if time.days}
    if days == {1}:
        keys.append("next_day")
    elif any(d > 0 for d in days):
        keys.append("later_days")
    if any(d < 0 for d in days):
        keys.append("evening_before")
    if any(c.untimed for c in cells):
        keys.append("untimed")
    if any("d" in c.mark for c in cells):
        keys.append("drop_off")
    if any("p" in c.mark for c in cells):
        keys.append("pick_up")
    if any("f" in c.mark for c in cells):
        keys.append("flag")
    if any(c.arrival for c in cells):
        keys.append("dwell")
    if any(row.approx and any(c.time for c in row.cells) for row in rows):
        keys.append("approx")
    legend = [
        *(Key("", n.text, letter=letter, href=n.href) for n, letter in letters.items()),
        *(Key("", t(locale, f"legend.{key}")) for key in keys),
    ]
    shown_icons = {
        "bikes": any(head.bikes for head in heads),
        "trips": any(head.wheelchair for head in heads),
        "stops": any(row.wheelchair for row in rows),
    }
    icons = [
        Key(icon, t(locale, f"amenity.{name}"))
        for name, icon in AMENITIES.items()
        if shown_icons[name]
    ]
    headsigns = [tidy(h) for h in table.headsigns]
    # Trip numbers repeated on every trip (often the route name) add nothing.
    return TableView(
        title=table_title(headsigns, locale),
        heads=heads,
        headsigns=headsign_spans(heads),
        show_numbers=len({head.number for head in heads}) > 1,
        show_headsigns=len({head.headsign for head in heads}) > 1,
        show_notes=bool(letters),
        rows=rows,
        legend=(*icons, *legend),
        anchor=anchor,
        trips=len(table.columns),
        gutter=gutter_width(graph.lane_count),
        trip_ids=tuple(
            "" if column.headway else column.trip_id for column in table.columns
        ),
        stop_ids=tuple(row.stop_id for row in table.rows),
        sched=tuple(
            minute_steps(
                [
                    cell.time - row.shift - column.day_shift
                    if cell and cell.time is not None
                    else None
                    for row, cell in zip(table.rows, column.cells, strict=True)
                ]
            )
            for column in table.columns
        ),
        shifts=tuple(row.shift // 60 for row in table.rows),
    )


def continuation(
    feed: Feed,
    route_id: str,
    column: Column,
    routes: Mapping[str, "RouteView"],
    locale: Locale = "en",
) -> Note | None:
    """'Continues as <route> to <headsign>' when the column's block goes on
    on another route, else None: on its own route that is the next column
    back."""
    trip = feed.trips.get(column.next_trip)
    if trip is None or trip.route_id == route_id:
        return None
    to = tidy(headsign(feed, trip))
    view = routes.get(trip.route_id)
    route = view.badge if view else tidy(feed.routes[trip.route_id].name)
    text = t(locale, "route.continues_as", route=route, headsign=to)
    return Note(text, f"../{view.slug}/" if view else "")


def day_notes(day: DayType, locale: Locale = "en") -> tuple[str, ...]:
    if not day.regular:
        listed = dates_label(day.dates, locale)
        # A name like 'Weekday, Oct 12 to Oct 16' already says it all.
        if day_name(day, locale) != capitalize(listed):
            return ()
        return (t(locale, "day.runs_only", dates=listed),)
    notes = []
    if day.missing:
        notes.append(t(locale, "day.no_service", dates=missing_label(day, locale)))
    if day.extra:
        notes.append(t(locale, "day.also_runs", dates=dates_label(day.extra, locale)))
    return tuple(notes)


def runs(table: Timetable) -> int:
    """Trips run over the horizon: each trip once per date it runs."""
    return sum(len(table.day_type.trip_dates(c.trip_id)) for c in table.columns)


def trip_span(tables: list[Timetable]) -> tuple[int, int] | None:
    """Earliest and latest trip start over the tables; a frequency column's
    last start is the last headway before its end."""
    starts = []
    for table in tables:
        for column in table.columns:
            if (headway := column.headway) is not None:
                gap = headway.headway_secs
                last = headway.start + (headway.end - headway.start - 1) // gap * gap
                starts += [headway.start, max(headway.start, last)]
            elif (first := column.first_time()) is not None:
                starts.append(first)
    return (min(starts), max(starts)) if starts else None


def endpoints(tables: list[Timetable]) -> tuple[str, str] | None:
    """First and last stop names of the busiest table, when they differ."""
    if not tables:
        return None
    busiest = max(tables, key=runs)
    names = [tidy(row.name) for row in busiest.rows]
    if len(names) < 2 or names[0] == names[-1]:
        return None
    return names[0], names[-1]


def ordered_days(tables: list[Timetable]) -> list[tuple[DayType, list[Timetable]]]:
    """Tables grouped by day type, busiest first by runs over the horizon;
    within a day, busiest table first."""
    by_day: dict[DayType, list[Timetable]] = {}
    for table in tables:
        by_day.setdefault(table.day_type, []).append(table)
    day_runs = {day: sum(runs(t) for t in group) for day, group in by_day.items()}
    return [
        (day, sorted(by_day[day], key=lambda t: (-runs(t), t.direction_id or 0)))
        for day in sorted(by_day, key=lambda d: (-day_runs[d], day_order(d)))
    ]


def stop_numbers(tables: list[Timetable]) -> dict[str, int]:
    """stop_id -> 1..N in order of first appearance among the rows of the
    tables, in page order, so a route's map and tables share numbers."""
    numbers: dict[str, int] = {}
    for _, group in ordered_days(tables):
        for table in group:
            for row in table.rows:
                if row.stop_id not in numbers:
                    numbers[row.stop_id] = len(numbers) + 1
    return numbers


def distinct_titles(
    views: list[TableView], locale: Locale = "en"
) -> tuple[TableView, ...]:
    """Tables sharing a title within a day are titled by their first and last
    stops instead."""
    counts = Counter(view.title for view in views)
    return tuple(
        replace(
            view,
            title=t(
                locale,
                "table.from_to",
                first=view.rows[0].name,
                last=view.rows[-1].name,
            ),
        )
        if counts[view.title] > 1 and view.rows
        else view
        for view in views
    )


def day_views(
    feed: Feed,
    tables: list[Timetable],
    time_format: TimeFormat,
    marked: frozenset[str] = frozenset(),
    locale: Locale = "en",
    routes: Mapping[str, "RouteView"] | None = None,
    today: date | None = None,
    fare_zones: Mapping[str, str] | None = None,
) -> list[DayView]:
    """A view per day type in page order (see `ordered_days`), stops numbered
    by `stop_numbers`; `routes` links block continuations to other routes,
    `fare_zones` is the fare zone badge text by stop id."""
    numbers = stop_numbers(tables)
    views = []
    anchors: set[str] = set()
    for day, group in ordered_days(tables):
        name = day_name(day, locale)
        anchor = slugify(name) or "day"
        while anchor in anchors:
            anchor += "-2"
        anchors.add(anchor)
        span = trip_span(group)
        views.append(
            DayView(
                anchor=anchor,
                name=name,
                notes=day_notes(day, locale),
                tables=distinct_titles(
                    [
                        table_view(
                            feed,
                            table,
                            time_format,
                            f"{anchor}-{i + 1}",
                            marked,
                            locale,
                            numbers,
                            routes,
                            fare_zones,
                        )
                        for i, table in enumerate(group)
                    ],
                    locale,
                ),
                runs=sum(runs(t) for t in group),
                first=clock_label(span[0], time_format) if span else "",
                last=clock_label(span[1], time_format) if span else "",
                valid=valid_label(feed, group, today, locale),
            )
        )
    return views


def valid_label(
    feed: Feed,
    tables: list[Timetable],
    today: date | None,
    locale: Locale = "en",
) -> str:
    """'Valid until Dec 31, 2026.', or 'Valid from Jan 5, 2027 to ...' when
    service starts after `today`, from the calendars of the tables' trips.
    Empty for exception days, which list their own dates."""
    if not tables or not tables[0].day_type.regular:
        return ""
    services = {
        feed.trips[c.trip_id].service_id for table in tables for c in table.columns
    }
    starts, ends = [], []
    for service_id in services:
        if calendar := feed.calendars.get(service_id):
            starts.append(calendar.start)
            ends.append(calendar.end)
        added = [d for d, on in feed.calendar_dates.get(service_id, {}).items() if on]
        starts += added
        ends += added
    if not ends:
        return ""
    first, last = min(starts), max(ends)
    if today and first > today:
        return t(locale, "day.valid", range=date_range(first, last, locale, "valid"))
    return t(locale, "day.valid_until", date=long_date(last, locale))


def route_labels(feed: Feed, routes: list[Route]) -> dict[str, str]:
    """route_id -> its agency's name, for routes whose name another agency's
    route shares; e.g. two agencies' 'Commuter Rail'."""
    agencies: dict[str, set[str]] = {}
    for route in routes:
        agencies.setdefault(tidy(route.name), set()).add(route.agency_id)
    return {
        route.route_id: tidy(agency.name)
        for route in routes
        if len(agencies[tidy(route.name)]) > 1
        and (agency := feed.agencies.get(route.agency_id))
    }


def route_view(
    route: Route,
    slug: str,
    tables: list[Timetable],
    label: str = "",
    state: str = "",
    state_date: date | None = None,
    locale: Locale = "en",
) -> RouteView:
    short, long = tidy(route.short_name), tidy(route.long_name)
    name = long if short else ""
    if label:
        name = f"{name} ({label})" if name else label
    days: list[str] = []
    for day in sorted({table.day_type for table in tables}, key=day_order):
        if day.regular and (day_text := day_name(day, locale)) not in days:
            days.append(day_text)
    return RouteView(
        route_id=route.route_id,
        slug=slug,
        badge=short or long or route.route_id,
        name=name,
        colors=badge_colors(route),
        days=tuple(days),
        url=route.url,
        desc=tidy(route.desc),
        mode=mode_name(route.route_type),
        endpoints=endpoints(tables),
        state=state,
        state_date=state_date,
        locale=locale,
    )


def route_slugs(
    routes: list[Route], labels: dict[str, str], reserved: Iterable[str] = ()
) -> dict[str, str]:
    """route_id -> a unique URL slug from the route's name and label, never
    one of `reserved` (the site's other directories)."""
    slugs: dict[str, str] = {}
    taken: set[str] = set(reserved)
    for route in routes:
        text = f"{route.name} {labels.get(route.route_id, '')}"
        base = slugify(text) or slugify(route.route_id) or "route"
        slug = base
        n = 2
        while slug in taken:
            slug = f"{base}-{n}"
            n += 1
        taken.add(slug)
        slugs[route.route_id] = slug
    return slugs


def service_range(feed: Feed) -> tuple[date | None, date | None]:
    """The first and last dates of the feed's calendars."""
    return first_service_date(feed), last_service_date(feed)


def feed_info_range(feed: Feed, locale: Locale = "en") -> str:
    """'The feed says it is valid from ... to ...', when feed_info has dates
    and either differs from the calendars'; else empty."""
    info = feed.feed_info
    if not info or not (info.start or info.end):
        return ""
    start, end = service_range(feed)
    if (info.start or start) == start and (info.end or end) == end:
        return ""
    return t(
        locale,
        "index.feed_info_valid",
        range=date_range(info.start, info.end, locale, "valid"),
    )


def date_range(
    start: date | None, end: date | None, locale: Locale = "en", both: str = "both"
) -> str:
    """'Jan 2, 2026 to Dec 31, 2029', 'from Jan 2, 2026', 'to Dec 31, 2029'
    or empty; `both` picks the `range.<both>` text used with both dates."""
    if start and end:
        return t(
            locale,
            f"range.{both}",
            start=long_date(start, locale),
            end=long_date(end, locale),
        )
    if start:
        return t(locale, "range.from", start=long_date(start, locale))
    return t(locale, "range.to", end=long_date(end, locale)) if end else ""


def duration_text(seconds: int, locale: Locale = "en") -> str:
    """'60 min', in whole minutes rounded up."""
    return t(locale, "fare.minutes", n=-(-seconds // 60))


def transfers_text(transfers: Transfers, locale: Locale = "en") -> str:
    """'1 free transfer within 60 min', 'no free transfers', or empty when
    the feed leaves both count and duration blank."""
    count, duration = transfers.count, transfers.duration
    if count == 0:
        if duration:
            return t(locale, "fare.valid_for", duration=duration_text(duration, locale))
        return t(locale, "fare.no_transfers")
    if count is None and not duration:
        return ""
    text = (
        t(locale, "fare.unlimited")
        if count is None
        else t(locale, "fare.transfers", n=count)
    )
    if duration:
        return t(
            locale,
            "fare.within",
            transfers=text,
            duration=duration_text(duration, locale),
        )
    return text


def timeframe_text(
    frames: Sequence[Timeframe], time_format: TimeFormat, locale: Locale = "en"
) -> str:
    """'7:00 AM to 9:00 AM', each window of a timeframe group."""
    return ", ".join(
        t(
            locale,
            "fare.times",
            start=clock_label(frame.start or 0, time_format),
            end=clock_label(
                frame.end if frame.end is not None else DAY_SECONDS, time_format
            ),
        )
        for frame in frames
    )


def fare_text(
    fare: Fare,
    several: bool,
    locale: Locale = "en",
    timeframes: Mapping[str, Sequence[Timeframe]] | None = None,
    time_format: TimeFormat = "24h",
) -> str:
    """'$1.25, 1 free transfer within 60 min'; with `several`, the payment
    method too ('$1.25 paid on board'). A v2 fare: '$1.70 with Smart card
    (Bus fare)', and its timeframe's hours when it has one."""
    text = price(fare.price, fare.currency, locale)
    if fare.media:
        text = t(locale, "fare.media", price=text, media=", ".join(fare.media))
    if fare.product:
        text = t(locale, "fare.product", price=text, product=fare.product)
    if fare.timeframe:
        frames = (timeframes or {})[fare.timeframe]
        times = timeframe_text(frames, time_format, locale)
        return t(locale, "fare.during", fare=text, times=times)
    if several and fare.payment_method is not None:
        key = "fare.before_boarding" if fare.payment_method else "fare.on_board"
        text = t(locale, key, price=text)
    extra = transfers_text(fare.transfers, locale) if fare.transfers else ""
    return t(locale, "fare.with", fare=text, transfers=extra) if extra else text


def fare_lines(
    fares: RouteFares,
    locale: Locale = "en",
    timeframes: Mapping[str, Sequence[Timeframe]] | None = None,
    time_format: TimeFormat = "24h",
) -> tuple[str, ...]:
    """A line per flat fare for the route page's fares card; empty without
    fares."""
    if not isinstance(fares, FlatFare):
        return ()
    several = len(fares.fares) > 1
    return tuple(
        fare_text(fare, several, locale, timeframes, time_format)
        for fare in fares.fares
    )


@dataclass(frozen=True, slots=True)
class FareChart:
    """A from-to fare table: `rows` pairs a place with its prices, one per
    column of `heads`; a triangle has shorter rows."""

    caption: str
    note: str
    heads: tuple[str, ...]
    rows: tuple[tuple[str, tuple[str, ...]], ...]


def fare_charts(fares: RouteFares, locale: Locale = "en") -> tuple[FareChart, ...]:
    """The route page's from-to tables for zone fares, one per payment method
    when prices depend on it; a triangle when every ride costs the same both
    ways. Empty for flat fares."""
    if not isinstance(fares, ZoneFares):
        return ()
    labels = zone_labels(fares, locale)
    methods = sorted(
        {f.payment_method for row in fares.cells for cell in row for f in cell} - {None}
    )
    groups: list[int | None] = list(methods) if len(methods) > 1 else [None]
    charts = []
    for method in groups:
        texts = [
            [
                " / ".join(
                    dict.fromkeys(
                        price(f.price, f.currency, locale)
                        for f in cell
                        if method is None or f.payment_method in (method, None)
                    )
                )
                for cell in row
            ]
            for row in fares.cells
        ]
        size = len(texts)
        symmetric = all(
            texts[i][j] == texts[j][i] for i in range(size) for j in range(i)
        )
        within = any(texts[i][i] for i in range(size))
        places = "zones" if fares.zones else "stops"
        if symmetric:
            # Row i holds the rides to every earlier place.
            first = 0 if within else 1
            rows = tuple(
                (labels[i], tuple(texts[i][: i + 1 if within else i]))
                for i in range(first, size)
            )
            heads = labels[: size if within else size - 1]
            note = t(locale, f"fare.between_{places}")
        else:
            rows = tuple((labels[i], tuple(texts[i])) for i in range(size))
            heads = labels
            note = t(locale, f"fare.from_{places}")
        caption = ""
        if method is not None:
            caption = (
                t(locale, "fare.paid_before")
                if method
                else t(locale, "fare.paid_on_board")
            )
        charts.append(FareChart(caption, note, heads, rows))
    return tuple(charts)


def zone_labels(fares: ZoneFares, locale: Locale = "en") -> tuple[str, ...]:
    """Each place's label: 'Zone 1' for a v1 zone, the area name for v2,
    the stop name when places are stops."""
    if fares.named:
        return tuple(label or t(locale, "fare.other_stops") for label in fares.labels)
    if fares.zones:
        return tuple(t(locale, "fare.zone", zone=label) for label in fares.labels)
    return fares.labels


def fare_badges(fares: RouteFares, locale: Locale = "en") -> dict[str, str]:
    """Badge text by station for stops in a zone fare table, else empty."""
    if not isinstance(fares, ZoneFares) or not fares.zones:
        return {}
    labels = dict(zip(fares.zones, zone_labels(fares, locale), strict=True))
    return {
        station: labels[zone]
        for station, zone in fares.stations.items()
        if zone in labels
    }


@dataclass(frozen=True, slots=True)
class FareSection:
    """A network's or agency's fares on the site fares page; no heading
    when the site has one section."""

    heading: str
    lines: tuple[str, ...]


def fare_sections(
    fares: SiteFares,
    locale: Locale = "en",
    timeframes: Mapping[str, Sequence[Timeframe]] | None = None,
    time_format: TimeFormat = "24h",
) -> tuple[FareSection, ...]:
    """A section per fare group, a line per fare."""
    one = len(fares.groups) == 1
    return tuple(
        FareSection(
            "" if one else group.name or t(locale, "fares.any_routes"),
            tuple(
                fare_text(fare, len(group.fares) > 1, locale, timeframes, time_format)
                for fare in group.fares
            ),
        )
        for group in fares.groups
    )


def legs_text(legs: LegGroup | None, locale: Locale = "en") -> str:
    """'Subway one-way fare (rapid_transit, from Airport)', or 'any fare'."""
    if legs is None:
        return t(locale, "fare.any_leg")
    products = " / ".join(legs.products)
    where = list(legs.networks)
    if legs.from_area:
        where.append(t(locale, "fare.from_area", area=legs.from_area))
    if not where:
        return products
    return t(locale, "fare.legs", products=products, networks=", ".join(where))


def transfer_text(rule: TransferRule, locale: Locale = "en") -> str:
    """'Local Bus one-way fare (local_bus) to Subway one-way fare
    (rapid_transit): $0.70 (Subway transfer) extra, within 120 min'."""
    fee = " / ".join(fare_text(fare, False, locale) for fare in rule.fee)
    if rule.fare_transfer_type == 1:
        terms = [
            t(locale, "fare.both_fares_fee", fee=fee)
            if fee
            else t(locale, "fare.both_fares")
        ]
    elif fee:
        key = "fare.fee_only" if rule.fare_transfer_type == 2 else "fare.transfer_fee"
        terms = [t(locale, key, fee=fee)]
    else:
        terms = [t(locale, "fare.transfer_free")]
    if rule.count == -1:
        terms.append(t(locale, "fare.transfer_any"))
    elif rule.count and rule.count > 1:
        terms.append(t(locale, "fare.transfer_up_to", n=rule.count))
    if rule.duration:
        terms.append(
            t(
                locale,
                "fare.transfer_within",
                duration=duration_text(rule.duration, locale),
            )
        )
    return t(
        locale,
        "fare.transfer",
        from_legs=legs_text(rule.from_legs, locale),
        to_legs=legs_text(rule.to_legs, locale),
        terms=", ".join(terms),
    )

"""build_site: the only entry point from a GTFS zip to a site's files."""

import hashlib
import json
import re
from collections import Counter
from dataclasses import replace
from datetime import date, timedelta
from importlib.metadata import version
from typing import NamedTuple
from urllib.parse import urlsplit

from anyascii import anyascii
from babel import Locale as BabelLocale
from babel.core import UnknownLocaleError
from gtfs_zone_feed_fetch.facts import zip_digest
from gtfs_zone_feed_fetch.localities import Localities
from gtfs_zone_feed_fetch.places import feed_place, place_label
from markupsafe import Markup

from gtfs_zone_timetable_sites.boundaries import (
    Region,
    country,
    region_of,
    regions,
    world,
)
from gtfs_zone_timetable_sites.catalog import shard_of
from gtfs_zone_timetable_sites.config import RouteFilter, Site
from gtfs_zone_timetable_sites.facts import feed_facts
from gtfs_zone_timetable_sites.fares import (
    NoFares,
    RouteFares,
    SiteFares,
    ZoneFares,
    route_fares,
    site_fares,
)
from gtfs_zone_timetable_sites.feedpage import (
    RT_ROLES,
    STATES,
    dataset,
    feed_view,
    overlaps_view,
    place_line,
    route_overlaps,
)
from gtfs_zone_timetable_sites.gtfs.reader import (
    Agency,
    FaresError,
    Feed,
    Route,
    ZipSource,
    open_zip,
    read_feed,
)
from gtfs_zone_timetable_sites.gtfs.service import (
    Services,
    day_types,
    horizon_start,
    key_services,
    max_day_shift,
    route_windows,
    service_rule,
)
from gtfs_zone_timetable_sites.i18n import (
    FALLBACK,
    UI_LOCALES,
    Locale,
    capitalize,
    join_and,
    message,
    plural_forms,
    t,
)
from gtfs_zone_timetable_sites.languages import feed_languages, translate
from gtfs_zone_timetable_sites.maps.geo import (
    SystemRoute,
    route_data,
    shape_points,
    system_data,
)
from gtfs_zone_timetable_sites.pages import (
    RouteView,
    booking_marks,
    booking_views,
    breadcrumbs,
    date_range,
    day_views,
    fare_badges,
    fare_charts,
    fare_lines,
    fare_sections,
    feed_amenities,
    feed_info_range,
    flex_notes,
    mode_groups,
    modes_label,
    modes_text,
    place_jsonld,
    route_labels,
    route_slugs,
    route_view,
    service_range,
    stop_numbers,
    tidy,
    transfer_text,
)
from gtfs_zone_timetable_sites.places import locate
from gtfs_zone_timetable_sites.render import asset, compact_json, render
from gtfs_zone_timetable_sites.sources import sources_page
from gtfs_zone_timetable_sites.timetable import (
    Blocks,
    Timetable,
    relabel,
    route_timetables,
)

BASE_URL = "https://sites.gtfs.zone"
ROOT_TITLE = "sites.gtfs.zone"
# The footer's build date, as base.html marks it up.
GENERATED = re.compile(rb'<time class="generated"[^>]*>.*?</time>')
# Agencies named in a site's title before "and N others".
TITLE_AGENCIES = 3


def page_digest(body: bytes) -> str:
    """sha256 of a page without its build date, so it changes only with the
    page's content."""
    return hashlib.sha256(GENERATED.sub(b"", body)).hexdigest()


def site_feed(source: ZipSource, site: Site, today: date) -> Feed:
    """The feed with only the routes the site's filter accepts, and only the
    trips that can run in their routes' windows from `today`."""
    return read_feed(
        source,
        keep_route=lambda r: site.routes.accepts(r.route_id, r.route_type),
        keep_services=lambda feed: window_services(feed, today, site.horizon_days),
        thin_shapes=shape_points,
    )


def route_order(route: Route) -> tuple[int, int, str, str]:
    """route_sort_order, then numeric short names in number order, then name."""
    number = route.short_name.isdigit()
    return (
        route.sort_order if route.sort_order is not None else 1 << 30,
        int(route.short_name) if number else 1 << 30,
        route.name,
        route.route_id,
    )


def merge_map(routes: dict[str, Route]) -> dict[str, str]:
    """route_id -> the route it merges into: the first by `route_order` of
    the routes sharing its agency, type, names and colors."""
    keep: dict[tuple, str] = {}
    into: dict[str, str] = {}
    for route in sorted(routes.values(), key=route_order):
        key = (
            route.agency_id,
            route.route_type,
            route.short_name,
            route.long_name,
            route.color,
            route.text_color,
        )
        into[route.route_id] = keep.setdefault(key, route.route_id)
    return into


def merged_services(feed: Feed, into: dict[str, str]) -> dict[str, frozenset[str]]:
    """Each merged route's service_ids, from every route merged into it."""
    services: dict[str, set[str]] = {}
    for route_id, target in into.items():
        services.setdefault(target, set()).update(feed.route_services.get(route_id, ()))
    return {route_id: frozenset(ids) for route_id, ids in services.items()}


def merge_routes(feed: Feed) -> Feed:
    """The feed with routes merged by `merge_map`, their trips and services
    moved onto the route kept; e.g. a feed with a route per train per date."""
    into = merge_map(feed.routes)
    if all(into[rid] == rid for rid in into):
        return feed
    return replace(
        feed,
        routes={rid: r for rid, r in feed.routes.items() if into[rid] == rid},
        trips={
            tid: trip
            if into[trip.route_id] == trip.route_id
            else replace(trip, route_id=into[trip.route_id])
            for tid, trip in feed.trips.items()
        },
        route_services=merged_services(feed, into),
    )


def window_services(feed: Feed, today: date, days: int) -> set[str]:
    """The services running on some date of a merged route's windows (see
    `route_windows`), or as many days after as `day_types` can shift a trip
    back into it: the services `site_timetables` can show."""
    services = Services(feed)
    start = horizon_start(feed, today, services)
    starts = {
        window.start
        for service_ids in merged_services(feed, merge_map(feed.routes)).values()
        for window in route_windows(services, service_ids, start, days)
    }
    running: set[str] = set()
    for first in starts:
        for offset in range(days + max_day_shift(feed, first)):
            running |= services.active(first + timedelta(days=offset))
    return running


def route_agencies(feed: Feed, route: Route) -> tuple[str, ...]:
    """The names of the route's agency, or of the feed's only agency when the
    route names none it has."""
    agency = feed.agencies.get(route.agency_id)
    if agency is not None:
        return (agency.name,)
    if len(feed.agencies) == 1:
        return tuple(a.name for a in feed.agencies.values())
    return ()


def agencies_by_routes(feed: Feed) -> list[Agency]:
    """The feed's agencies, those running the most routes first."""
    counts: dict[str, int] = {}
    for route in feed.routes.values():
        counts[route.agency_id] = counts.get(route.agency_id, 0) + 1
    return sorted(feed.agencies.values(), key=lambda a: -counts.get(a.agency_id, 0))


class RouteTables(NamedTuple):
    route: Route
    tables: list[Timetable]
    # "expired" or "upcoming" when the tables are from outside the horizon.
    state: str = ""
    state_date: date | None = None


def site_timetables(feed: Feed, site: Site, today: date) -> list[RouteTables]:
    """Every route's timetables over the site's horizon from `today`. A route
    with no service in the horizon gets its last `horizon_days` of service
    when that ended, or its first when that starts later."""
    services = Services(feed)
    start = horizon_start(feed, today, services)
    days = site.horizon_days
    blocks = Blocks(feed, services)
    trips_by_route: dict[str, list[str]] = {}
    for trip in feed.trips.values():
        trips_by_route.setdefault(trip.route_id, []).append(trip.trip_id)
    result = []
    for route in sorted(feed.routes.values(), key=route_order):
        trip_ids = trips_by_route.get(route.route_id, [])
        service_ids = feed.route_services.get(route.route_id, ())
        for window in route_windows(services, service_ids, start, days):
            types = day_types(feed, trip_ids, window.start, days, services)
            tables = route_timetables(feed, route.route_id, types, blocks)
            if tables:
                result.append(
                    RouteTables(route, tables, window.state, window.state_date)
                )
                break
    return result


class EmptyFeed(ValueError):
    """A zip with no route that has scheduled trips."""


def site_title(feed: Feed, site: Site, locale: Locale = "en") -> str:
    """The configured title, else the named agencies with the most routes
    first, else the catalog's feed name, else the slug."""
    if site.title:
        return site.title
    running = {route.agency_id for route in feed.routes.values()}
    agencies = agencies_by_routes(feed)
    # Only agencies running a route, unless no route names a listed agency.
    agencies = [a for a in agencies if a.agency_id in running] or agencies
    names = [name for a in agencies if (name := tidy(a.name))]
    if len(names) > TITLE_AGENCIES:
        rest = len(names) - TITLE_AGENCIES
        names = [*names[:TITLE_AGENCIES], t(locale, "others", n=rest)]
    if names:
        return join_and(names, locale)
    return (site.catalog and tidy(site.catalog.name)) or site.slug


def with_locality(title: str, locality: str | None) -> str:
    """'BCTransit, Quesnel' for a site told apart by its town, else the title."""
    if locality and locality.casefold() not in title.casefold():
        return f"{title}, {locality}"
    return title


def source_label(url: str) -> str:
    """'host: file' for a download URL, e.g. 'github.com: gtfs.zip'."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    name = parts.path.rstrip("/").rsplit("/", 1)[-1]
    if parts.query:
        name = f"{name}?{parts.query}"
    if not host:
        return name
    return f"{host}: {name}" if name else host


class PageSet(NamedTuple):
    """One language's pages of a site."""

    # The language of the feed text, as `<html lang>`.
    lang: str
    # The UI catalog: `lang`'s when there is one, else English.
    locale: str
    # The set's directory under the site: empty for the default language.
    prefix: str


def page_sets(feed: Feed, site: Site) -> list[PageSet]:
    """A page set per language the feed offers, the default first. A pinned
    `locale` replaces the feed's default language."""
    languages = feed_languages(feed)
    default = site.locale or languages.default
    codes = [default, *(code for code in languages.offered[1:] if code != default)]
    return [
        PageSet(
            lang=code,
            locale=code if code in UI_LOCALES else FALLBACK,
            prefix=f"{code.lower()}/" if i else "",
        )
        for i, code in enumerate(codes)
    ]


def stop_readings(feed: Feed, codes: tuple[str, ...]) -> dict[str, tuple[str, str]]:
    """stop_id -> (reading, its code) for stops whose name translations.txt
    gives in a reading code (`ja-Hrkt`), the first code in `codes` first."""
    readings: dict[str, tuple[str, str]] = {}
    for code in codes:
        text = translate(feed, code)
        for stop_id, stop in text.stops.items():
            if stop.name != feed.stops[stop_id].name:
                readings.setdefault(stop_id, (stop.name, code))
    return readings


def language_name(code: str) -> str:
    """A language named in itself ('Français', '日本語'), else in English."""
    try:
        name = BabelLocale.parse(code, sep="-").get_display_name()
    except (UnknownLocaleError, ValueError):
        name = None
    return capitalize(name or BabelLocale("en").languages.get(code, code))


def alternates(
    sets: list[PageSet], site_url: str, path: str, assets: str, lang: str
) -> list[dict]:
    """The picker and hreflang links of the page at `path` (relative to a page
    set) in each set: `href` relative from the page, `url` absolute. Empty for
    a one-language site."""
    if len(sets) < 2:
        return []
    return [
        {
            "code": s.lang,
            "name": language_name(s.lang),
            "href": f"{assets}{s.prefix}{path}" or "./",
            "url": f"{site_url}{s.prefix}{path}",
            "current": s.lang == lang,
        }
        for s in sets
    ]


def feed_fares(
    feed: Feed, trip_ids: list[set[str]]
) -> tuple[SiteFares | None, list[RouteFares], str]:
    """The site's fares and those of each route's `trip_ids`, or no fares and
    the error when the feed's fares cannot be read or linked."""
    try:
        if feed.fares.error:
            raise FaresError(feed.fares.error)
        return site_fares(feed), [route_fares(feed, ids) for ids in trip_ids], ""
    except FaresError as error:
        return None, [NoFares() for _ in trip_ids], str(error)


def build_site(
    source: ZipSource,
    site: Site,
    today: date | None = None,
    localities: Localities | None = None,
) -> dict[str, bytes]:
    """Map of path (relative to the site root) to file contents: a page set per
    language (see `page_sets`) plus the files they share. `source` is the zip's
    bytes or a seekable binary file; `localities`, when given, adds municipality
    tags to the content report. No I/O beyond reading `source` and
    `localities`."""
    with open_zip(source) as archive:
        bad = archive.testzip()
    if bad is not None:
        raise ValueError(f"corrupt member in GTFS zip: {bad}")
    digest = zip_digest(source)
    today = today or date.today()
    read = site_feed(source, site, today)
    feed = merge_routes(read)
    timetables = site_timetables(feed, site, today)
    if not timetables:
        raise EmptyFeed("no route has scheduled trips")
    sets = page_sets(read, site)
    languages = feed_languages(read)
    readings = stop_readings(feed, languages.readings)
    labels = route_labels(feed, [rt.route for rt in timetables])
    reserved = {"fares", *(s.prefix.rstrip("/") for s in sets if s.prefix)}
    slugs = route_slugs([rt.route for rt in timetables], labels, reserved)
    site_url = f"{BASE_URL}/{site.slug}/"
    stations = locate(feed, localities)
    place = feed_place(stations)
    all_tables = [table for rt in timetables for table in rt.tables]
    start, end = service_range(feed)
    maps = site.map == "maplibre"
    vehicles = site.vehicles_url()
    updates = site.trip_updates_url()
    all_fares, routes_fares, fares_error = feed_fares(
        read,
        [
            {c.trip_id for table in rt.tables for c in table.columns} - {""}
            for rt in timetables
        ],
    )
    fares_page = bool(all_fares) or bool(fares_error)

    # Per page set: the feed with its text in the set's language, the
    # timetables relabeled from it, its route views and what every one of its
    # pages shares. Slugs stay those of the feed's own text.
    texts: dict[str, Feed] = {}
    set_tables: dict[str, list[list[Timetable]]] = {}
    views: dict[str, list[RouteView]] = {}
    commons: dict[str, dict] = {}
    for s in sets:
        locale = s.locale
        text = texts[s.lang] = translate(feed, s.lang)
        # Readings of the names the set shows untranslated or in the default
        # language.
        default_text = texts[sets[0].lang]
        set_readings = {
            stop_id: reading
            for stop_id, reading in readings.items()
            if text.stops[stop_id].name
            in (feed.stops[stop_id].name, default_text.stops[stop_id].name)
        }
        set_tables[s.lang] = [
            rt.tables
            if text is feed and not set_readings
            else [relabel(table, text, set_readings) for table in rt.tables]
            for rt in timetables
        ]
        set_labels = route_labels(text, [rt.route for rt in timetables])
        feed_notes, marked = feed_amenities(all_tables, locale)
        feed_notes = (*feed_notes, *flex_notes(feed, locale))
        title = site_title(text, site, locale)
        label = place_label(place, title, site.locality)
        base_url = f"{site_url}{s.prefix}"
        views[s.lang] = [
            route_view(
                text.routes[rt.route.route_id],
                slugs[rt.route.route_id],
                tables,
                set_labels.get(rt.route.route_id, ""),
                rt.state,
                rt.state_date,
                locale,
            )
            for rt, tables in zip(timetables, set_tables[s.lang], strict=True)
        ]
        commons[s.lang] = {
            "locale": locale,
            "html_lang": s.lang,
            "site_title": title,
            # The title with where the feed runs, for page titles and descriptions.
            "site_place": f"{title}, {label}" if label else title,
            "place": label,
            "local": bool(label)
            and (bool(site.locality) or (place is not None and bool(place.region))),
            "base_url": base_url,
            "generated": today,
            "feed_notes": feed_notes,
            "marked": marked,
            "source": data_source(text, site),
            "map_text": map_text(locale),
            "trail": [
                (ROOT_TITLE, f"{BASE_URL}/"),
                (with_locality(title, site.locality), base_url),
            ],
        }

    routes = views[sets[0].lang]
    expired = [view for view in routes if view.state == "expired"]
    # Search engines skip expired routes, and the whole site when it is not
    # indexed or every route has expired.
    indexed = site.index and any(view.state != "expired" for view in routes)
    route_noindex = {
        view.slug for view in routes if not indexed or view.state == "expired"
    }
    noindex: set[str] = set()
    files: dict[str, bytes] = {}
    by_route = {
        lang: {view.route_id: view for view in set_views}
        for lang, set_views in views.items()
    }

    def page(s: PageSet, path: str, template: str, **context: object) -> None:
        """Render `template` at `path` of page set `s`."""
        depth = path.count("/")
        root = "../" * depth
        assets = root + ("../" if s.prefix else "")
        common = commons[s.lang]
        files[f"{s.prefix}{path}index.html"] = render(
            template,
            **{k: v for k, v in common.items() if k not in ("trail", "marked")},
            root=root,
            assets=assets,
            languages=alternates(sets, site_url, path, assets, s.lang),
            noindex=f"{s.prefix}{path}index.html" in noindex,
            **context,
        )

    if not indexed:
        noindex |= {f"{s.prefix}index.html" for s in sets}
    # A fares page with only the error is left out too.
    if fares_page and (not indexed or not all_fares):
        noindex |= {f"{s.prefix}fares/index.html" for s in sets}
    noindex |= {f"{s.prefix}{slug}/index.html" for s in sets for slug in route_noindex}

    for s in sets:
        locale = s.locale
        common = commons[s.lang]
        set_routes = views[s.lang]
        groups = mode_groups(
            [view for view in set_routes if view.state != "expired"], locale
        )
        page(
            s,
            "",
            "index.html",
            agencies=agencies_by_routes(texts[s.lang]),
            routes=set_routes,
            groups=groups,
            expired=[view for view in set_routes if view.state == "expired"],
            modes=modes_label([view.mode for view in set_routes], locale),
            modes_lower=modes_text([view.mode for view in set_routes], locale),
            system_map=maps and any(group.routes for group in groups),
            valid=date_range(start, end, locale, "valid"),
            feed_info_valid=feed_info_range(feed, locale),
            unknown_languages=languages.unknown,
            feed=feed_view(site.catalog, locale, built=True, sha256=digest[1])
            if site.catalog
            else None,
            overlaps=overlaps_view(site.overlaps, locale),
            fares_page=fares_page,
            jsonld=[
                breadcrumbs(common["trail"]),
                *place_jsonld(place, common["place"], common["base_url"]),
            ],
        )
        if fares_page:
            fares_url = f"{common['base_url']}fares/"
            page(
                s,
                "fares/",
                "fares.html",
                sections=fare_sections(
                    all_fares, locale, read.fares.timeframes, site.time_format
                )
                if all_fares
                else (),
                transfers=sorted(
                    transfer_text(rule, locale) for rule in all_fares.transfers
                )
                if all_fares
                else [],
                fares_error=fares_error,
                jsonld=[
                    breadcrumbs(
                        [*common["trail"], (t(locale, "fares.heading"), fares_url)]
                    )
                ],
            )

    groups = mode_groups(
        [view for view in routes if view.state != "expired"], sets[0].locale
    )
    shown = [view.slug for group in groups for view in group.routes]
    trips_by_route: dict[str, list[str]] = {}
    for trip in feed.trips.values():
        trips_by_route.setdefault(trip.route_id, []).append(trip.trip_id)
    served = Counter(
        stop_id
        for rt in timetables
        for stop_id in {row.stop_id for table in rt.tables for row in table.rows}
    )
    for i, rt in enumerate(timetables):
        slug = routes[i].slug
        numbers = stop_numbers(rt.tables)
        bookings = booking_marks(rt.tables)
        # The route map per feed text, shared by sets whose text is the same.
        route_maps: dict[int, str] = {}
        fares = routes_fares[i]
        # Live-times data is fetched only once a timetable is on screen, so
        # it adds nothing to the page itself. Without trip updates it still
        # marks each running trip's next stop from the schedule. One file
        # serves every page set: it holds no text.
        live = bool(updates) or not rt.state
        for s in sets:
            locale = s.locale
            common = commons[s.lang]
            view = views[s.lang][i]
            text = texts[s.lang]
            tables = set_tables[s.lang][i]
            if maps and id(text) not in route_maps:
                data = route_data(
                    text,
                    tables,
                    numbers,
                    view.colors,
                    {"url": vehicles, **route_ids(read, rt.tables)}
                    if vehicles
                    else None,
                    served,
                )
                route_maps[id(text)] = (
                    script_json(data) if data and data["lines"] else ""
                )
            days = day_views(
                text,
                tables,
                site.time_format,
                common["marked"],
                locale,
                by_route[s.lang],
                today,
                fare_badges(fares, locale),
                bookings,
            )
            if live and not s.prefix:
                files[f"{slug}/{LIVE_FILE}"] = compact_json(
                    {
                        "url": updates,
                        "tz": feed.timezone(rt.route),
                        "fmt": site.time_format,
                        "tables": [table.live for day in days for table in day.tables],
                        "parents": stop_parents(feed, rt.tables),
                        **live_services(
                            feed,
                            trips_by_route.get(rt.route.route_id, []),
                            [table.trip_ids for day in days for table in day.tables],
                        ),
                    }
                ).encode()
            route_url = f"{common['base_url']}{slug}/"
            page(
                s,
                f"{slug}/",
                "route.html",
                route=view,
                fares=fare_lines(
                    fares, locale, read.fares.timeframes, site.time_format
                ),
                fare_charts=fare_charts(fares, locale),
                overlaps=route_overlaps(site.overlaps, route_agencies(feed, rt.route)),
                fares_page=fares_page,
                fares_error=fares_error,
                booking=booking_views(text, bookings, site.time_format, locale),
                fares_by=(
                    t(locale, "fare.by_zone")
                    if isinstance(fares, ZoneFares) and fares.zones
                    else t(locale, "fare.by_stop")
                ),
                map=route_maps.get(id(text), ""),
                live=(f"../../{slug}/{LIVE_FILE}" if s.prefix else LIVE_FILE)
                if live
                else "",
                days=days,
                jsonld=[
                    breadcrumbs([*common["trail"], (view.title, route_url)]),
                    *place_jsonld(place, common["place"], route_url),
                ],
            )
    # map.json is shared by the page sets: its text is the default set's.
    by_slug = {
        view.slug: (view, tables)
        for view, tables in zip(routes, set_tables[sets[0].lang], strict=True)
    }
    if maps and shown:
        files["map.json"] = json.dumps(
            system_data(
                texts[sets[0].lang],
                [
                    SystemRoute(
                        slug=view.slug,
                        badge=view.badge,
                        title=view.title,
                        colors=view.colors,
                        mode=view.mode,
                        tables=tables,
                        route_ids=route_ids(read, tables)["routes"],
                    )
                    for view, tables in (by_slug[slug] for slug in shown)
                ],
                vehicles,
            ),
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    download_url = commons[sets[0].lang]["source"]["download_url"]
    titles = {
        s.lang: with_locality(commons[s.lang]["site_title"], site.locality)
        for s in sets
    }
    other_titles = {
        lang: title for lang, title in titles.items() if title != titles[sets[0].lang]
    }
    files["site.json"] = summary_json(
        slug=site.slug,
        title=titles[sets[0].lang],
        # The page sets' languages, the default first.
        languages=[s.lang for s in sets],
        # The title in each other language whose text differs, when any does.
        **({"titles": other_titles} if other_titles else {}),
        source=source_label(download_url) if download_url else "",
        routes=len(routes),
        expired_routes=len(expired),
        valid_from=start.isoformat() if start else None,
        valid_through=end.isoformat() if end else None,
        generated=today.isoformat(),
        # The catalog's country, else the one the stops are in.
        country_code=(site.catalog and site.catalog.country_code)
        or (place and place.country_code),
        country=(site.catalog and site.catalog.country) or (place and place.country),
        subdivision=site.catalog and site.catalog.subdivision,
        # Only for a feed within one region; a national network's center
        # would place it in whichever region that falls in.
        lat=place.lat if place and place.region else None,
        lon=place.lon if place and place.region else None,
        noindex=sorted(noindex),
    )
    files["content.json"] = summary_json(
        **feed_facts(source, read, site.routes != RouteFilter(), stations, digest),
        # Why the feed's fares could not be read, when they could not.
        **({"faresError": fares_error} if fares_error else {}),
    )
    files["style.css"] = asset("style.css")
    files["map.js"] = asset("map.js")
    files["logo.svg"] = asset("logo.svg")
    return files


# JSON safe to put inside a <script> element.
script_json = compact_json


# A route's live-times data, next to its page.
LIVE_FILE = "live.json"

# Plural text map.js shows, by i18n key: forms by CLDR category.
MAP_PLURALS = ("map.live",)
# Text map.js shows, by i18n key.
MAP_TEXT = (
    "map.in_timetable",
    "map.vehicle",
    "map.to",
    "map.stopped_at",
    "map.arriving_at",
    "map.next_stop",
    "map.position_ago",
    "map.open_timetable",
    "map.updated",
    "rt.on_time",
    "rt.skipped",
    "rt.canceled",
    "index.load_error",
)


def map_text(locale: Locale) -> Markup:
    """map.js's strings, as JSON for a <script> element."""
    return script_json(
        {
            **{key: message(locale, key) for key in MAP_TEXT},
            **{key: plural_forms(locale, key) for key in MAP_PLURALS},
        }
    )


# Plural text root.js shows, by i18n key: forms by CLDR category.
ROOT_PLURALS = ("root.agencies", "root.feeds")
# Text root.js shows, by i18n key.
ROOT_TEXT = (
    "root.map_count",
    "root.none_found",
    "root.no_feeds",
    "root.loading",
    "root.load_error",
    "root.modified",
    *(f"state.{state}" for state in STATES),
    *(f"role.{role}" for role in RT_ROLES),
    *(f"role.chip_{state}" for state in STATES),
)


def root_text(locale: Locale) -> Markup:
    """root.js's strings, as JSON for a <script> element."""
    return script_json(
        {
            **{key: message(locale, key) for key in ROOT_TEXT},
            **{key: plural_forms(locale, key) for key in ROOT_PLURALS},
        }
    )


# Trip ids listed for realtime matching up to this many per route.
MAX_RT_TRIPS = 400


def stop_parents(feed: Feed, tables: list[Timetable]) -> dict[str, str]:
    """Stop id -> the station the timetable rows use, for the route's stops
    inside a station; realtime feeds name the platform."""
    trips = {c.trip_id for t in tables for c in t.columns}
    return {
        st.stop_id: station
        for trip in sorted(trips)
        for st in feed.stop_times.get(trip, ())
        if (station := feed.station(st.stop_id)) != st.stop_id
    }


def live_services(
    feed: Feed, trip_ids: list[str], tables: list[tuple[str, ...]]
) -> dict[str, object]:
    """For tables given by their columns' trip ids: each column's indexes into
    "services" (none for frequency columns), and those services as
    `service_rule`s, the dates the column runs on, unchanged by the build date."""
    services = key_services(feed, trip_ids, {t for trips in tables for t in trips if t})
    index: dict[str, int] = {}
    svc = [
        [
            [index.setdefault(sid, len(index)) for sid in services.get(trip, ())]
            for trip in trips
        ]
        for trips in tables
    ]
    return {"svc": svc, "services": [service_rule(feed, sid) for sid in index]}


def route_ids(feed: Feed, tables: list[Timetable]) -> dict[str, list[str]]:
    """The feed's route ids behind a (maybe merged) route, and its trip ids
    when few enough, for matching vehicles to it."""
    trips = list(dict.fromkeys(c.trip_id for t in tables for c in t.columns))
    return {
        "routes": sorted({feed.trips[t].route_id for t in trips if t in feed.trips}),
        "trips": trips if len(trips) <= MAX_RT_TRIPS else [],
    }


def data_source(feed: Feed, site: Site) -> dict[str, object]:
    """Who published the feed, where it was downloaded, its license and catalog
    pages, for the footer."""
    info = feed.feed_info
    agency = next(iter(agencies_by_routes(feed)), None)
    publisher = (info and tidy(info.publisher_name)) or (agency and tidy(agency.name))
    publisher_url = (info and info.publisher_url) or (agency and agency.url)
    try:
        download_url = site.download_url()
    except LookupError:
        download_url = ""
    return {
        "publisher": publisher or site.slug,
        "publisher_url": publisher_url or "",
        "download_url": download_url,
        "licenses": site.licenses(),
        "catalog_links": site.catalog.catalog_links if site.catalog else (),
    }


def summary_json(**fields: object) -> bytes:
    """A site's summary for the root index, as site.json."""
    return json.dumps(fields, indent=2).encode() + b"\n"


def unbuilt_summary(site: Site) -> dict:
    """The summary of a site with no build, from its config and catalog entry."""
    catalog = site.catalog
    try:
        source = source_label(site.download_url())
    except LookupError:
        source = ""
    return {
        "slug": site.slug,
        "title": with_locality(
            site.title or (catalog and tidy(catalog.name)) or site.slug, site.locality
        ),
        "source": source,
        "country_code": catalog and catalog.country_code,
        "country": catalog and catalog.country,
        "subdivision": catalog and catalog.subdivision,
    }


# A feed down this long keeps its page but out of the index.
NOINDEX_DOWN_DAYS = 90


def feed_indexable(site: Site, today: date) -> bool:
    """Indexed, named, and not long down: thin or dead pages stay out."""
    feed = site.catalog
    if not site.index or feed is None or not feed.name.strip():
        return False
    since = feed.since and date.fromisoformat(feed.since[:10])
    return not (
        feed.state == "down" and since and (today - since).days > NOINDEX_DOWN_DAYS
    )


def build_feed_page(
    site: Site, today: date | None = None, listed: bool = False
) -> dict[str, bytes]:
    """The files of a site with no timetables: its catalog feed's page and
    summary, which the root lists only when `listed` (a site that has not
    built yet, not a feed the catalog filter skips). No I/O."""
    if site.catalog is None:
        raise ValueError(f"{site.slug} has no catalog feed")
    today = today or date.today()
    feed = site.catalog
    # feeds.json has no feed languages, so English unless the site pins one.
    locale = site.locale or FALLBACK
    view = feed_view(feed, locale)
    title = with_locality(tidy(feed.name) or site.slug, site.locality)
    place = place_line(feed)
    base_url = f"{BASE_URL}/{site.slug}/"
    site_place = f"{title}, {place}" if place else title
    description = t(
        "en",
        "feed.description",
        name=site_place,
        kinds=view.kinds,
        state=view.state_text,
    )
    indexed = feed_indexable(site, today)
    trail = [(ROOT_TITLE, f"{BASE_URL}/"), (title, base_url)]
    page = render(
        "feed.html",
        locale=locale,
        site_title=title,
        site_place=site_place,
        place=place,
        base_url=base_url,
        generated=today,
        source=None,
        feed_only=True,
        feed=view,
        overlaps=overlaps_view(site.overlaps, locale),
        jsonld=[breadcrumbs(trail), dataset(feed, base_url, description)],
        root="",
        noindex=not indexed,
    )
    return {
        "index.html": page,
        "site.json": summary_json(
            **unbuilt_summary(site),
            feed_only=not listed,
            generated=today.isoformat(),
            noindex=[] if indexed else ["index.html"],
        ),
        "style.css": asset("style.css"),
        "logo.svg": asset("logo.svg"),
    }


SitemapUrl = tuple[str, str, list[tuple[str, str]]]


def sitemap_urls(
    pages: dict[str, dict[str, str]],
    noindex: dict[str, set[str]] | None = None,
    languages: dict[str, list[str]] | None = None,
) -> list[SitemapUrl]:
    """(url, lastmod, alternates) for each site's pages, from {slug: {path:
    YYYY-MM-DD}}, leaving out the paths in each site's `noindex`; see
    `site_sitemap` for alternates."""
    noindex = noindex or {}
    languages = languages or {}
    return [
        url
        for slug in sorted(pages)
        for url in site_sitemap(
            slug, pages[slug], noindex.get(slug, set()), languages.get(slug) or []
        )
    ]


def site_sitemap(
    slug: str, pages: dict[str, str], skip: set[str], codes: list[str]
) -> list[SitemapUrl]:
    """A site's sitemap entries, its index first. A page's alternates are
    (hreflang, url) of the same page in each language of `codes` (the default
    first, at the site root; the others under `<lang>/`) plus `x-default`,
    when more than one of them is listed."""
    prefixes = {f"{code.lower()}/": code for code in codes[1:]}
    listed = [
        path
        for path in pages
        if path not in skip and (path == "index.html" or path.endswith("/index.html"))
    ]
    same: dict[str, dict[str, str]] = {}
    in_set = {}
    for path in listed:
        head = path.split("/", 1)[0] + "/"
        if head in prefixes:
            code, base = prefixes[head], path[len(head) :]
        else:
            code, base = (codes[0] if codes else ""), path
        same.setdefault(base, {})[code] = path
        in_set[path] = base
    base_url = f"{BASE_URL}/{slug}/"
    urls = []
    for path in sorted(listed, key=lambda p: (p != "index.html", p)):
        versions = same[in_set[path]]
        alternates = []
        if len(versions) > 1:
            alternates = [
                (code, base_url + versions[code].removesuffix("index.html"))
                for code in codes
                if code in versions
            ]
            if codes[0] in versions:
                default = versions[codes[0]].removesuffix("index.html")
                alternates.append(("x-default", base_url + default))
        urls.append(
            (base_url + path.removesuffix("index.html"), pages[path], alternates)
        )
    return urls


OTHER = "other"
# A site whose timetables end within this many days is marked as expiring.
EXPIRING_DAYS = 14


def country_key(summary: dict) -> str:
    """The lowercase country code a site is listed under."""
    return (summary.get("country_code") or OTHER).lower()


def site_region(key: str, site: dict) -> Region | None:
    """The region of country `key` a site is in: the one holding its point,
    else the one its catalog subdivision names by name or ISO code suffix
    (`Oregon` or `OR` for US-OR)."""
    if site.get("lat") is not None and site.get("lon") is not None:
        found = region_of(key, site["lat"], site["lon"])
        if found:
            return found
    name = region_key(site.get("subdivision") or "")
    if not name:
        return None
    for region in regions(key):
        if name in (region_key(region.name), region_key(region.id.partition("-")[2])):
            return region
    return None


# Words that say what kind of region a name is, not which one.
REGION_WORDS = re.compile(
    r"\b(prefecture|province|region|state|county|autonomous|community|of|the)\b"
)


def region_key(name: str) -> str:
    """A region name romanized and casefolded, without words like Prefecture,
    so `Aomori Prefecture` matches `Aomori` and `Ile-de-France` `Île-de-France`."""
    folded = REGION_WORDS.sub(" ", anyascii(name).casefold())
    return " ".join(re.findall(r"[a-z0-9]+", folded))


def subdivision_groups(key: str, members: list[dict]) -> list[dict]:
    """A country's sites as `{id, name, sites}` sections: by region, else by
    catalog subdivision (with no id), unnamed last."""
    groups: dict[str, dict] = {}
    for site in members:
        region = site_region(key, site)
        if region:
            group_id, name = region.id, region.name
        else:
            group_id, name = "", site.get("subdivision") or ""
        group = groups.setdefault(
            group_id or f"name:{name}", {"id": group_id, "name": name, "sites": []}
        )
        group["sites"].append(site)
    return sorted(groups.values(), key=lambda g: (not g["name"], g["name"].casefold()))


def country_name(key: str, catalog_name: str | None, locale: Locale) -> str:
    """Country `key` named in `locale` (CLDR), else the catalog's name, else
    its code."""
    if key == OTHER:
        return t(locale, "country.other")
    names = BabelLocale.parse(locale).territories
    return names.get(key.upper()) or catalog_name or key.upper()


def country_groups(sites: list[dict], locale: Locale = "en") -> list[dict]:
    """Countries by name, each with its sites grouped by subdivision."""
    countries: dict[str, list[dict]] = {}
    for site in sites:
        countries.setdefault(country_key(site), []).append(site)
    groups = []
    for key, members in countries.items():
        groups.append(
            {
                "key": key,
                "name": country_name(key, members[0].get("country"), locale),
                "sites": members,
                "subdivisions": subdivision_groups(key, members),
            }
        )
    # Other last.
    return sorted(groups, key=lambda g: (g["key"] == OTHER, g["name"].casefold()))


# Outcomes that are the feed's own fault, not a passing download problem.
UNUSABLE = frozenset({"not_zip", "missing_files", "parse_error", "empty"})
# Outcomes with their own `reason.<outcome>` text; others are `reason.error`.
REASONS = frozenset(
    {"not_zip", "missing_files", "parse_error", "empty", "timeout", "memory"}
)
# Order of statuses on a country page.
STATUS_ORDER = {"ok": 0, "expiring": 0, "stale": 0, "expired": 1, "unavailable": 2}


def site_status(summary: dict, outcome: str | None, today: date) -> str:
    """From a site.json summary and the outcome of its last download and
    build: unavailable (never built, or the feed itself is unusable), expired
    (its service has ended), stale (the last update failed and an older build
    is served), expiring (service ends within EXPIRING_DAYS) or ok."""
    through = summary.get("valid_through")
    if outcome in UNUSABLE or not summary.get("routes") or not through:
        return "unavailable"
    days_left = (date.fromisoformat(through) - today).days
    if days_left < 0:
        return "expired"
    if outcome and outcome != "ok":
        return "stale"
    return "expiring" if days_left < EXPIRING_DAYS else "ok"


def unavailable_reason(outcome: str | None, detail: str, locale: Locale = "en") -> str:
    """Why a site is unavailable, for its row on a country page."""
    if outcome is None:
        return t(locale, "reason.not_built")
    if outcome == "ok":
        return t(locale, "reason.no_routes")
    if outcome == "http_error" and detail:
        return t(locale, "reason.http_error_detail", detail=detail)
    if outcome == "http_error" or outcome in REASONS:
        return t(locale, f"reason.{outcome}")
    return t(locale, "reason.error")


def site_row(summary: dict, outcome: dict, today: date, locale: Locale = "en") -> dict:
    """A site.json summary plus what its country page row shows."""
    status = site_status(summary, outcome.get("outcome"), today)
    start, end = summary.get("valid_from"), summary.get("valid_through")
    # Defaults for summaries written before these fields existed.
    return (
        {"source": "", "routes": 0, "expired_routes": 0}
        | summary
        | {
            "status": status,
            "dates": date_range(
                start and date.fromisoformat(start),
                end and date.fromisoformat(end),
                locale,
            ),
            "reason": unavailable_reason(
                outcome.get("outcome"), outcome.get("detail", ""), locale
            )
            if status == "unavailable"
            else "",
            # A site that never built has a page once its feed page is
            # published.
            "linked": status != "unavailable" or "generated" in summary,
        }
    )


def with_counts(doc: dict, key: str, counts: dict[str, int]) -> bytes:
    """GeoJSON `doc` with each feature's `count` of sites, by its `key`
    property, keeping the doc's other fields (its name, license, source)."""
    features = [
        f | {"properties": props | {"count": counts.get(props[key], 0)}}
        for f in doc["features"]
        for props in (f["properties"],)
    ]
    return json.dumps(
        doc | {"features": features},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()


# search.json fields root.js reads.
SEARCH_FIELDS = ("n", "s", "a", "p", "pt", "cc", "st", "m")


def search_index(
    catalog: dict | None, slugs: dict[str, str], countries: list[dict]
) -> bytes:
    """search.json for root.js: data.gtfs.zone's search.json entries with `i`
    the feed's slug ("" with none), each URL cut to its host, `rs` without
    the schedule, the fields root.js does not read dropped, and `lg` the
    other page-set languages of a site with several."""
    langs = {
        site["slug"]: codes[1:]
        for place in countries
        for group in place["subdivisions"]
        for site in group["sites"]
        if len(codes := site.get("languages") or []) > 1
    }
    feeds = []
    for entry in (catalog or {}).get("feeds", []):
        slug = slugs.get(entry["i"], "")
        feed = {"i": slug} | {k: entry[k] for k in SEARCH_FIELDS if entry.get(k)}
        feed["rs"] = {
            role: state
            for role, state in entry.get("rs", {}).items()
            if role != "scheduled"
        }
        feed["u"] = {
            role: sorted({f"https://{host}" for url in urls if (host := url_host(url))})
            for role, urls in entry.get("u", {}).items()
        }
        if slug in langs:
            feed["lg"] = langs[slug]
        feeds.append(feed)
    return json.dumps(
        {"feeds": feeds}, separators=(",", ":"), ensure_ascii=False
    ).encode()


def url_host(url: str) -> str:
    """A URL's host, without credentials or port; "" when it has none."""
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


# The root's languages, the default first: every UI catalog.
ROOT_LOCALES = (FALLBACK, *sorted(UI_LOCALES - {FALLBACK}))


def root_path(locale: str) -> str:
    """The root page's directory in `locale`, from the bucket root."""
    return "" if locale == FALLBACK else f"lang/{locale}/"


def country_languages(sites: list[dict]) -> list[str]:
    """The UI languages of a country's sites, the one most sites offer first;
    English when none has a catalog."""
    counts = Counter(
        code
        for site in sites
        for code in site.get("languages") or ()
        if code in UI_LOCALES
    )
    ranked = sorted(counts, key=lambda code: (-counts[code], code != FALLBACK, code))
    return ranked or [FALLBACK]


def country_path(key: str, locale: str, codes: list[str]) -> str:
    """Country `key`'s page directory in `locale` (from the bucket root), its
    default when it has no page in `locale`."""
    base = f"countries/{key}/"
    return f"{base}{locale}/" if locale in codes[1:] else base


def site_path(site: dict, locale: str) -> str:
    """A site's page set in `locale` (from the bucket root), its default set
    when it has none in `locale`."""
    codes = site.get("languages") or []
    return f"{site['slug']}/" + (f"{locale.lower()}/" if locale in codes[1:] else "")


def localized(site: dict, locale: str) -> dict:
    """A site row with its title and link in `locale` when it has that page
    set."""
    return site | {
        "feed_title": site["title"],
        "title": (site.get("titles") or {}).get(locale, site["title"]),
        "href": site_path(site, locale),
    }


def picker(paths: dict[str, str], root: str, locale: str) -> list[dict]:
    """The picker and hreflang links of a page at `paths` ({language: path from
    the bucket root}), `href` relative from the page by `root`. Empty for one
    language."""
    if len(paths) < 2:
        return []
    return [
        {
            "code": code,
            "name": language_name(code),
            "href": f"{root}{path}" or "./",
            "url": f"{BASE_URL}/{path}",
            "current": code == locale,
        }
        for code, path in paths.items()
    ]


def sitemap_alternates(paths: dict[str, str]) -> list[tuple[str, str]]:
    """(hreflang, url) for each of `paths` plus x-default (the first), when
    there are several."""
    if len(paths) < 2:
        return []
    urls = [(code, f"{BASE_URL}/{path}") for code, path in paths.items()]
    return [*urls, ("x-default", urls[0][1])]


def build_root(
    summaries: list[dict],
    today: date,
    pages: dict[str, dict[str, str]] | None = None,
    indexnow_key: str | None = None,
    outcomes: dict[str, dict] | None = None,
    catalog: dict | None = None,
    slugs: dict[str, str] | None = None,
    sources: dict | None = None,
) -> dict[str, bytes]:
    """The bucket root: an index of countries in each UI language (English at
    `/`, others at `/lang/<lang>/`), a page per country listing its sites in
    each of its sites' UI languages (the most offered at `/countries/<cc>/`,
    others under it at `<lang>/`), a sitemap index over one sitemap per shard,
    robots.txt and the error page. `summaries` are the sites' site.json
    contents (a site never built has only its slug, title, source and place),
    `pages` each site's page paths with the date they last changed and
    `outcomes` each site's last download and build outcome and detail,
    `catalog` data.gtfs.zone's search.json and `slugs` each feed id's slug,
    for the root search, and `sources` feed-catalog's source-stats.json for
    the sources page in each UI language (`sources/`, others at
    `lang/<lang>/sources/`, noindex), linked from the root. A summary's
    `noindex` paths stay out of the sitemap, and a feed page's summary
    (`feed_only`) is not listed. No I/O."""
    outcomes = outcomes or {}
    by_locale: dict[str, list[dict]] = {}
    for locale in ROOT_LOCALES:
        sites = sorted(
            (
                localized(
                    site_row(s, outcomes.get(s["slug"], {}), today, locale), locale
                )
                for s in summaries
                if not s.get("feed_only")
            ),
            key=lambda row: (STATUS_ORDER[row["status"]], row["title"].casefold()),
        )
        by_locale[locale] = country_groups(sites, locale)
    countries = by_locale[FALLBACK]
    codes = {group["key"]: country_languages(group["sites"]) for group in countries}
    root_paths = {locale: root_path(locale) for locale in ROOT_LOCALES}
    sources_paths = {locale: f"{path}sources/" for locale, path in root_paths.items()}
    version_text = version("gtfs-zone-timetable-sites")
    website = {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": ROOT_TITLE,
        "url": f"{BASE_URL}/",
    }
    files = {
        "error.html": render(
            "error.html",
            locale=FALLBACK,
            root_text=root_text(FALLBACK),
            site_title=ROOT_TITLE,
            base_url=f"{BASE_URL}/",
            generated=today,
            version=version_text,
            root="/",
        ),
        "style.css": asset("style.css"),
        "logo.svg": asset("logo.svg"),
        "root.js": asset("root.js"),
        "search.json": search_index(catalog, slugs or {}, countries),
        "world.json": with_counts(
            world(), "key", {c["key"]: len(c["sites"]) for c in countries}
        ),
    }
    for locale, groups in by_locale.items():
        path = root_paths[locale]
        root = "../" * path.count("/")
        common = {
            "locale": locale,
            "root_text": root_text(locale),
            "site_title": ROOT_TITLE,
            "generated": today,
            "version": version_text,
        }
        files[f"{path}index.html"] = render(
            "root.html",
            **common,
            base_url=f"{BASE_URL}/{path}",
            countries=[
                g | {"href": country_path(g["key"], locale, codes[g["key"]])}
                for g in groups
            ],
            total=sum(len(g["sites"]) for g in groups),
            jsonld=website | {"url": f"{BASE_URL}/{path}"},
            languages=picker(root_paths, root, locale),
            sources=sources is not None and sources_paths[locale],
            root=root,
        )
        if sources is not None:
            path = sources_paths[locale]
            root = "../" * path.count("/")
            url = f"{BASE_URL}/{path}"
            files[f"{path}index.html"] = render(
                "sources.html",
                **common,
                base_url=url,
                stats=sources_page(sources),
                noindex=True,
                jsonld=breadcrumbs(
                    [
                        (ROOT_TITLE, f"{BASE_URL}/{root_paths[locale]}"),
                        (t(locale, "sources.title"), url),
                    ]
                ),
                languages=picker(sources_paths, root, locale),
                home=root_paths[locale],
                root=root,
            )
        for group in groups:
            key = group["key"]
            if locale not in codes[key]:
                continue
            paths = {code: country_path(key, code, codes[key]) for code in codes[key]}
            path = paths[locale]
            root = "../" * path.count("/")
            url = f"{BASE_URL}/{path}"
            boundaries = country(key) if key != OTHER else None
            files[f"{path}index.html"] = render(
                "country.html",
                **common,
                base_url=url,
                country=group,
                boundaries=boundaries
                and {k: v for k, v in boundaries.items() if k != "features"},
                regions=f"{root}countries/{key}/regions.json",
                jsonld=breadcrumbs(
                    [
                        (ROOT_TITLE, f"{BASE_URL}/{root_paths[locale]}"),
                        (group["name"], url),
                    ]
                ),
                languages=picker(paths, root, locale),
                home=root_paths[locale],
                root=root,
            )
    for group in countries:
        key = group["key"]
        boundaries = country(key) if key != OTHER else None
        if boundaries:
            files[f"countries/{key}/regions.json"] = with_counts(
                boundaries,
                "id",
                {g["id"]: len(g["sites"]) for g in group["subdivisions"] if g["id"]},
            )

    # One sitemap per shard of sites, so each stays far below 50,000 URLs.
    root_urls: list[SitemapUrl] = [
        (f"{BASE_URL}/{path}", None, sitemap_alternates(root_paths))
        for path in root_paths.values()
    ]
    for group in countries:
        key = group["key"]
        paths = {code: country_path(key, code, codes[key]) for code in codes[key]}
        root_urls += [
            (f"{BASE_URL}/{path}", None, sitemap_alternates(paths))
            for path in paths.values()
        ]
    maps = {"sitemaps/root.xml": root_urls}
    noindex = {s["slug"]: set(s.get("noindex", ())) for s in summaries}
    languages = {s["slug"]: s.get("languages") or [] for s in summaries}
    shards: dict[int, dict[str, dict[str, str]]] = {}
    for slug, site_pages in (pages or {}).items():
        shards.setdefault(shard_of(slug), {})[slug] = site_pages
    for shard, shard_pages in sorted(shards.items()):
        maps[f"sitemaps/{shard:02d}.xml"] = sitemap_urls(
            shard_pages, noindex, languages
        )
    for path, urls in maps.items():
        files[path] = render("sitemap.xml", urls=urls)
    files["sitemap.xml"] = render(
        "sitemap-index.xml",
        sitemaps=[
            (f"{BASE_URL}/{path}", max((u[1] for u in urls if u[1]), default=None))
            for path, urls in maps.items()
        ],
    )
    files["robots.txt"] = (
        f"User-agent: *\nAllow: /\nSitemap: {BASE_URL}/sitemap.xml\n"
    ).encode()
    if indexnow_key:
        files[f"{indexnow_key}.txt"] = indexnow_key.encode()
    return files


def redirect_pages(
    redirects: dict[str, str],
    pages: dict[str, dict[str, str]],
    old_pages: dict[str, set[str]] | None = None,
) -> dict[str, bytes]:
    """A page at each of an old slug's paths that sends the browser to the same
    path under the current slug, from {old slug: current slug} and each
    current site's page paths. An old slug's own page paths in `old_pages`
    that the current site lacks send the browser to its index. No I/O."""
    files = {}
    for old, new in redirects.items():
        paths = set(pages.get(new) or {"index.html"})
        for path in sorted(paths | (old_pages or {}).get(old, set())):
            target = path if path in paths else "index.html"
            url = f"{BASE_URL}/{new}/{target.removesuffix('index.html')}"
            files[f"{old}/{path}"] = render("redirect.html", url=url)
    return files

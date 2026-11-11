"""build_site: the only entry point from a GTFS zip to a site's files."""

import hashlib
import io
import json
import re
import zipfile
from dataclasses import replace
from datetime import date, timedelta
from importlib.metadata import version
from typing import NamedTuple
from urllib.parse import urlsplit

from anyascii import anyascii
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
from gtfs_zone_timetable_sites.gtfs.reader import Agency, Feed, Route, read_feed
from gtfs_zone_timetable_sites.gtfs.service import (
    day_types,
    first_service_date,
    horizon_start,
    last_service_date,
)
from gtfs_zone_timetable_sites.i18n import CATALOGS, Locale, capitalize, join_and, t
from gtfs_zone_timetable_sites.maps.geo import SystemRoute, route_data, system_data
from gtfs_zone_timetable_sites.pages import (
    breadcrumbs,
    date_range,
    day_views,
    feed_amenities,
    mode_groups,
    mode_label,
    modes_label,
    modes_text,
    place_jsonld,
    route_labels,
    route_slugs,
    route_view,
    service_range,
    stop_numbers,
    tidy,
)
from gtfs_zone_timetable_sites.places import feed_place, place_label
from gtfs_zone_timetable_sites.render import asset, compact_json, render
from gtfs_zone_timetable_sites.timetable import Timetable, route_timetables

BASE_URL = "https://sites.gtfs.zone"
LIST_URL = "https://list.gtfs.zone"
ROOT_TITLE = "sites.gtfs.zone"
# The footer's build date, as base.html marks it up.
GENERATED = re.compile(rb'<time class="generated"[^>]*>.*?</time>')
# Agencies named in a site's title before "and N others".
TITLE_AGENCIES = 3


def page_digest(body: bytes) -> str:
    """sha256 of a page without its build date, so it changes only with the
    page's content."""
    return hashlib.sha256(GENERATED.sub(b"", body)).hexdigest()


def site_feed(zip_bytes: bytes, site: Site) -> Feed:
    """The feed with only the routes the site's filter accepts."""
    return read_feed(
        zip_bytes, keep_route=lambda r: site.routes.accepts(r.route_id, r.route_type)
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


def merge_routes(feed: Feed) -> Feed:
    """The feed with routes that share agency, type, names and colors merged
    into the first by `route_order`, their trips moved onto it; e.g. a feed
    with a route per train per date."""
    keep: dict[tuple, str] = {}
    into: dict[str, str] = {}
    for route in sorted(feed.routes.values(), key=route_order):
        key = (
            route.agency_id,
            route.route_type,
            route.short_name,
            route.long_name,
            route.color,
            route.text_color,
        )
        into[route.route_id] = keep.setdefault(key, route.route_id)
    if len(keep) == len(feed.routes):
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
    )


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
    start = horizon_start(feed, today)
    days = site.horizon_days
    trips_by_route: dict[str, list[str]] = {}
    for trip in feed.trips.values():
        trips_by_route.setdefault(trip.route_id, []).append(trip.trip_id)
    result = []
    for route in sorted(feed.routes.values(), key=route_order):
        trip_ids = trips_by_route.get(route.route_id, [])
        types = day_types(feed, trip_ids, start, days, site.locale)
        tables = route_timetables(feed, route.route_id, types, site.timepoints)
        if tables:
            result.append(RouteTables(route, tables))
            continue
        services = {feed.trips[t].service_id for t in trip_ids}
        first = first_service_date(feed, services)
        last = last_service_date(feed, services)
        if first is None or last is None:
            continue
        if last < start:
            state, state_date = "expired", last
            window = max(first, last - timedelta(days=days - 1))
        elif first > start:
            state, state_date, window = "upcoming", first, first
        else:
            continue
        types = day_types(feed, trip_ids, window, days, site.locale)
        tables = route_timetables(feed, route.route_id, types, site.timepoints)
        if tables:
            result.append(RouteTables(route, tables, state, state_date))
    return result


class EmptyFeed(ValueError):
    """A zip with no route that has scheduled trips."""


def site_title(feed: Feed, site: Site) -> str:
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
        names = [*names[:TITLE_AGENCIES], t(site.locale, "others", n=rest)]
    if names:
        return join_and(names, site.locale)
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


def build_site(
    zip_bytes: bytes, site: Site, today: date | None = None
) -> dict[str, bytes]:
    """Map of path (relative to the site root) to file contents. No I/O."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        bad = archive.testzip()
    if bad is not None:
        raise ValueError(f"corrupt member in GTFS zip: {bad}")
    today = today or date.today()
    read = site_feed(zip_bytes, site)
    feed = merge_routes(read)
    timetables = site_timetables(feed, site, today)
    if not timetables:
        raise EmptyFeed("no route has scheduled trips")
    labels = route_labels(feed, [rt.route for rt in timetables])
    slugs = route_slugs([rt.route for rt in timetables], labels)
    agencies = agencies_by_routes(feed)
    base_url = f"{BASE_URL}/{site.slug}/"
    locale = site.locale
    feed_notes, marked = feed_amenities(
        [table for rt in timetables for table in rt.tables], locale
    )
    title = site_title(feed, site)
    place = feed_place(feed)
    label = place_label(place, title, site.locality)
    common = {
        "locale": locale,
        "site_title": title,
        # The title with where the feed runs, for page titles and descriptions.
        "site_place": f"{title}, {label}" if label else title,
        "place": label,
        "local": bool(label)
        and (bool(site.locality) or (place is not None and bool(place.region))),
        "base_url": base_url,
        "generated": today,
        "feed_notes": feed_notes,
        "source": data_source(feed, site),
    }

    routes = [
        route_view(
            rt.route,
            slugs[rt.route.route_id],
            rt.tables,
            labels.get(rt.route.route_id, ""),
            rt.state,
            rt.state_date,
            locale,
        )
        for rt in timetables
    ]
    common["map_text"] = map_text(locale, [view.mode for view in routes])
    start, end = service_range(feed)
    maps = site.map == "maplibre"
    groups = mode_groups([view for view in routes if view.state != "expired"], locale)
    expired = [view for view in routes if view.state == "expired"]
    vehicles = site.vehicles_url()
    updates = site.trip_updates_url()
    shown = [view.slug for group in groups for view in group.routes]
    by_slug = {
        view.slug: (view, rt) for view, rt in zip(routes, timetables, strict=True)
    }
    trail = [
        (ROOT_TITLE, f"{BASE_URL}/"),
        (with_locality(common["site_title"], site.locality), base_url),
    ]
    # Search engines skip expired routes, and the whole site when it is not
    # indexed or every route has expired.
    indexed = site.index and any(view.state != "expired" for view in routes)
    noindex = {
        f"{view.slug}/index.html"
        for view in routes
        if not indexed or view.state == "expired"
    }
    if not indexed:
        noindex.add("index.html")
    files = {
        "index.html": render(
            "index.html",
            **common,
            agencies=agencies,
            routes=routes,
            groups=groups,
            expired=expired,
            modes=modes_label([view.mode for view in routes], locale),
            modes_lower=modes_text([view.mode for view in routes], locale),
            system_map=maps and bool(shown),
            valid=date_range(start, end, locale, "valid"),
            feed_url=f"{LIST_URL}/#feed={site.feed}" if site.feed else "",
            jsonld=[breadcrumbs(trail), *place_jsonld(place, label, base_url)],
            root="",
            noindex="index.html" in noindex,
        )
    }
    for view, rt in zip(routes, timetables, strict=True):
        numbers = stop_numbers(rt.tables)
        data = (
            route_data(
                feed,
                rt.tables,
                numbers,
                view.colors,
                {"url": vehicles, **route_ids(read, rt.tables)} if vehicles else None,
            )
            if maps
            else None
        )
        route_map = script_json(data) if data and data["lines"] else ""
        days = day_views(feed, rt.tables, site.time_format, marked, locale)
        # Live-times data is fetched only once a timetable is on screen, so
        # it adds nothing to the page itself
        if updates:
            files[f"{view.slug}/{LIVE_FILE}"] = compact_json(
                {
                    "url": updates,
                    "tz": feed.timezone(rt.route),
                    "fmt": site.time_format,
                    "tables": [table.live for day in days for table in day.tables],
                    "parents": stop_parents(feed, rt.tables),
                }
            ).encode()
        files[f"{view.slug}/index.html"] = render(
            "route.html",
            **common,
            route=view,
            map=route_map,
            live=LIVE_FILE if updates else "",
            days=days,
            jsonld=[
                breadcrumbs([*trail, (view.title, f"{base_url}{view.slug}/")]),
                *place_jsonld(place, label, f"{base_url}{view.slug}/"),
            ],
            root="../",
            noindex=f"{view.slug}/index.html" in noindex,
        )
    if maps and shown:
        files["map.json"] = json.dumps(
            system_data(
                feed,
                [
                    SystemRoute(
                        slug=view.slug,
                        badge=view.badge,
                        title=view.title,
                        colors=view.colors,
                        mode=view.mode,
                        tables=rt.tables,
                        route_ids=route_ids(read, rt.tables)["routes"],
                    )
                    for view, rt in (by_slug[slug] for slug in shown)
                ],
                vehicles,
            ),
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    download_url = common["source"]["download_url"]
    files["site.json"] = summary_json(
        slug=site.slug,
        title=with_locality(common["site_title"], site.locality),
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
        **feed_facts(zip_bytes, read, site.routes != RouteFilter())
    )
    files["style.css"] = asset("style.css")
    files["map.js"] = asset("map.js")
    files["logo.svg"] = asset("logo.svg")
    return files


# JSON safe to put inside a <script> element.
script_json = compact_json


# A route's live-times data, next to its page.
LIVE_FILE = "live.json"

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
    "map.live_one",
    "map.live_other",
    "map.updated",
    "rt.on_time",
    "rt.skipped",
    "rt.canceled",
    "index.load_error",
)


def map_text(locale: Locale, modes: list[str]) -> Markup:
    """map.js's strings and mode names, as JSON for a <script> element."""
    catalog = CATALOGS[locale]
    return script_json(
        {
            **{key: catalog[key] for key in MAP_TEXT},
            "modes": {
                m: capitalize(mode_label(m, locale)) for m in dict.fromkeys(modes)
            },
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


def sitemap_urls(
    pages: dict[str, dict[str, str]], noindex: dict[str, set[str]] | None = None
) -> list[tuple[str, str]]:
    """(url, lastmod) for each site's pages, from {slug: {path: YYYY-MM-DD}},
    leaving out the paths in each site's `noindex`."""
    noindex = noindex or {}
    urls = []
    for slug in sorted(pages):
        skip = noindex.get(slug, set())
        # The site's index first, then its routes.
        for path, modified in sorted(
            pages[slug].items(), key=lambda item: (item[0] != "index.html", item[0])
        ):
            if path in skip:
                continue
            if path == "index.html" or path.endswith("/index.html"):
                urls.append(
                    (f"{BASE_URL}/{slug}/{path[: -len('index.html')]}", modified)
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


def country_groups(sites: list[dict]) -> list[dict]:
    """Countries by name, each with its sites grouped by subdivision."""
    countries: dict[str, list[dict]] = {}
    for site in sites:
        countries.setdefault(country_key(site), []).append(site)
    groups = []
    for key, members in countries.items():
        fallback = "Other" if key == OTHER else key.upper()
        groups.append(
            {
                "key": key,
                "name": members[0].get("country") or fallback,
                "sites": members,
                "subdivisions": subdivision_groups(key, members),
            }
        )
    # Other last.
    return sorted(groups, key=lambda g: (g["key"] == OTHER, g["name"].casefold()))


# Outcomes that are the feed's own fault, not a passing download problem.
UNUSABLE = frozenset({"not_zip", "missing_files", "parse_error", "empty"})
# Why a site is unavailable, by the outcome of its last download and build.
REASONS = {
    "not_zip": "Download is not a zip",
    "missing_files": "Missing required files",
    "parse_error": "Feed could not be read",
    "empty": "Feed has no scheduled trips",
    "timeout": "Download timed out",
    "memory": "Too large to build",
}
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


def unavailable_reason(outcome: str | None, detail: str) -> str:
    """Why a site is unavailable, for its row on a country page."""
    if outcome is None:
        return "Not built yet"
    if outcome == "ok":
        return "Feed has no routes"
    if outcome == "http_error":
        return f"Download failed: {detail}" if detail else "Download failed"
    return REASONS.get(outcome, "Build failed")


def site_row(summary: dict, outcome: dict, today: date) -> dict:
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
                start and date.fromisoformat(start), end and date.fromisoformat(end)
            ),
            "reason": unavailable_reason(
                outcome.get("outcome"), outcome.get("detail", "")
            )
            if status == "unavailable"
            else "",
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


def search_index(countries: list[dict]) -> bytes:
    """sites.json for root.js's search: [slug, title, source, where, status] per
    site, where being its region and country."""
    rows = [
        [
            site["slug"],
            site["title"],
            site.get("source") or "",
            ", ".join(n for n in (group["name"], place["name"]) if n),
            site["status"],
        ]
        for place in countries
        for group in place["subdivisions"]
        for site in group["sites"]
    ]
    return json.dumps(rows, separators=(",", ":"), ensure_ascii=False).encode()


def build_root(
    summaries: list[dict],
    today: date,
    pages: dict[str, dict[str, str]] | None = None,
    indexnow_key: str | None = None,
    outcomes: dict[str, dict] | None = None,
) -> dict[str, bytes]:
    """The bucket root: an index of countries, a page per country listing its
    sites, a sitemap index over one sitemap per shard, robots.txt and the error
    page. `summaries` are the sites' site.json contents (a site never built has
    only its slug, title, source and place), `pages` each site's page paths
    with the date they last changed and `outcomes` each site's last download
    and build outcome and detail. A summary's `noindex` paths stay out of the
    sitemap. No I/O."""
    outcomes = outcomes or {}
    sites = sorted(
        (site_row(s, outcomes.get(s["slug"], {}), today) for s in summaries),
        key=lambda row: (STATUS_ORDER[row["status"]], row["title"].casefold()),
    )
    countries = country_groups(sites)
    common = {
        "locale": "en",
        "site_title": ROOT_TITLE,
        "base_url": f"{BASE_URL}/",
        "generated": today,
        "version": version("gtfs-zone-timetable-sites"),
    }
    website = {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": ROOT_TITLE,
        "url": f"{BASE_URL}/",
    }
    files = {
        "index.html": render(
            "root.html",
            **common,
            countries=countries,
            total=len(sites),
            jsonld=website,
            root="",
        ),
        "error.html": render("error.html", **common, root="/"),
        "style.css": asset("style.css"),
        "logo.svg": asset("logo.svg"),
        "root.js": asset("root.js"),
        "sites.json": search_index(countries),
        "world.json": with_counts(
            world(), "key", {c["key"]: len(c["sites"]) for c in countries}
        ),
    }
    for group in countries:
        key = group["key"]
        url = f"{BASE_URL}/countries/{key}/"
        boundaries = country(key) if key != OTHER else None
        if boundaries:
            files[f"countries/{key}/regions.json"] = with_counts(
                boundaries,
                "id",
                {g["id"]: len(g["sites"]) for g in group["subdivisions"] if g["id"]},
            )
        files[f"countries/{key}/index.html"] = render(
            "country.html",
            **(common | {"base_url": url}),
            country=group,
            boundaries=boundaries
            and {k: v for k, v in boundaries.items() if k != "features"},
            jsonld=breadcrumbs([(ROOT_TITLE, f"{BASE_URL}/"), (group["name"], url)]),
            root="../../",
        )

    # One sitemap per shard of sites, so each stays far below 50,000 URLs.
    root_urls = [(f"{BASE_URL}/", None)] + [
        (f"{BASE_URL}/countries/{c['key']}/", None) for c in countries
    ]
    maps = {"sitemaps/root.xml": root_urls}
    noindex = {s["slug"]: set(s.get("noindex", ())) for s in summaries}
    shards: dict[int, dict[str, dict[str, str]]] = {}
    for slug, site_pages in (pages or {}).items():
        shards.setdefault(shard_of(slug), {})[slug] = site_pages
    for shard, shard_pages in sorted(shards.items()):
        maps[f"sitemaps/{shard:02d}.xml"] = sitemap_urls(shard_pages, noindex)
    for path, urls in maps.items():
        files[path] = render("sitemap.xml", urls=urls)
    files["sitemap.xml"] = render(
        "sitemap-index.xml",
        sitemaps=[
            (f"{BASE_URL}/{path}", max((m for _, m in urls if m), default=None))
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

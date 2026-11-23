"""A catalog feed's page: its state, URLs and their last checks, catalog rows,
license and what its schedule held, ported from feed-catalog's `pages.py`.

Nothing here reads the GTFS zip: every value comes from feeds.json, its rows
from sources.json and their checks from status.json, so a feed that never
built gets the same page as one that did. Values are untrusted catalog text,
escaped by the templates.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import quote, urlencode

from babel.numbers import format_decimal

from gtfs_zone_timetable_sites.config import CatalogFeed, CatalogRow
from gtfs_zone_timetable_sites.i18n import Locale, long_date, t

EDITOR_BASE = "https://edit.gtfs.zone"
VIEWER_BASE = "https://viz.rt.gtfs.zone"
TRANSITLAND_FEED_BASE = "https://www.transit.land/feeds/"
MOBILITYDATABASE_FEED_BASE = "https://mobilitydatabase.org/feeds/"

STATIC_ROLES = ("scheduled",)
RT_ROLES = ("vehicles", "trip_updates", "alerts", "realtime")
# A feed's or a role's states, each with `state.<state>` and `role.chip_<state>` text.
STATES = ("up", "partial", "down", "unknown")
ROLE_SHORT = {
    "scheduled": "GTFS",
    "vehicles": "VP",
    "trip_updates": "TU",
    "alerts": "SA",
    "realtime": "RT",
}
ROLE_FORMATS = {
    "scheduled": "application/zip",
    "vehicles": "application/x-protobuf",
    "trip_updates": "application/x-protobuf",
    "alerts": "application/x-protobuf",
    "realtime": "application/x-protobuf",
}
CATALOG_NAMES = {
    "transitland": "Transitland",
    "mobilitydatabase": "Mobility Database",
    "gtfszone": "rt.gtfs.zone",
    "ntd": "National Transit Database",
}
# feed-catalog's artifacts.ATTRIBUTION, by catalog.
ATTRIBUTION = {
    "transitland": (
        "Transitland Atlas",
        "https://github.com/transitland/transitland-atlas",
        "CC BY 4.0",
        "https://creativecommons.org/licenses/by/4.0/",
    ),
    "mobilitydatabase": (
        "Mobility Database",
        "https://mobilitydatabase.org",
        "CC0 1.0",
        "https://creativecommons.org/publicdomain/zero/1.0/",
    ),
    "ntd": (
        "National Transit Database (FTA)",
        "https://data.transportation.gov/d/2u7n-ub22",
        "Public Domain U.S. Government",
        "https://www.usa.gov/government-works",
    ),
}
# Content report outcomes with a label; others read as "error".
OUTCOMES = frozenset(
    {
        "ok",
        "not_zip",
        "missing_files",
        "parse_error",
        "http_error",
        "timeout",
        "memory",
        "empty",
        "error",
    }
)
# Check error classes with a label; others read as "other".
CHECK_ERRORS = frozenset({"dns", "refused", "timeout", "tls", "relative"})


@dataclass(frozen=True)
class UrlView:
    url: str
    absolute: bool
    # The last check of the row this URL came from.
    state: str
    check: str
    redirect: str


@dataclass(frozen=True)
class RoleView:
    role: str
    label: str
    short: str
    state: str
    chip_title: str
    auth: bool
    urls: tuple[UrlView, ...]


@dataclass(frozen=True)
class RowView:
    catalog: str
    name: str
    catalog_id: str
    href: str
    operator: str
    license_url: str


@dataclass(frozen=True)
class Credit:
    name: str
    url: str
    license: str
    license_url: str


@dataclass(frozen=True)
class FeedView:
    feed_id: str
    name: str
    subtitle: str
    kinds: str
    state: str
    state_label: str
    state_text: str
    roles: tuple[RoleView, ...]
    # (label, value) pairs of what the schedule's last download held.
    facts: tuple[tuple[str, str], ...]
    rows: tuple[RowView, ...]
    licenses: tuple[str, ...]
    credits: tuple[Credit, ...]
    editor_url: str
    viewer_url: str
    # Why there are no timetables, for a feed without a site.
    reason: str


def absolute(url: str) -> bool:
    return url.startswith(("http://", "https://"))


def day(value: str | None) -> date | None:
    """The date of an ISO date or datetime."""
    return date.fromisoformat(value[:10]) if value else None


def has_realtime(feed: CatalogFeed) -> bool:
    return any(feed.urls.get(role) for role in RT_ROLES)


def kinds(feed: CatalogFeed, locale: Locale) -> str:
    """GTFS, GTFS Realtime, or both."""
    if feed.urls.get("scheduled") and has_realtime(feed):
        return t(locale, "feed.kind_both")
    if has_realtime(feed):
        return t(locale, "feed.kind_rt")
    return t(locale, "feed.kind_static")


def state_text(feed: CatalogFeed, locale: Locale) -> str:
    """'Down since Sep 23, 2026', 'Down, never seen up', or just the state."""
    label = t(locale, f"state.{feed.state}")
    if feed.never_up and feed.state == "down":
        return t(locale, "state.never_up", state=label)
    since = day(feed.since)
    if since and feed.state != "unknown":
        return t(locale, "state.since", state=label, date=long_date(since, locale))
    return label


def catalog_url(row: CatalogRow) -> str:
    """The row's page in its catalog, as feed-catalog's `catalog_url`."""
    if row.catalog == "transitland":
        return f"{TRANSITLAND_FEED_BASE}{row.feed_id}"
    if row.catalog == "mobilitydatabase":
        kind = "gtfs_rt" if row.kind == "rt" else "gtfs"
        return f"{MOBILITYDATABASE_FEED_BASE}{kind}/{row.feed_id}"
    return ""


def editor_url(feed: CatalogFeed) -> str:
    """The schedule in edit.gtfs.zone, as feed-list's app-links.ts."""
    scheduled = next(iter(feed.urls.get("scheduled", ())), "")
    if not absolute(scheduled):
        return ""
    return f"{EDITOR_BASE}/#load={quote(scheduled, safe='')}"


def viewer_url(feed: CatalogFeed) -> str:
    """Schedule and realtime in viz.rt.gtfs.zone, as feed-list's app-links.ts."""
    scheduled = next(iter(feed.urls.get("scheduled", ())), "")
    if not scheduled or not has_realtime(feed):
        return ""
    params = {"scheduled": scheduled}
    slots = (("vehicles", "rt_vp"), ("trip_updates", "rt_tu"), ("alerts", "rt_al"))
    for role, key in slots:
        if urls := feed.urls.get(role):
            params[key] = urls[0]
    # An endpoint of undeclared type takes the first empty slot.
    empty = next((key for _, key in slots if key not in params), None)
    if (untyped := feed.urls.get("realtime")) and empty:
        params[empty] = untyped[0]
    return f"{VIEWER_BASE}/#{urlencode(params)}"


def url_row(feed: CatalogFeed, role: str, url: str) -> CatalogRow | None:
    """The member row that lists `url` for `role`, else any that lists it."""
    rows = [row for row in feed.rows if url in row.urls.values()]
    return next((row for row in rows if row.urls.get(role) == url), None) or next(
        iter(rows), None
    )


def check_text(row: CatalogRow | None, auth: bool, locale: Locale) -> str:
    """'Up since Sep 23, 2026, HTTP 200', 'Never reached (DNS lookup failed)'."""
    if auth or (row and row.auth):
        return t(locale, "check.key")
    check = row and row.check
    if not check or check.state == "unknown":
        return t(locale, "check.unknown")
    if check.code:
        detail = t(locale, "check.http", code=check.code)
    elif check.error:
        error = check.error if check.error in CHECK_ERRORS else "other"
        detail = t(locale, f"check.error_{error}")
    else:
        detail = ""
    since = day(check.since)
    if check.state == "down" and check.never_up:
        text = t(locale, "check.never")
    elif since:
        text = t(locale, f"check.{check.state}_since", date=long_date(since, locale))
    else:
        text = t(locale, f"state.{check.state}")
    return t(locale, "check.detail", text=text, detail=detail) if detail else text


def role_views(feed: CatalogFeed, locale: Locale) -> tuple[RoleView, ...]:
    """The feed's roles, schedule first, each with its URLs best first."""
    views = []
    for role in STATIC_ROLES + RT_ROLES:
        urls = feed.urls.get(role)
        if not urls:
            continue
        state = feed.role_state.get(role, "unknown")
        label = t(locale, f"role.{role}")
        auth = role in feed.auth
        url_views = []
        for url in urls:
            row = url_row(feed, role, url)
            check = row.check if row else None
            url_views.append(
                UrlView(
                    url=url,
                    absolute=absolute(url),
                    state=(check.state if check else "unknown"),
                    check=check_text(row, auth, locale),
                    redirect=(check.final_url or "") if check else "",
                )
            )
        views.append(
            RoleView(
                role=role,
                label=label,
                short=ROLE_SHORT[role],
                state=state,
                chip_title=t(locale, f"role.chip_{state}", role=label),
                auth=auth,
                urls=tuple(url_views),
            )
        )
    return tuple(views)


def number(n: int, locale: Locale) -> str:
    """1,234 or 1 234, in `locale`'s digits grouping."""
    return format_decimal(n, locale=locale)


def size(n: int, locale: Locale) -> str:
    """Bytes as '22.3 MB' or '22,3 Mo'."""
    for unit, scale in (("gb", 1e9), ("mb", 1e6), ("kb", 1e3)):
        if n >= scale:
            value = format_decimal(n / scale, "0.0", locale=locale)
            return t(locale, f"size.{unit}", size=value)
    return t(locale, "size.b", size=number(n, locale))


def outcome_label(outcome: str, locale: Locale) -> str:
    key = outcome if outcome in OUTCOMES else "error"
    return t(locale, f"outcome.{key}")


def fact_rows(
    feed: CatalogFeed, locale: Locale, built: bool = False
) -> tuple[tuple[str, str], ...]:
    """What the feed's schedule is and held, as (label, value) pairs; the
    last download's outcome only for a feed without a site (`built`), whose
    page would otherwise contradict it."""
    facts = []
    content = feed.content
    if content.get("state") and not built:
        checked = day(content.get("checked"))
        outcome = outcome_label(content["state"], locale)
        facts.append(
            (
                t(locale, "field.download"),
                t(
                    locale,
                    "feed.checked",
                    outcome=outcome,
                    date=long_date(checked, locale),
                )
                if checked
                else outcome,
            )
        )
    start, end = day(content.get("serviceStart")), day(content.get("serviceEnd"))
    if start and end:
        facts.append(
            (
                t(locale, "field.service"),
                t(
                    locale,
                    "range.both",
                    start=long_date(start, locale),
                    end=long_date(end, locale),
                ),
            )
        )
    if content.get("publisher"):
        facts.append((t(locale, "field.publisher"), content["publisher"]))
    if content.get("version"):
        facts.append((t(locale, "field.version"), content["version"]))
    if content.get("routes") is not None:
        facts.append(
            (
                t(locale, "field.contents"),
                t(
                    locale,
                    "feed.contents",
                    routes=number(content["routes"], locale),
                    stops=number(content.get("stops") or 0, locale),
                    trips=number(content.get("trips") or 0, locale),
                ),
            )
        )
    if feed.static_bytes is not None:
        facts.append((t(locale, "field.size"), size(feed.static_bytes, locale)))
    if modified := day(feed.last_modified):
        facts.append((t(locale, "field.last_modified"), long_date(modified, locale)))
    facts.append((t(locale, "field.feed_id"), feed.feed_id))
    return tuple(facts)


def row_views(feed: CatalogFeed) -> tuple[RowView, ...]:
    """The member rows, once each: a catalog's static and rt rows can share a
    name and id."""
    views: dict[tuple[str, str, str], RowView] = {}
    for row in feed.rows:
        view = RowView(
            catalog=CATALOG_NAMES.get(row.catalog, row.catalog),
            name=row.name,
            catalog_id=row.feed_id,
            href=catalog_url(row),
            operator="" if row.operator_name == row.name else row.operator_name,
            license_url=row.license_url or "",
        )
        views.setdefault((view.catalog, view.name, view.catalog_id), view)
    return tuple(views.values())


def credits(feed: CatalogFeed) -> tuple[Credit, ...]:
    """The catalogs the feed's rows come from, with their licenses."""
    catalogs = {row.catalog for row in feed.rows}
    return tuple(
        Credit(*ATTRIBUTION[catalog]) for catalog in ATTRIBUTION if catalog in catalogs
    )


def no_site_reason(feed: CatalogFeed, locale: Locale) -> str:
    """Why the feed has no timetables."""
    outcome = feed.content.get("state")
    if outcome and outcome != "ok":
        return t(locale, "feed.no_site_outcome", outcome=outcome_label(outcome, locale))
    if not feed.urls.get("scheduled"):
        return t(locale, "feed.no_site_realtime")
    if feed.role_state.get("scheduled") != "up":
        return t(locale, "feed.no_site_down")
    return t(locale, "feed.no_site")


def feed_view(feed: CatalogFeed, locale: Locale, built: bool = False) -> FeedView:
    """The page view of a feed, on its site's index when `built`."""
    licenses = feed.licenses or tuple(
        sorted({row.license_url for row in feed.rows if row.license_url})
    )
    return FeedView(
        feed_id=feed.feed_id,
        name=feed.name,
        subtitle=feed.subtitle or "",
        kinds=kinds(feed, locale),
        state=feed.state,
        state_label=t(locale, f"state.{feed.state}"),
        state_text=state_text(feed, locale),
        roles=role_views(feed, locale),
        facts=fact_rows(feed, locale, built),
        rows=row_views(feed),
        licenses=licenses,
        credits=credits(feed),
        editor_url=editor_url(feed),
        viewer_url=viewer_url(feed),
        reason=no_site_reason(feed, locale),
    )


def place_line(feed: CatalogFeed) -> str:
    parts = (feed.municipality, feed.subdivision, feed.country)
    return ", ".join(part for part in parts if part)


def dataset(feed: CatalogFeed, url: str, description: str) -> dict[str, Any]:
    """schema.org Dataset, for Google Dataset Search."""
    doc: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "@id": url,
        "url": url,
        "name": f"{feed.name} {kinds(feed, 'en')}",
        "description": description,
        "identifier": feed.feed_id,
        "isAccessibleForFree": True,
        "keywords": ["GTFS", "public transit", "transit schedule"]
        + (["GTFS Realtime"] if has_realtime(feed) else []),
        "includedInDataCatalog": {
            "@type": "DataCatalog",
            "name": "sites.gtfs.zone",
            "url": "https://sites.gtfs.zone/",
        },
        "distribution": [
            {
                "@type": "DataDownload",
                "name": t("en", f"role.{role}"),
                "encodingFormat": ROLE_FORMATS[role],
                "contentUrl": urls[0],
            }
            for role in STATIC_ROLES + RT_ROLES
            if (urls := feed.urls.get(role)) and absolute(urls[0])
        ],
    }
    if aliases := sorted({r.name for r in feed.rows if r.name and r.name != feed.name}):
        doc["alternateName"] = aliases
    if operators := sorted({r.operator_name for r in feed.rows if r.operator_name}):
        doc["creator"] = [{"@type": "Organization", "name": op} for op in operators]
    if feed.licenses:
        doc["license"] = feed.licenses[0]
    if feed.last_modified:
        doc["dateModified"] = feed.last_modified
    if where := place_line(feed):
        doc["spatialCoverage"] = {"@type": "Place", "name": where}
    start, end = feed.content.get("serviceStart"), feed.content.get("serviceEnd")
    if start and end:
        doc["temporalCoverage"] = f"{start}/{end}"
    return doc

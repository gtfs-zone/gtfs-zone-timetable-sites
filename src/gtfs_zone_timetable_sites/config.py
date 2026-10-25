"""Pydantic models for sites.yaml: defaults plus per-site overrides."""

from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from gtfs_zone_timetable_sites.i18n import Locale

MapKind = Literal["svg", "none"]
TileStyle = Literal[
    "stadia-alidade-smooth",
    "stadia-alidade-smooth-dark",
    "stadia-alidade-bright",
    "stadia-alidade-satellite",
    "stadia-outdoors",
    "stadia-osm-bright",
    "stadia-toner",
    "stadia-toner-lite",
    "stadia-toner-dark",
    "stadia-toner-blacklite",
    "stadia-toner-background",
    "stadia-terrain",
    "stadia-terrain-background",
    "stadia-watercolor",
]
TimeFormat = Literal["12h", "24h"]
Timepoints = Literal["auto", "all", "timepoint-flag"]

FEEDS_URL = "https://data.gtfs.zone/feeds.json"
USER_AGENT = "gtfs-zone-timetable-sites (+https://sites.gtfs.zone)"

SLUG_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"
FEED_PATTERN = r"^f-[0-9a-f]{10}$"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BasemapPair(Strict):
    """Tile styles for the light and dark color schemes."""

    light: TileStyle
    dark: TileStyle


Basemap = Literal["none"] | TileStyle | BasemapPair


class Options(Strict):
    map: MapKind = "svg"
    horizon_days: int = Field(28, gt=0, le=366)
    time_format: TimeFormat = "12h"
    timepoints: Timepoints = "auto"
    basemap: Basemap = "none"
    locale: Locale = "en"
    # Whether search engines may index the site's pages.
    index: bool = True


class RouteFilter(Strict):
    """Include by route_type or route_id (empty means all), then exclude."""

    route_types: tuple[int, ...] = ()
    route_ids: tuple[str, ...] = ()
    exclude_route_types: tuple[int, ...] = ()
    exclude_route_ids: tuple[str, ...] = ()

    def accepts(self, route_id: str, route_type: int) -> bool:
        if self.route_types and route_type not in self.route_types:
            return False
        if self.route_ids and route_id not in self.route_ids:
            return False
        return (
            route_type not in self.exclude_route_types
            and route_id not in self.exclude_route_ids
        )


class CatalogFeed(BaseModel):
    """One entry of feeds.json, as feed-catalog's `artifacts.feed_entry` writes it."""

    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)

    feed_id: str = Field(alias="feedId")
    name: str
    members: tuple[str, ...] = ()
    # role (scheduled, trip_updates, vehicles, alerts) to URLs, best first.
    urls: dict[str, tuple[str, ...]] = {}
    role_state: dict[str, str] = Field({}, alias="roleState")
    state: str = "unknown"
    country: str | None = None
    country_code: str | None = None
    subdivision: str | None = None
    municipality: str | None = None
    # What sets the feed apart from others of the same name, like its network.
    subtitle: str | None = None
    static_bytes: int | None = Field(None, alias="staticBytes")
    last_modified: str | None = Field(None, alias="lastModified")
    # The members' license URLs and Transitland / Mobility Database pages.
    licenses: tuple[str, ...] = ()
    catalog_links: tuple[str, ...] = Field((), alias="catalogLinks")


def parse_feeds(feeds_doc: dict[str, Any]) -> dict[str, CatalogFeed]:
    """feeds.json to {feed id: feed}."""
    feeds = (CatalogFeed.model_validate(e) for e in feeds_doc.get("feeds", []))
    return {feed.feed_id: feed for feed in feeds}


class Site(Options):
    """One site with its options fully resolved against the defaults."""

    slug: str = Field(pattern=SLUG_PATTERN)
    feed: str | None = Field(None, pattern=FEED_PATTERN)
    url: str | None = None
    title: str | None = None
    license_url: str | None = None
    routes: RouteFilter = RouteFilter()
    # The feed's catalog entry, when it is in feeds.json.
    catalog: CatalogFeed | None = None

    def licenses(self) -> tuple[str, ...]:
        """The configured license URL, else the catalog's."""
        if self.license_url:
            return (self.license_url,)
        return self.catalog.licenses if self.catalog else ()

    def download_url(self) -> str:
        """The direct URL, else the catalog's best scheduled URL."""
        if self.url is not None:
            return self.url
        if self.catalog is None:
            raise LookupError(f"feed {self.feed} not in feeds.json")
        urls = self.catalog.urls.get("scheduled")
        if not urls:
            raise LookupError(f"feed {self.feed} has no scheduled URL")
        return urls[0]


class SiteEntry(Options):
    """One `sites:` entry. A catalog feed may leave its slug to be assigned."""

    slug: str | None = Field(None, pattern=SLUG_PATTERN)
    feed: str | None = Field(None, pattern=FEED_PATTERN)
    url: str | None = None
    title: str | None = None
    license_url: str | None = None
    routes: RouteFilter = RouteFilter()

    @model_validator(mode="after")
    def one_source(self) -> Self:
        name = self.slug or self.feed or self.url
        if (self.feed is None) == (self.url is None):
            raise ValueError(f"site {name!r} needs exactly one of feed or url")
        if self.url is not None and self.slug is None:
            raise ValueError(f"site {name!r} with a url needs a slug")
        return self

    def resolve(self, slug: str, catalog: CatalogFeed | None) -> Site:
        return Site(**self.model_dump(exclude={"slug"}), slug=slug, catalog=catalog)


class CatalogFilter(Strict):
    """Which feeds.json feeds become sites, beyond those in `sites:`. Only
    feeds whose schedule is up are taken."""

    countries: tuple[str, ...] = ()
    exclude_countries: tuple[str, ...] = ()
    # Skip feeds without a country code.
    require_country: bool = False
    max_bytes: int | None = Field(None, gt=0)
    exclude: tuple[str, ...] = ()
    # Countries whose feeds are indexed by search engines; empty for all.
    index_countries: tuple[str, ...] = ()


class Config(Strict):
    defaults: Options = Options()
    catalog: CatalogFilter | None = None
    sites: tuple[SiteEntry, ...] = ()
    # Feed ids `timetable-sites dev` builds by default.
    dev: tuple[Annotated[str, Field(pattern=FEED_PATTERN)], ...] = ()

    @model_validator(mode="before")
    @classmethod
    def apply_defaults(cls, data: object) -> object:
        """Fill each site's unset options from `defaults`."""
        if not isinstance(data, dict):
            return data
        defaults = data.get("defaults") or {}
        if not isinstance(defaults, dict):
            return data
        sites = data.get("sites") or []
        merged = [defaults | site if isinstance(site, dict) else site for site in sites]
        return data | {"sites": merged}

    @model_validator(mode="after")
    def unique_entries(self) -> Self:
        for field in ("slug", "feed"):
            values = [getattr(s, field) for s in self.sites if getattr(s, field)]
            dupes = sorted({v for v in values if values.count(v) > 1})
            if dupes:
                raise ValueError(f"duplicate site {field}s: {', '.join(dupes)}")
        return self

    def entry(self, slug: str) -> SiteEntry:
        for site in self.sites:
            if site.slug == slug:
                return site
        raise KeyError(f"no site {slug!r} in config")


def load_config(text: str) -> Config:
    return Config.model_validate(yaml.safe_load(text) or {})

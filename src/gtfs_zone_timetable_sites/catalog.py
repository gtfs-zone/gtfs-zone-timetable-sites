"""feed-catalog's logical feeds (data.gtfs.zone/feeds.json) to resolved sites.

A site's slug is its public URL, so once assigned it is pinned in a
{feed id: slug} map kept alongside the sites. A renamed site's old slug stays
as an alias that redirects to the new one, and no slug is ever reused.
"""

import functools
import hashlib
import re
from typing import Any

from anyascii import anyascii
from pykakasi import kakasi

from gtfs_zone_timetable_sites.config import CatalogFeed, Config, Site, parse_feeds

SHARDS = 16
# Root paths a site slug may not take.
RESERVED = {"countries", "sitemaps"}
# Countries whose timetables use 12-hour times; every other country uses 24h.
TWELVE_HOUR = {"US", "CA", "AU", "NZ", "IN", "PH", "PK", "BD", "EG", "SA"}
# Countries whose catalog sites are in French.
FRENCH = {"FR", "MC"}
QUEBEC = ("CA", "Quebec")
SLUG_LENGTH = 60
# A slug with the feed's subtitle or place added.
LONG_SLUG_LENGTH = 80
# Hiragana and katakana.
KANA = re.compile(r"[\u3040-\u30ff]")
# Hepburn long vowels, written short as in Tokyo or Kyushu.
LONG_VOWELS = re.compile(r"(?<=o)u|(?<=u)u")
# Cyrillic soft and hard signs, which anyascii writes as apostrophes.
SIGNS = re.compile("[ьъЬЪ]")


@functools.cache
def _kakasi() -> kakasi:
    return kakasi()


def romanize(text: str, country_code: str | None = None) -> str:
    """ASCII text: Hepburn for Japanese (a JP feed, or kana), else anyascii."""
    if country_code == "JP" or KANA.search(text):
        text = " ".join(
            LONG_VOWELS.sub("", item["hepburn"])
            if item["hepburn"] != item["orig"]
            else item["orig"]
            for item in _kakasi().convert(text)
        )
    return anyascii(SIGNS.sub("", text))


def cut(slug: str, length: int) -> str:
    """The slug cut to `length` at a word boundary when it has one."""
    if len(slug) <= length:
        return slug
    head = slug[: length + 1]
    return head[: head.rfind("-")] if "-" in head else slug[:length]


def slugify(name: str, country_code: str | None = None) -> str:
    """Romanized, lowercase, hyphenated, cut at a word boundary."""
    slug = re.sub(r"[^a-z0-9]+", "-", romanize(name, country_code).lower()).strip("-")
    return cut(slug, SLUG_LENGTH) or "feed"


def base_slug(feed: CatalogFeed) -> str:
    """The feed name's slug, or the feed id when the name romanizes to nothing."""
    slug = slugify(feed.name, feed.country_code)
    return feed.feed_id if slug == "feed" or slug in RESERVED else slug


def place_slug(feed: CatalogFeed) -> str:
    """The base slug with the first of the feed's subtitle, municipality and
    subdivision that the name does not already hold."""
    base = base_slug(feed)
    for extra in (feed.subtitle, feed.municipality, feed.subdivision):
        part = slugify(extra, feed.country_code) if extra else "feed"
        if part != "feed" and part.replace("-", "") not in base.replace("-", ""):
            return cut(f"{base}-{part}", LONG_SLUG_LENGTH)
    return base


def assign_slugs(
    feeds: list[CatalogFeed], pinned: dict[str, str], taken: set[str]
) -> dict[str, str]:
    """Pinned plus a new slug for each unpinned feed. A name more than one feed
    shares takes the feed's subtitle or place; a slug still taken is suffixed
    -2, -3, ... past any slug pinned (even to a feed no longer listed) or in
    `taken`."""
    slugs = dict(pinned)
    used = set(pinned.values()) | taken
    names: dict[str, int] = {}
    for feed in feeds:
        names[base_slug(feed)] = names.get(base_slug(feed), 0) + 1
    for feed in sorted(feeds, key=lambda f: f.feed_id):
        if feed.feed_id in slugs:
            continue
        base = base_slug(feed)
        if names[base] > 1:
            base = place_slug(feed)
        slug = base
        n = 1
        while slug in used:
            n += 1
            slug = f"{base}-{n}"
        slugs[feed.feed_id] = slug
        used.add(slug)
    return slugs


def selected(config: Config, feed: CatalogFeed) -> bool:
    """Whether the catalog filter takes this feed."""
    rules = config.catalog
    if rules is None or feed.feed_id in rules.exclude:
        return False
    if feed.role_state.get("scheduled") != "up" or not feed.urls.get("scheduled"):
        return False
    if rules.countries and feed.country_code not in rules.countries:
        return False
    if feed.country_code in rules.exclude_countries:
        return False
    if rules.require_country and not feed.country_code:
        return False
    return not (
        rules.max_bytes is not None
        and feed.static_bytes is not None
        and feed.static_bytes > rules.max_bytes
    )


def country_options(feed: CatalogFeed) -> dict[str, str]:
    """The time format and, where French, the locale for a feed's country."""
    quebec = (feed.country_code, feed.subdivision) == QUEBEC
    twelve = feed.country_code in TWELVE_HOUR and not quebec
    options = {"time_format": "12h" if twelve else "24h"}
    if feed.country_code in FRENCH or quebec:
        options["locale"] = "fr"
    return options


def resolve_sites(
    config: Config,
    feeds_doc: dict[str, Any],
    pinned: dict[str, str],
    aliases: dict[str, str] | None = None,
) -> tuple[list[Site], dict[str, str], dict[str, str]]:
    """Every configured site plus every catalog feed the filter takes, the
    updated {feed id: slug} map and the updated {old slug: feed id} aliases. A
    catalog feed's options are the defaults with its country's options over
    them; a `sites:` entry for it sets its options instead and, when given, its
    slug, which renames a pinned feed and keeps its old slug as an alias."""
    feeds = parse_feeds(feeds_doc)
    aliases = dict(aliases or {})
    entries = {entry.feed: entry for entry in config.sites if entry.feed}
    renamed = dict(pinned)
    for feed_id, entry in entries.items():
        old = pinned.get(feed_id)
        if entry.slug and old and old != entry.slug:
            aliases[old] = feed_id
            renamed[feed_id] = entry.slug
        if entry.slug and aliases.get(entry.slug) == feed_id:
            del aliases[entry.slug]
    auto = [
        feed
        for feed in feeds.values()
        if feed.feed_id not in entries and selected(config, feed)
    ]
    unslugged = [
        feeds.get(entry.feed) or CatalogFeed(feedId=entry.feed, name=entry.feed)
        for entry in entries.values()
        if not entry.slug
    ]
    fixed = {entry.slug for entry in config.sites if entry.slug}
    slugs = assign_slugs(auto + unslugged, renamed, fixed | set(aliases))

    sites = []
    for entry in config.sites:
        slug = entry.slug or slugs[entry.feed]
        sites.append(entry.resolve(slug, feeds.get(entry.feed)))
    defaults = config.defaults.model_dump()
    for feed in auto:
        slug = slugs[feed.feed_id]
        options = defaults | country_options(feed)
        sites.append(Site(**options, slug=slug, feed=feed.feed_id, catalog=feed))

    counts: dict[str, int] = dict.fromkeys(aliases, 1)
    for site in sites:
        counts[site.slug] = counts.get(site.slug, 0) + 1
    clash = sorted(slug for slug, n in counts.items() if n > 1)
    if clash:
        raise ValueError(f"slugs taken twice: {', '.join(clash)}")
    return sites, slugs, aliases


def redirects(
    slugs: dict[str, str], aliases: dict[str, str], live: set[str]
) -> dict[str, str]:
    """{old slug: current slug} for each alias whose feed has a live site."""
    return {
        old: slugs[feed_id]
        for old, feed_id in aliases.items()
        if slugs.get(feed_id) in live
    }


def shard_of(slug: str, shards: int = SHARDS) -> int:
    """A stable shard for the slug."""
    return int.from_bytes(hashlib.sha256(slug.encode()).digest()[:4]) % shards

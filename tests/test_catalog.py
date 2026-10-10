from pathlib import Path

import pytest

from gtfs_zone_timetable_sites.catalog import (
    SHARDS,
    assign_slugs,
    base_slug,
    country_options,
    redirects,
    resolve_sites,
    shard_of,
    slugify,
)
from gtfs_zone_timetable_sites.config import CatalogFeed, RouteFilter, load_config

REPO_CONFIG = Path(__file__).parent.parent / "sites.yaml"


def feed(feed_id, name="Bus", **fields):
    return {
        "feedId": feed_id,
        "name": name,
        "urls": {"scheduled": [f"https://x/{feed_id}.zip"]},
        "roleState": {"scheduled": "up"},
        "country_code": "US",
        "staticBytes": 1000,
    } | fields


def catalog_feed(feed_id, name, **fields):
    return CatalogFeed(feedId=feed_id, name=name, **fields)


def test_slugify():
    assert slugify("L'Inter des Laurentides") == "l-inter-des-laurentides"
    assert slugify("Société de transport") == "societe-de-transport"
    assert slugify("Ужгородська міська рада") == "uzhgorodska-miska-rada"
    assert slugify("Αθήνα") == "athina"
    assert slugify("서울") == "seoul"
    assert slugify("北京") == "beijing"
    # Kanji read as Japanese for a JP feed, and kana anywhere.
    assert slugify("静岡市", "JP") == "shizuokashi"
    # Long vowels written short.
    assert slugify("東京都", "JP") == "tokyoto"
    assert slugify("さいたま市") == "saitama-shi"
    assert slugify("!!!") == "feed"
    assert base_slug(catalog_feed("f-0000000001", "!!!")) == "f-0000000001"
    assert base_slug(catalog_feed("f-0000000002", "Countries")) == "f-0000000002"


def test_slugify_cuts_at_a_word_boundary():
    name = "Syndicat mixte des transports collectifs de la region de Grenoble"
    slug = slugify(name)
    assert slug == "syndicat-mixte-des-transports-collectifs-de-la-region-de"
    assert len(slugify("x" * 80)) == 60


def test_assign_slugs_pins_and_suffixes():
    feeds = [
        catalog_feed("f-0000000003", "Metro"),
        catalog_feed("f-0000000001", "Metro"),
        catalog_feed("f-0000000002", "Metro Transit"),
    ]
    slugs = assign_slugs(feeds, {"f-0000000002": "mt"}, {"metro"})
    # Pinned kept; new ones by feed id, past slugs already taken.
    assert slugs == {
        "f-0000000001": "metro-2",
        "f-0000000002": "mt",
        "f-0000000003": "metro-3",
    }


def test_assign_slugs_names_a_shared_name_by_place():
    feeds = [
        catalog_feed("f-0000000001", "BC Transit", municipality="Victoria"),
        catalog_feed("f-0000000002", "BC Transit", municipality="Kelowna"),
        catalog_feed("f-0000000003", "BC Transit", subdivision="British Columbia"),
        catalog_feed("f-0000000004", "BC Transit"),
        catalog_feed("f-0000000005", "BC Transit", municipality="Victoria"),
        catalog_feed("f-0000000006", "Guelph Transit", municipality="Guelph"),
        catalog_feed("f-0000000007", "Guelph Transit", municipality="Guelph"),
        catalog_feed("f-0000000008", "Zenbus", municipality="Sète"),
        catalog_feed(
            "f-0000000009", "MTA", subtitle="Manhattan Bus", municipality="Brooklyn"
        ),
        catalog_feed("f-000000000a", "MTA", municipality="Brooklyn"),
    ]
    slugs = assign_slugs(feeds, {"f-0000000008": "zenbus"}, set())
    assert slugs == {
        "f-0000000001": "bc-transit-victoria",
        "f-0000000002": "bc-transit-kelowna",
        "f-0000000003": "bc-transit-british-columbia",
        "f-0000000004": "bc-transit",
        "f-0000000005": "bc-transit-victoria-2",
        # The name already holds the place.
        "f-0000000006": "guelph-transit",
        "f-0000000007": "guelph-transit-2",
        "f-0000000008": "zenbus",
        # The subtitle first.
        "f-0000000009": "mta-manhattan-bus",
        "f-000000000a": "mta-brooklyn",
    }


def test_assign_slugs_takes_the_place_once_a_name_is_shared():
    feeds = [
        catalog_feed("f-0000000001", "Zenbus", municipality="Sète"),
        catalog_feed("f-0000000002", "Zenbus", municipality="Meudon"),
    ]
    slugs = assign_slugs(feeds, {"f-0000000001": "zenbus"}, set())
    assert slugs["f-0000000002"] == "zenbus-meudon"


def test_assign_slugs_never_reuses_a_dropped_feeds_slug():
    slugs = assign_slugs(
        [catalog_feed("f-0000000002", "Metro")], {"f-0000000001": "metro"}, set()
    )
    assert slugs["f-0000000002"] == "metro-2"
    assert slugs["f-0000000001"] == "metro"


def test_resolve_sites_filters_and_overrides():
    config = load_config(
        """
defaults: {time_format: 24h}
catalog: {countries: [US, CA], max_bytes: 5000, exclude: [f-000000000e]}
sites:
  - {feed: f-0000000001, title: Listed, time_format: 12h}
  - {slug: manual, feed: f-0000000002}
  - {slug: direct, url: "https://y/z.zip"}
  - {feed: f-00000000ff}
"""
    )
    doc = {
        "feeds": [
            feed("f-0000000001", "One", country_code="FR"),
            feed("f-0000000002", "Two"),
            feed("f-0000000003", "Three"),
            feed("f-0000000004", "Four", country_code="CA", staticBytes=None),
            feed("f-0000000005", "Too Big", staticBytes=6000),
            feed("f-0000000006", "Elsewhere", country_code="JP"),
            feed("f-0000000007", "Down", roleState={"scheduled": "down"}),
            feed("f-0000000008", "No schedule", urls={"vehicles": ["v"]}),
            feed("f-000000000e", "Excluded"),
        ]
    }
    sites, slugs, _ = resolve_sites(config, doc, {"f-0000000003": "three-pinned"})
    by_slug = {site.slug: site for site in sites}
    assert list(by_slug) == [
        "one",
        "manual",
        "direct",
        "f-00000000ff",
        "three-pinned",
        "four",
    ]
    # Listed feeds skip the filter and keep their options.
    assert by_slug["one"].title == "Listed"
    assert by_slug["one"].time_format == "12h"
    assert by_slug["one"].catalog.country_code == "FR"
    assert by_slug["manual"].catalog.name == "Two"
    assert by_slug["direct"].catalog is None
    # Not in feeds.json: listed without a catalog entry, fails on download.
    assert by_slug["f-00000000ff"].catalog is None
    # The country's time format over the defaults.
    assert by_slug["four"].time_format == "12h"
    assert by_slug["four"].download_url() == "https://x/f-0000000004.zip"
    assert slugs == {
        "f-0000000001": "one",
        "f-0000000003": "three-pinned",
        "f-0000000004": "four",
        "f-00000000ff": "f-00000000ff",
    }


def test_resolve_sites_excludes_countries_and_feeds_without_one():
    config = load_config(
        "catalog: {exclude_countries: [JP], require_country: true}\n"
        "sites: [{feed: f-0000000004}]"
    )
    doc = {
        "feeds": [
            feed("f-0000000001", "Here"),
            feed("f-0000000002", "Elsewhere", country_code="JP"),
            feed("f-0000000003", "Nowhere", country_code=None),
            feed("f-0000000004", "Listed", country_code=None),
        ]
    }
    sites, _, _ = resolve_sites(config, doc, {})
    assert [site.slug for site in sites] == ["listed", "here"]


def test_resolve_sites_takes_options_from_the_country():
    config = load_config(
        """
defaults: {time_format: 12h, locale: en}
catalog: {}
sites: [{feed: f-0000000003, time_format: 12h, locale: en}]
"""
    )
    doc = {
        "feeds": [
            feed("f-0000000001", "Paris", country_code="FR"),
            feed("f-0000000002", "Berlin", country_code="DE"),
            feed("f-0000000003", "Lyon", country_code="FR"),
        ]
    }
    sites, _, _ = resolve_sites(config, doc, {})
    options = {site.slug: (site.time_format, site.locale) for site in sites}
    # A listed feed keeps its own options.
    assert options == {
        "lyon": ("12h", "en"),
        "paris": ("24h", "fr"),
        "berlin": ("24h", "en"),
    }


@pytest.mark.parametrize(
    ("country", "subdivision", "expected"),
    [
        ("US", None, {"time_format": "12h"}),
        ("DE", None, {"time_format": "24h"}),
        ("FR", None, {"time_format": "24h", "locale": "fr"}),
        ("CA", "Quebec", {"time_format": "24h", "locale": "fr"}),
        ("CA", "Ontario", {"time_format": "12h"}),
        (None, None, {"time_format": "24h"}),
    ],
)
def test_country_options(country, subdivision, expected):
    entry = CatalogFeed(
        feedId="f-0000000001", name="Bus", country_code=country, subdivision=subdivision
    )
    assert country_options(entry) == expected


def test_resolve_sites_renames_a_pinned_feed_with_an_alias():
    config = load_config("catalog: {}\nsites: [{feed: f-0000000001, slug: metro-lyon}]")
    doc = {"feeds": [feed("f-0000000001", "Metro"), feed("f-0000000002", "Metro")]}
    sites, slugs, aliases = resolve_sites(config, doc, {"f-0000000001": "metro"})
    assert slugs == {"f-0000000001": "metro-lyon", "f-0000000002": "metro-2"}
    assert aliases == {"metro": "f-0000000001"}
    live = {site.slug for site in sites}
    assert redirects(slugs, aliases, live) == {"metro": "metro-lyon"}

    # Without the entry the rename stays; renamed back, the alias goes.
    _, slugs, kept = resolve_sites(load_config("catalog: {}"), doc, slugs, aliases)
    assert slugs["f-0000000001"] == "metro-lyon"
    assert kept == aliases
    back = load_config("catalog: {}\nsites: [{feed: f-0000000001, slug: metro}]")
    _, slugs, aliases = resolve_sites(back, doc, slugs, aliases)
    assert slugs["f-0000000001"] == "metro"
    assert aliases == {"metro-lyon": "f-0000000001"}


def test_resolve_sites_rejects_a_slug_taken_by_an_alias():
    config = load_config("sites: [{slug: metro, url: u}]")
    with pytest.raises(ValueError, match="metro"):
        resolve_sites(config, {"feeds": []}, {}, {"metro": "f-0000000001"})


def test_resolve_sites_without_catalog_takes_only_listed():
    config = load_config("sites: [{slug: a, url: u}]")
    sites, slugs, _ = resolve_sites(config, {"feeds": [feed("f-0000000001")]}, {})
    assert [site.slug for site in sites] == ["a"]
    assert slugs == {}


def test_resolve_sites_rejects_a_slug_taken_twice():
    config = load_config("catalog: {}\nsites: [{slug: bus, feed: f-0000000001}]")
    doc = {"feeds": [feed("f-0000000001"), feed("f-0000000002")]}
    with pytest.raises(ValueError, match="bus"):
        resolve_sites(config, doc, {"f-0000000002": "bus"})


def test_shard_of_is_stable_and_spread():
    assert shard_of("amtrak") == shard_of("amtrak")
    shards = {shard_of(f"site-{n}") for n in range(500)}
    assert shards == set(range(SHARDS))


def test_repo_config_takes_whole_feeds_from_the_catalog():
    config = load_config(REPO_CONFIG.read_text())
    doc = {
        "feeds": [
            feed("f-1f748c5476", "MBTA"),
            feed("f-2f033a022e", "Amtrak"),
            feed("f-ff2cfa2434", "Columbia County", country_code=None),
        ]
    }
    sites, _, _ = resolve_sites(config, doc, {})
    by_slug = {site.slug: site for site in sites}
    # No country code, so the catalog filter skips it.
    assert set(by_slug) == {"mbta", "amtrak"}
    assert all(site.time_format == "12h" for site in sites)
    assert all(site.routes == RouteFilter() for site in sites)


def test_repo_config_dev_feeds_are_valid():
    config = load_config(REPO_CONFIG.read_text())
    assert config.dev and len(set(config.dev)) == len(config.dev)

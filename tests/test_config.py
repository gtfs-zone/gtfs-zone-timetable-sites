from pathlib import Path

import pytest
from pydantic import ValidationError

from gtfs_zone_timetable_sites.config import (
    CatalogFeed,
    RouteFilter,
    Site,
    load_config,
    parse_feeds,
)

REPO_CONFIG = Path(__file__).parent.parent / "sites.yaml"


def test_repo_config_loads():
    config = load_config(REPO_CONFIG.read_text())
    assert config.defaults.map == "maplibre"
    assert all(site.routes == RouteFilter() for site in config.sites)


def test_site_overrides_defaults():
    config = load_config(
        """
defaults: {time_format: 24h, horizon_days: 14}
sites:
  - {slug: a, url: "https://example.org/a.zip"}
  - {slug: b, url: "https://example.org/b.zip", time_format: 12h}
"""
    )
    assert config.entry("a").time_format == "24h"
    assert config.entry("a").horizon_days == 14
    assert config.entry("b").time_format == "12h"


@pytest.mark.parametrize(
    "text",
    [
        "defaults: {colour: red}",
        "sites: [{slug: a, url: u, colour: red}]",
        "sites: [{slug: a}]",
        "sites: [{slug: a, url: u, feed: f-0123456789}]",
        "sites: [{slug: Bad Slug, url: u}]",
        "sites: [{slug: a, url: u}, {slug: a, url: v}]",
        "sites: [{slug: a, url: u, style: fancy}]",
        "sites: [{slug: a, url: u, orientation: trips-down}]",
        "sites: [{slug: a, url: u, brand_color: red}]",
        "sites: [{slug: a, url: u, basemap: stadia-toner}]",
        "sites: [{slug: a, url: u, map: svg}]",
        "sites: [{slug: a, url: u, interactive_map: true}]",
        "sites: [{url: u}]",
        "sites: [{feed: f-0123456789}, {slug: b, feed: f-0123456789}]",
        "catalog: {countries: [US], max_size: 5}",
    ],
)
def test_invalid_config(text):
    with pytest.raises(ValidationError):
        load_config(text)


def test_download_url_from_catalog():
    doc = {
        "feeds": [
            {
                "feedId": "f-0123456789",
                "name": "G",
                "urls": {"scheduled": ["https://x/g.zip"], "vehicles": ["https://x/v"]},
                "extra": "ignored",
            }
        ]
    }
    feed = parse_feeds(doc)["f-0123456789"]
    assert feed.urls["vehicles"] == ("https://x/v",)
    site = Site(slug="a", feed=feed.feed_id, catalog=feed)
    assert site.download_url() == "https://x/g.zip"
    assert Site(slug="a", url="https://y/z.zip").download_url() == "https://y/z.zip"
    with pytest.raises(LookupError):
        Site(slug="a", feed="f-0123456789").download_url()
    empty = CatalogFeed(feedId="f-0123456789", name="G")
    with pytest.raises(LookupError):
        Site(slug="a", feed="f-0123456789", catalog=empty).download_url()


def test_feed_entry_slug_optional():
    config = load_config(
        "catalog: {countries: [US]}\nsites: [{feed: f-0123456789, time_format: 24h}]"
    )
    assert config.sites[0].slug is None
    assert config.catalog.countries == ("US",)


def test_route_filter():
    rail = RouteFilter(route_types=(2,), exclude_route_ids=("CR-Foxboro",))
    assert rail.accepts("CR-Fitchburg", 2)
    assert not rail.accepts("Red", 1)
    assert not rail.accepts("CR-Foxboro", 2)
    assert RouteFilter().accepts("anything", 3)


def test_dev_feeds_must_be_feed_ids():
    with pytest.raises(ValidationError):
        load_config("dev: [mbta]")

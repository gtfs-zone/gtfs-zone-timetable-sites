import io
import json
import re
from datetime import date

import pytest
from conftest import fixture_files, fixture_zip, make_zip

from gtfs_zone_timetable_sites import build as build_module
from gtfs_zone_timetable_sites.build import (
    ROOT_LOCALES,
    build_root,
    build_site,
    country_languages,
    merge_routes,
    page_digest,
    site_feed,
    site_status,
    site_timetables,
    site_title,
    source_label,
    unavailable_reason,
    window_services,
)
from gtfs_zone_timetable_sites.catalog import shard_of
from gtfs_zone_timetable_sites.config import CatalogFeed, Site
from gtfs_zone_timetable_sites.gtfs.reader import read_feed
from gtfs_zone_timetable_sites.pages import date_range
from gtfs_zone_timetable_sites.sources import sources_page

MONDAY = date(2026, 10, 5)
# The root page in each UI language.
ROOT_PAGES = {"index.html"} | {f"lang/{lang}/index.html" for lang in ROOT_LOCALES[1:]}
NOINDEX = '<meta name="robots" content="noindex">'
GEO = re.compile(rb'"geo": ?\{[^}]*\}|"center": ?\[[^\]]*\]')


def build(name: str, **options) -> dict[str, str]:
    site = Site(slug="test", url="https://example.org/g.zip", **options)
    files = build_site(fixture_zip(name), site, today=MONDAY)
    return {path: body.decode() for path, body in files.items()}


def test_file_source_builds_the_same_files():
    site = Site(slug="test", url="https://example.org/g.zip")
    body = fixture_zip("branching")
    from_bytes = build_site(body, site, today=MONDAY)
    from_file = build_site(io.BytesIO(body), site, today=MONDAY)
    assert from_file == from_bytes


def test_site_files():
    files = build("branching", title="Test & Co")
    assert set(files) == {
        "index.html",
        "4/index.html",
        "4/live.json",
        "site.json",
        "content.json",
        "style.css",
        "map.js",
        "map.json",
        "logo.svg",
    }
    home = files["index.html"]
    assert "Test &amp; Co</h1>" in home
    assert 'href="4/" aria-label=' in home
    assert '<link rel="canonical" href="https://sites.gtfs.zone/test/">' in home


def test_route_page_has_timetable():
    page = build("overnight")["1/index.html"]
    assert '<section class="card bg-base-100 shadow-sm" id="daily">' in page
    assert "Charlie</a> <small>CDT time</small></span></th>" in page
    assert '<td class="pm">11:40</td>' in page
    assert "PM times are in bold." in page


def test_24h_times_not_bold():
    page = build("overnight", time_format="24h")["1/index.html"]
    assert "<td>23:40</td>" in page
    assert 'class="pm"' not in page


def test_multiday_markers_and_legend():
    page = build("multiday")["1/index.html"]
    assert "11:30<sup>+1</sup>" in page
    assert "2:10<sup>+2</sup>" in page
    assert "+n: after midnight, n days later." in page


def test_frequency_column_footer():
    page = build("frequencies")["3/index.html"]
    assert "then every" in page


def test_not_a_zip():
    site = Site(slug="test", url="https://example.org/g.zip")
    with pytest.raises(Exception, match="zip"):
        build_site(b"not a zip", site)


def test_site_with_maps():
    files = build("branching")
    home = files["index.html"]
    assert '<link rel="stylesheet" href="style.css">' in home
    assert '<script type="module" src="map.js"></script>' in home
    assert (
        '<section class="card system bg-base-100 shadow-sm" data-src="map.json">'
        in home
    )
    assert '<details class="route-item" id="r-4">' in home
    assert 'aria-label="Open the route page: 4 Forks">Open the route page<svg' in home
    page = files["4/index.html"]
    assert '<link rel="stylesheet" href="../style.css">' in page
    assert '<script type="module" src="../map.js"></script>' in page
    assert "<nav" not in page.split("<main", 1)[1]
    assert "<details" not in page
    assert '<div class="space-y-6" data-route-body>' in page
    assert '<section class="card bg-base-100 shadow-sm" id="' in page
    assert '<script type="application/json" class="route-data">{"color":"#' in page
    assert '<link rel="icon" type="image/svg+xml" href="../logo.svg">' in page
    assert 'href="../../" aria-label="sites.gtfs.zone"' in page
    rows = re.findall(r'<th scope="row"[^>]*>.*?</th>', page, re.S)
    assert rows and all('<span class="dot' in row for row in rows)
    assert all(
        'href="https://www.google.com/maps/search/?api=1&amp;query=' in row
        for row in rows
    )


def test_index_groups_routes_by_mode():
    files = fixture_files("branching")
    files["routes.txt"] += "S,Red,Red Line,1\n"
    trips = files["trips.txt"].splitlines()[1:]
    files["trips.txt"] += "".join(
        line.replace("R,", "S,", 1)
        .replace(",north", ",s-north")
        .replace(",south", ",s-south")
        + "\n"
        for line in trips
    )
    times = files["stop_times.txt"].splitlines()[1:]
    files["stop_times.txt"] += "".join(f"s-{line}\n" for line in times)
    site = Site(slug="test", url="https://example.org/g.zip")
    home = build_site(make_zip(files), site, today=MONDAY)["index.html"].decode()
    assert 'href="#subway"' not in home
    assert home.index('id="subway">Subway</h2>') < home.index('id="bus">Bus</h2>')
    assert home.index('href="red/"') < home.index('href="4/"')
    assert home.count('class="system-map"') == 1
    system = json.loads(build_site(make_zip(files), site, today=MONDAY)["map.json"])
    assert [r["mode"] for r in system["routes"]] == ["subway", "bus"]


def test_title_from_the_agency_with_most_routes():
    files = fixture_files("branching")
    files["agency.txt"] = (
        "agency_id,agency_name,agency_url,agency_timezone\n"
        "b,Small Partner,https://example.org/b,America/New_York\n"
        "a,Test Transit,https://example.org,America/New_York\n"
    )
    files["routes.txt"] = "agency_id," + files["routes.txt"].replace("\nR,", "\na,R,")
    site = Site(slug="test", url="https://example.org/g.zip")
    home = build_site(make_zip(files), site, today=MONDAY)["index.html"].decode()
    assert '<h1 class="text-3xl font-bold">Test Transit</h1>' in home
    assert home.index(">Test Transit</span>") < home.index(">Small Partner</span>")


def test_single_mode_index_has_no_mode_headings():
    home = build("branching")["index.html"]
    assert 'id="bus"' not in home


@pytest.mark.parametrize(
    ("summary", "outcome", "status"),
    [
        ({"routes": 2, "valid_through": "2026-12-01"}, "ok", "ok"),
        ({"routes": 2, "valid_through": "2026-12-01"}, None, "ok"),
        # A far-off end date is not a placeholder to drop.
        ({"routes": 2, "valid_through": "2029-12-31"}, "ok", "ok"),
        ({"routes": 2, "valid_through": "2026-10-10"}, "ok", "expiring"),
        ({"routes": 2, "valid_through": "2026-12-01"}, "http_error", "stale"),
        ({"routes": 2, "valid_through": "2026-10-04"}, "ok", "expired"),
        ({"routes": 2, "valid_through": "2026-10-04"}, "http_error", "expired"),
        ({"routes": 2, "valid_through": "2026-12-01"}, "parse_error", "unavailable"),
        ({"routes": 2, "valid_through": "2026-12-01"}, "empty", "unavailable"),
        ({"routes": 2, "valid_through": None}, "ok", "unavailable"),
        ({"routes": 0, "valid_through": "2026-12-01"}, "ok", "unavailable"),
        ({}, None, "unavailable"),
    ],
)
def test_site_status(summary, outcome, status):
    assert site_status(summary, outcome, MONDAY) == status


@pytest.mark.parametrize(
    ("outcome", "detail", "reason"),
    [
        (None, "", "Not built yet"),
        ("ok", "", "Feed has no routes"),
        ("http_error", "HTTP 404", "Download failed: HTTP 404"),
        ("empty", "", "Feed has no scheduled trips"),
        ("error", "RuntimeError: x", "Build failed"),
    ],
)
def test_unavailable_reason(outcome, detail, reason):
    assert unavailable_reason(outcome, detail) == reason


@pytest.mark.parametrize(
    ("outcome", "detail", "reason"),
    [
        (None, "", "Pas encore construit"),
        ("http_error", "HTTP 404", "Échec du téléchargement : HTTP 404"),
        ("timeout", "", "Délai de téléchargement dépassé"),
        ("error", "RuntimeError: x", "Échec du traitement"),
    ],
)
def test_unavailable_reason_in_french(outcome, detail, reason):
    assert unavailable_reason(outcome, detail, "fr") == reason


def test_root_pages_carry_root_js_text():
    summaries = [
        {"slug": "a", "title": "Alpha", "routes": 2, "valid_through": "2026-12-01"}
    ]
    files = build_root(summaries, MONDAY)
    for path in ("index.html", "countries/other/index.html"):
        page = files[path].decode()
        text = json.loads(re.search(r'id="root-text">(.*?)</script>', page)[1])
        assert text["root.agencies"] == {"one": "{n} agency", "other": "{n} agencies"}
        assert text["root.none_found"] == "No agencies found"
        assert text["root.feeds"] == {"one": "{n} feed", "other": "{n} feeds"}
        assert text["state.unknown"] == "Inaccessible"
        assert text["role.chip_down"] == "{role}: did not answer the last daily check"
    assert "1 agency<" in files["index.html"].decode()


def test_root_status_from_outcomes():
    summaries = [
        {"slug": "a", "title": "Alpha", "routes": 2, "valid_through": "2026-12-01"}
    ]
    files = build_root(
        summaries, MONDAY, outcomes={"a": {"outcome": "http_error", "detail": "x"}}
    )
    country = files["countries/other/index.html"].decode()
    assert 'rounded-full bg-warning" title="Last update failed"' in country
    assert 'href="../../a/"' in country


def test_root_lists_unavailable_sites_last_without_a_link():
    summaries = [
        {"slug": "a", "title": "Alpha", "routes": 2, "valid_through": "2026-12-01"},
        {"slug": "b", "title": "Bravo", "routes": 2, "valid_through": "2026-10-01"},
        {"slug": "c", "title": "Charlie", "routes": 2, "valid_through": "2026-12-01"},
        {"slug": "aa", "title": "Aardvark", "source": "example.org: g.zip"},
    ]
    outcomes = {
        "c": {"outcome": "empty", "detail": ""},
        "aa": {"outcome": "http_error", "detail": "HTTP 404"},
    }
    country = build_root(summaries, MONDAY, outcomes=outcomes)[
        "countries/other/index.html"
    ].decode()
    assert (
        country.index(">Alpha<")
        < country.index(">Bravo<")
        < country.index(">Aardvark<")
        < country.index(">Charlie<")
    )
    assert 'href="../../b/"' in country and "badge-error" in country
    assert 'href="../../aa/"' not in country and 'href="../../c/"' not in country
    assert "Download failed: HTTP 404" in country
    assert "Feed has no scheduled trips" in country
    assert "example.org: g.zip" in country


def test_only_external_links_open_in_new_tab():
    for body in build("branching").values():
        for tag in re.findall(r"<a [^>]*>", body):
            href = re.search(r'href="([^"]*)"', tag)[1]
            assert not href.startswith("https://sites.gtfs.zone"), tag
            assert ('target="_blank"' in tag) == href.startswith("http"), tag


def test_same_headsign_runs_share_a_header_cell():
    files = fixture_files("branching")
    files["trips.txt"] = files["trips.txt"].replace(
        "back_skip,Alpha", "back_skip,Charlie"
    )
    site = Site(slug="test", url="https://example.org/g.zip")
    page = build_site(make_zip(files), site, today=MONDAY)["4/index.html"].decode()
    row = re.search(r'<tr class="to">.*?</tr>', page)[0]
    assert '<th scope="col">Alpha</th><th scope="col" colspan="2">Charlie</th>' in row


def test_arrival_and_departure_share_a_cell_on_dwells():
    files = fixture_files("branching")
    files["stop_times.txt"] = (
        files["stop_times.txt"]
        .replace("north1,08:10:00,08:10:00,B,2", "north1,08:10:00,08:15:00,B,2")
        .replace("north2,09:10:00,09:10:00,B,2", "north2,09:10:00,09:12:00,B,2")
    )
    site = Site(slug="test", url="https://example.org/g.zip")
    page = build_site(make_zip(files), site, today=MONDAY)["4/index.html"].decode()
    assert 'class="ar"' not in page and 'class="dp"' not in page
    assert '<td class="dw"><span>8:10</span>8:15</td>' in page
    assert "Two times: arrives, then departs." in page


def test_rail_branches():
    page = build("branching")["4/index.html"]
    # Alpha-bound trips split at Delta: via Charlie or via Echo.
    delta = re.search(r'<th scope="row"[^>]*>.*?Delta</a>', page)[0]
    assert "C20,80 34,70 34,100" in delta
    echo = re.search(r'<th scope="row"[^>]*>[^\n]*?Echo</a>[^\n]*?</th>', page)[0]
    charlie = re.search(r'<th scope="row"[^>]*>[^\n]*?Charlie</a>[^\n]*?</th>', page)[0]
    assert 'style="left:20px"' in echo and "<small>" not in echo
    assert 'style="left:34px"' in charlie
    # Straight single-lane rows are drawn by CSS, with no SVG.
    assert '<span class="rail d" aria-hidden="true"><span class="dot solid"' in page


def test_site_json():
    summary = json.loads(build("branching", title="Test & Co")["site.json"])
    assert summary == {
        "slug": "test",
        "title": "Test & Co",
        "source": "example.org: g.zip",
        "routes": 1,
        "expired_routes": 0,
        "valid_from": summary["valid_from"],
        "valid_through": summary["valid_through"],
        "generated": "2026-10-05",
        # Geocoded from the stops, with no catalog entry.
        "country_code": "US",
        "country": "United States",
        "subdivision": None,
        # Its stops straddle two states, so no point.
        "lat": None,
        "lon": None,
        "noindex": [],
        "languages": ["en"],
    }


def test_locality_names_the_town_in_titles():
    files = build("overnight", title="Test & Co", locality="Quesnel")
    index = files["index.html"]
    assert (
        "<title>Test &amp; Co schedules and timetables - Quesnel, United States</title>"
    ) in index
    assert "Test &amp; Co, Quesnel, United States" in index
    page = files["1/index.html"]
    title = "1 Night Owl bus schedule - Test &amp; Co, Quesnel, United States"
    assert f"<title>{title}</title>" in page
    assert "from Test &amp; Co, Quesnel, United States." in page
    crumbs, _ = json_ld(page)
    assert crumbs["itemListElement"][1]["name"] == "Test & Co, Quesnel"
    assert json.loads(files["site.json"])["title"] == "Test & Co, Quesnel"


def test_site_json_country_from_catalog():
    feed = CatalogFeed(
        feedId="f-0123456789",
        name="Test",
        country="United States",
        country_code="US",
        subdivision="Oregon",
    )
    site = Site(slug="test", feed=feed.feed_id, catalog=feed)
    summary = json.loads(
        build_site(fixture_zip("branching"), site, MONDAY)["site.json"]
    )
    assert summary["country_code"] == "US"
    assert summary["country"] == "United States"
    assert summary["subdivision"] == "Oregon"


def test_footer_credits_publisher_download_and_catalog_license():
    feed = CatalogFeed(
        feedId="f-0123456789",
        name="Test",
        urls={"scheduled": ("https://example.org/g.zip",)},
        licenses=("https://example.org/license",),
        catalogLinks=("https://www.transit.land/feeds/f-test",),
    )
    site = Site(slug="test", feed=feed.feed_id, catalog=feed)
    home = build_site(fixture_zip("branching"), site, MONDAY)["index.html"].decode()
    assert 'href="https://example.org/g.zip"' in home
    assert 'href="https://example.org/license"' in home
    assert ">Transitland</a>" in home


def test_footer_without_a_license_defers_to_the_publisher():
    home = build("branching")["index.html"]
    assert "Schedule data from" in home
    assert "on the publisher's terms" in home
    assert "Listed in" not in home


def test_a_configured_license_wins_over_the_catalog():
    site = Site(
        slug="test",
        url="https://example.org/g.zip",
        license_url="https://example.org/own-license",
    )
    assert site.licenses() == ("https://example.org/own-license",)


def test_build_root():
    us = {"country_code": "US", "country": "United States"}
    summaries = [
        {"slug": "b", "title": "bravo", "routes": 1, "valid_through": "2026-10-01"}
        | us
        | {"subdivision": "Oregon"},
        {
            "slug": "a",
            "title": "Alpha",
            "routes": 2,
            "valid_through": "2026-12-01",
            "subdivision": "Maine",
        }
        | us,
        {"slug": "c", "title": "Charlie", "routes": 3},
        {"slug": "d", "title": "Delta", "routes": 1, "country_code": "CA"},
    ]
    summaries[1]["noindex"] = ["old/index.html"]
    pages = {
        "a": {
            "index.html": "2026-09-01",
            "r/index.html": "2026-10-05",
            "old/index.html": "2026-10-05",
        }
    }
    files = {
        k: v.decode() for k, v in build_root(summaries, MONDAY, pages, "k3y").items()
    }
    shard = f"sitemaps/{shard_of('a'):02d}.xml"
    assert set(files) == ROOT_PAGES | {
        "countries/us/index.html",
        "countries/ca/index.html",
        "countries/other/index.html",
        "countries/us/regions.json",
        "countries/ca/regions.json",
        "error.html",
        "style.css",
        "logo.svg",
        "root.js",
        "search.json",
        "world.json",
        "sitemap.xml",
        "sitemaps/root.xml",
        shard,
        "robots.txt",
        "k3y.txt",
    }
    home = files["index.html"]
    # By name, Other last.
    assert (
        home.index('href="countries/ca/"')
        < home.index('href="countries/us/"')
        < home.index('href="countries/other/"')
    )
    assert "United States</span>" in home and "2 agencies" in home
    assert re.search(r"\.gtfs\.zone<sup[^>]*>v\d+\.\d+\.\d+</sup>", home)
    assert 'href="https://gtfs.zone" target="_blank"' in home
    assert 'timetable-sites" target="_blank" rel="noopener">timetable-sites</a>' in home
    assert '"@type": "WebSite"' in home

    country = files["countries/us/index.html"]
    assert country.index('id="US-ME">Maine</h2>') < country.index(
        'id="US-OR">Oregon</h2>'
    )
    assert (
        'data-src="../../countries/us/regions.json"' in country
        and "geoBoundaries" in country
    )
    assert "United States Census Bureau" in country
    assert json.loads(files["countries/us/regions.json"])["license"] == "Public Domain"
    assert country.index('href="../../a/"') < country.index('href="../../b/"')
    assert "2 routes, to Dec 1, 2026" in country
    assert 'rounded-full bg-success" title="Current"' in country
    assert 'rounded-full bg-error" title="Expired"' in country
    assert '<link rel="stylesheet" href="../../style.css">' in country
    assert (
        '<link rel="canonical" href="https://sites.gtfs.zone/countries/us/">' in country
    )
    assert "<h2" not in files["countries/other/index.html"]

    index = files["sitemap.xml"]
    assert "<sitemapindex" in index
    assert "<loc>https://sites.gtfs.zone/sitemaps/root.xml</loc></sitemap>" in index
    assert (
        f"<loc>https://sites.gtfs.zone/{shard}</loc><lastmod>2026-10-05</lastmod>"
        in index
    )
    root_map = files["sitemaps/root.xml"]
    alternates = "".join(
        f'<xhtml:link rel="alternate" hreflang="{lang}" '
        f'href="https://sites.gtfs.zone/lang/{lang}/"/>'
        for lang in ROOT_LOCALES[1:]
    )
    assert (
        "<url><loc>https://sites.gtfs.zone/</loc>"
        '<xhtml:link rel="alternate" hreflang="en" href="https://sites.gtfs.zone/"/>'
        f"{alternates}"
        '<xhtml:link rel="alternate" hreflang="x-default" href="https://sites.gtfs.zone/"/>'
        "</url>"
    ) in root_map
    assert "<url><loc>https://sites.gtfs.zone/lang/fr/</loc>" in root_map
    assert "<loc>https://sites.gtfs.zone/countries/us/</loc>" in root_map
    sitemap = files[shard]
    assert (
        "<loc>https://sites.gtfs.zone/a/</loc><lastmod>2026-09-01</lastmod>" in sitemap
    )
    assert (
        "<loc>https://sites.gtfs.zone/a/r/</loc><lastmod>2026-10-05</lastmod>"
        in sitemap
    )
    assert "/a/old/" not in sitemap
    assert files["k3y.txt"] == "k3y"
    assert '<link rel="stylesheet" href="/style.css">' in files["error.html"]
    assert '<meta name="robots" content="noindex">' in files["error.html"]
    assert "Sitemap: https://sites.gtfs.zone/sitemap.xml" in files["robots.txt"]
    assert summaries[1]["valid_through"] == "2026-12-01"


def json_ld(page: str) -> list | dict:
    match = re.search(r'<script type="application/ld\+json">(.*?)</script>', page)
    assert match
    return json.loads(match.group(1))


def test_route_page_seo():
    files = build("overnight", title="Test & Co")
    page = files["1/index.html"]
    title = "1 Night Owl bus schedule - Test &amp; Co, United States"
    assert f"<title>{title}</title>" in page
    assert (
        '<meta name="description" content="1 Night Owl bus schedule between Alpha'
        " and Charlie: Daily. Times at each stop and first and last trips, from"
        ' Test &amp; Co, United States.">'
    ) in page
    assert '<meta property="og:title" content="1 Night Owl bus schedule' in page
    assert '<meta property="og:url" content="https://sites.gtfs.zone/test/1/">' in page
    assert (
        "The 1 Night Owl bus route runs between Alpha and Charlie, with Daily"
        " timetables.</p>"
    ) in page
    assert "First trip starts at 6:00 AM, last at 11:40 PM.</p>" in page
    crumbs, webpage = json_ld(page)
    crumbs = crumbs["itemListElement"]
    assert [c["item"] for c in crumbs] == [
        "https://sites.gtfs.zone/",
        "https://sites.gtfs.zone/test/",
        "https://sites.gtfs.zone/test/1/",
    ]
    assert crumbs[1]["name"] == "Test & Co"
    assert webpage["spatialCoverage"]["name"] == "United States"
    home = files["index.html"]
    title = "Test &amp; Co schedules and timetables - United States"
    assert f"<title>{title}</title>" in home
    assert (
        "Bus schedules for Test &amp; Co, United States: timetables for 1 route,"
        in home
    )
    assert "Bus timetables in" not in home
    assert len(json_ld(home)[0]["itemListElement"]) == 2


def test_page_digest_ignores_build_date():
    site = Site(slug="test", url="https://example.org/g.zip")
    body = fixture_zip("overnight")
    monday = build_site(body, site, today=MONDAY)["1/index.html"]
    tuesday = build_site(body, site, today=date(2026, 10, 6))["1/index.html"]
    assert monday != tuesday
    assert page_digest(monday) == page_digest(tuesday)
    assert page_digest(monday) != page_digest(monday.replace(b"11:40", b"11:45"))


def test_amenity_icons_and_feed_notes():
    files = build("amenities")
    page = files["7/index.html"]
    assert page.count('<symbol id="i-bike"') == 1
    assert 'aria-label="Bikes allowed"><use href="#i-bike"/>' in page
    assert "f</sup>" in page
    assert "<li>f: flag stop, stops only on request.</li>" in page
    # Every trip in the feed is accessible: said once, not marked per trip.
    for path in ("index.html", "7/index.html"):
        assert "All trips are wheelchair accessible.</p>" in files[path]
    assert "Wheelchair accessible trip." not in page
    assert "Filled dot" not in page


def test_no_icons_without_amenity_data():
    page = build("branching")["4/index.html"]
    assert "<symbol" not in page
    assert "amenities" not in page


def test_title_falls_back_to_the_catalog_name_for_blank_agencies():
    files = fixture_files("branching")
    files["agency.txt"] = (
        "agency_id,agency_name,agency_url,agency_timezone\n"
        "a,,https://example.org,America/New_York\n"
    )
    feed = CatalogFeed(feedId="f-0123456789", name="Miller Transportation")
    site = Site(slug="miller", feed=feed.feed_id, catalog=feed)
    assert site_title(read_feed(make_zip(files)), site) == "Miller Transportation"


def test_title_names_up_to_three_agencies():
    files = fixture_files("branching")
    files["agency.txt"] = (
        "agency_id,agency_name,agency_url,agency_timezone\n"
        + "".join(
            f"{c},{c.upper()} Lines,https://example.org,America/New_York\n"
            for c in "abcde"
        )
    )
    files["routes.txt"] = (
        "agency_id,route_id,route_short_name,route_long_name,route_type\n"
        + ("".join(f"{c},R{c},{c},{c},3\n" for c in "abcde"))
    )
    site = Site(slug="t", url="https://example.org/g.zip")
    assert (
        site_title(read_feed(make_zip(files)), site)
        == "A Lines, B Lines, C Lines, and 2 others"
    )


@pytest.mark.parametrize(
    ("url", "label"),
    [
        (
            "https://s3.amazonaws.com/datatools-511ny/public/Gloversville.zip",
            "s3.amazonaws.com: Gloversville.zip",
        ),
        (
            "https://bct.tmix.se/gtfs/?operatorIds=21",
            "bct.tmix.se: gtfs?operatorIds=21",
        ),
        ("https://example.org/", "example.org"),
    ],
)
def test_source_label(url, label):
    assert source_label(url) == label


def test_french_site():
    files = build("calendar-dates-only", locale="fr", time_format="24h")
    home, page = files["index.html"], files["2/index.html"]
    assert '<html lang="fr">' in home and '<html lang="fr">' in page
    assert "<title>Horaires de Test Transit - United States</title>" in home
    assert "Horaires valides du 10 oct. 2026 au 17 oct. 2026." in home
    assert '<h2 class="text-lg font-semibold">Samedi ' in page
    assert "Aucun service : sam. 24 oct., sam. 31 oct.</p>" in page
    assert "Toutes les lignes" in page
    assert "Généré le" in page and "5 oct. 2026" in page


def test_english_site_is_marked_english():
    assert '<html lang="en">' in build("branching")["index.html"]


def test_date_range_always_has_the_year():
    assert date_range(date(2026, 1, 2), date(2029, 12, 31)) == (
        "Jan 2, 2026 to Dec 31, 2029"
    )
    assert date_range(None, date(2026, 12, 1)) == "to Dec 1, 2026"
    assert date_range(date(2026, 1, 2), None) == "from Jan 2, 2026"
    assert date_range(None, None) == ""


def expired_files() -> dict[str, str]:
    """branching, plus a route whose own service ended in the spring."""
    files = fixture_files("branching")
    files["calendar.txt"] += "SPRING,1,1,1,1,1,0,0,20260301,20260515\n"
    files["routes.txt"] += "OLD,9,Old Line,3\n"
    files["trips.txt"] += "OLD,SPRING,old1,North,0\n"
    first_stop = files["stop_times.txt"].splitlines()[1].split(",")
    header = files["stop_times.txt"].splitlines()[0].split(",")
    row = dict(zip(header, first_stop, strict=True))
    lines = []
    for seq, (stop, time) in enumerate(
        [(row["stop_id"], "07:00:00"), ("X", "07:30:00")]
    ):
        row.update(trip_id="old1", stop_id=stop, stop_sequence=str(seq + 1))
        row.update(arrival_time=time, departure_time=time)
        lines.append(",".join(row[h] for h in header))
    files["stop_times.txt"] += "\n".join(lines) + "\n"
    return files


def test_expired_routes_keep_their_last_timetable():
    files = expired_files()
    stop = files["stops.txt"].splitlines()[1].split(",")
    files["stops.txt"] += ",".join(["X", "Xray", *stop[2:]]) + "\n"
    site = Site(slug="test", url="https://example.org/g.zip")
    feed = read_feed(make_zip(files))
    states = {
        rt.route.route_id: (rt.state, rt.state_date)
        for rt in site_timetables(feed, site, MONDAY)
    }
    assert states == {"R": ("", None), "OLD": ("expired", date(2026, 5, 15))}
    out = build_site(make_zip(files), site, today=MONDAY)
    home = out["index.html"].decode()
    assert home.index(">Forks<") < home.index('id="expired"') < home.index(">Old Line<")
    assert "Ended May 15, 2026" in home
    page = out["9/index.html"].decode()
    assert "service ended on May 15, 2026" in page and "7:00" in page
    summary = json.loads(out["site.json"])
    assert summary["routes"] == 2 and summary["expired_routes"] == 1
    # Only the expired route's page is kept out of search engines.
    assert summary["noindex"] == ["9/index.html"]
    assert NOINDEX in page and NOINDEX not in home


def test_site_not_indexed_is_noindex():
    files = build("branching", index=False)
    assert json.loads(files["site.json"])["noindex"] == ["4/index.html", "index.html"]
    assert NOINDEX in files["index.html"] and NOINDEX in files["4/index.html"]


def test_site_with_every_route_expired_is_noindex():
    site = Site(slug="test", url="https://example.org/g.zip")
    out = build_site(fixture_zip("branching"), site, today=date(2030, 1, 7))
    summary = json.loads(out["site.json"])
    assert summary["expired_routes"] == 1
    assert summary["noindex"] == ["4/index.html", "index.html"]
    assert NOINDEX in out["index.html"].decode()
    assert NOINDEX in out["4/index.html"].decode()


def split_route_files() -> dict[str, str]:
    """branching with its second north trip on a route of its own, named the
    same, and a route named apart."""
    files = fixture_files("branching")
    files["routes.txt"] += "R2,4,Forks,3\nR3,5,Forks,3\n"
    files["trips.txt"] = files["trips.txt"].replace(
        "R,DAILY,north2,", "R2,DAILY,north2,"
    )
    return files


def test_merge_routes_joins_routes_named_the_same():
    feed = merge_routes(read_feed(make_zip(split_route_files())))
    assert set(feed.routes) == {"R", "R3"}
    assert feed.trips["north2"].route_id == "R"
    site = Site(slug="test", url="https://example.org/g.zip")
    out = build_site(make_zip(split_route_files()), site, today=MONDAY)
    assert "4/index.html" in out and "4-2/index.html" not in out
    assert json.loads(out["content.json"])["counts"]["routes"] == 3


def test_upcoming_routes_get_their_first_timetable():
    files = expired_files()
    files["calendar.txt"] = files["calendar.txt"].replace(
        "SPRING,1,1,1,1,1,0,0,20260301,20260515",
        "SPRING,1,1,1,1,1,0,0,20261201,20261231",
    )
    stop = files["stops.txt"].splitlines()[1].split(",")
    files["stops.txt"] += ",".join(["X", "Xray", *stop[2:]]) + "\n"
    site = Site(slug="test", url="https://example.org/g.zip")
    out = build_site(make_zip(files), site, today=MONDAY)
    home = out["index.html"].decode()
    assert 'id="expired"' not in home and "Starts Dec 1, 2026" in home
    assert "service starts on Dec 1, 2026" in out["9/index.html"].decode()


def test_root_groups_by_region_and_writes_maps():
    us = {"country_code": "US", "country": "United States", "routes": 1}
    summaries = [
        # Boston, with no catalog subdivision.
        {"slug": "a", "title": "Alpha", "lat": 42.36, "lon": -71.06} | us,
        # Named by code only.
        {"slug": "b", "title": "Bravo", "subdivision": "MA"} | us,
        # A point wins over a wrong name.
        {
            "slug": "c",
            "title": "Charlie",
            "subdivision": "Oregon",
            "lat": 47.6,
            "lon": -122.33,
        }
        | us,
        {"slug": "d", "title": "Delta", "subdivision": "Atlantis"} | us,
    ]
    files = build_root(summaries, MONDAY)
    country = files["countries/us/index.html"].decode()
    assert country.count("<h2") == 3
    # By name; a section with no region has no anchor.
    assert (
        country.index('<h2 class="scroll-mt-4 text-xl font-semibold">Atlantis</h2>')
        < country.index('id="US-MA">Massachusetts</h2>')
        < country.index('id="US-WA">Washington</h2>')
    )
    regions = json.loads(files["countries/us/regions.json"])
    counts = {
        f["properties"]["id"]: f["properties"]["count"] for f in regions["features"]
    }
    assert counts["US-MA"] == 2 and counts["US-WA"] == 1 and counts["US-OR"] == 0
    world = json.loads(files["world.json"])
    counts = {
        f["properties"]["key"]: f["properties"]["count"] for f in world["features"]
    }
    assert counts["us"] == 4 and counts["fr"] == 0
    assert json.loads(files["search.json"]) == {"feeds": []}


def test_block_continuations_stop_codes_and_platforms():
    files = build("blocks")
    page = files["1/index.html"]
    # A lettered note on the column, linked in the legend
    link = '<a class="link link-hover" href="../2/">Continues as 2 to Charlie</a>'
    assert f"<li>A: {link}.</li>" in page
    assert '<tr><td></td><th scope="col">A</th>' in page
    # Turning back on the same route is the next column, not a note; q2
    # starts 70 minutes after r4 ends: no continuation
    assert "Continues to" not in page
    assert page.count("Continues as") == 1
    assert "<tfoot>" not in page
    assert '<small class="code">101</small>' in page
    assert "Central</a> <small" in page
    assert '<small class="code">Platform 1</small>' in page
    assert "Valid until Dec 31, 2026." in page


def test_validity_names_a_start_after_today():
    site = Site(slug="test", url="https://example.org/g.zip")
    files = build_site(fixture_zip("blocks"), site, today=date(2025, 12, 1))
    page = files["1/index.html"].decode()
    assert "Valid from Jan 1, 2026 to Dec 31, 2026." in page


def test_feed_info_dates_shown_beside_the_calendars():
    files = fixture_files("blocks")
    files["feed_info.txt"] = (
        "feed_publisher_name,feed_publisher_url,feed_lang,"
        "feed_start_date,feed_end_date\n"
        "Example,https://example.org,en,20260101,20261016\n"
    )
    site = Site(slug="test", url="https://example.org/g.zip")
    built = build_site(make_zip(files), site, today=MONDAY)
    index = built["index.html"].decode()
    assert "Timetables valid from Jan 1, 2026 to Dec 31, 2026." in index
    assert "The feed says it is valid from Jan 1, 2026 to Oct 16, 2026." in index
    assert "Valid until Dec 31, 2026." in built["1/index.html"].decode()
    summary = json.loads(built["site.json"])
    assert summary["valid_through"] == "2026-12-31"


def test_feed_info_dates_matching_the_calendars_add_no_line():
    files = fixture_files("blocks")
    files["feed_info.txt"] = (
        "feed_publisher_name,feed_publisher_url,feed_lang,feed_end_date\n"
        "Example,https://example.org,en,20261231\n"
    )
    site = Site(slug="test", url="https://example.org/g.zip")
    index = build_site(make_zip(files), site, today=MONDAY)["index.html"].decode()
    assert "The feed says" not in index


def test_route_page_fare_card():
    files = build("fares-flat-v1")
    assert "<p>Fare: $1.25, no free transfers.</p>" in files["1/index.html"]
    assert "<p>Fare: $2.00, 1 free transfer within 60 min.</p>" in files["x/index.html"]
    assert "Fare:" not in build("overnight")["1/index.html"]


def test_route_page_fare_card_lists_prices():
    files = fixture_files("fares-zone-v1")
    files["stop_times.txt"] = "\n".join(files["stop_times.txt"].splitlines()[:3])
    site = Site(slug="test", url="https://example.org/g.zip", locale="fr")
    page = build_site(make_zip(files), site, today=MONDAY)
    html = next(
        body.decode()
        for path, body in page.items()
        if path.endswith("/index.html") and path != "fares/index.html"
    )
    assert "<p>Tarifs :</p>" in html
    assert "<li>190\xa0JPY payé avant de monter</li>" in html


def test_route_page_zone_fare_chart_and_badges():
    files = build("fares-zone-v1")
    page = next(
        body
        for path, body in files.items()
        if path.endswith("/index.html") and path != "fares/index.html"
    )
    assert "<summary" in page and "Fares by zone</summary>" in page
    assert '<th scope="col"><span>Zone 2</span></th>' in page
    assert '<small class="code fare-zone">Zone 1</small>' in page


def test_fares_page_linked_from_index_and_route():
    files = build("fares-flat-v1")
    page = files["fares/index.html"]
    assert "<li>$1.25 paid on board, no free transfers</li>" in page
    assert "<li>$2.00 paid on board, 1 free transfer within 60 min</li>" in page
    assert NOINDEX not in page
    assert '<a class="link" href="fares/">Fares</a>' in files["index.html"]
    assert '<a class="link" href="../fares/">All fares</a>' in files["1/index.html"]


def test_fares_page_v2_by_network_with_transfers():
    page = build("fares-v2")["fares/index.html"]
    assert '<h2 class="card-title">Local Bus</h2>' in page
    assert "<li>$2.00 with Cash (Bus fare)</li>" in page
    assert (
        "<li>Bus fare (Local Bus) to Peak rail fare / Zone 1 rail fare / Zone 2"
        " rail fare (Commuter Rail, from Zone 1): free, within 120 min</li>"
    ) in page


def test_no_fares_page_without_fares():
    files = build("overnight")
    assert "fares/index.html" not in files
    assert 'href="fares/"' not in files["index.html"]


def test_fares_page_noindex_when_site_is_not_indexed():
    files = build("fares-flat-v1", index=False)
    assert NOINDEX in files["fares/index.html"]
    assert "fares/index.html" in json.loads(files["site.json"])["noindex"]


def test_unreadable_fares_are_named_not_fatal():
    files = fixture_files("fares-flat-v1")
    files["fare_attributes.txt"] = files["fare_attributes.txt"].replace(
        "a,local,1.25,USD,0", "a,local,1.25,USD,cash"
    )
    site = Site(slug="test", url="https://example.org/g.zip")
    pages = {
        path: body.decode()
        for path, body in build_site(make_zip(files), site, today=MONDAY).items()
    }
    note = (
        "<p>The feed's fares could not be read: fare_attributes.txt:"
        " payment_method &#39;cash&#39; is not valid</p>"
    )
    assert note in pages["fares/index.html"]
    assert NOINDEX in pages["fares/index.html"]
    assert note in pages["1/index.html"]
    assert "Fare:" not in pages["1/index.html"]
    assert '<a class="link" href="fares/">Fares</a>' in pages["index.html"]
    content = json.loads(pages["content.json"])
    assert content["faresError"].startswith("fare_attributes.txt: payment_method")
    assert "faresError" not in json.loads(build("fares-flat-v1")["content.json"])


def test_flex_feed_with_zone_fares_builds():
    page = next(
        body
        for path, body in build("flex-fares").items()
        if path.endswith("/index.html") and path != "fares/index.html"
    )
    assert "Fares by zone</summary>" in page


def test_unknown_language_code_named_on_index():
    files = fixture_files("branching")
    files["agency.txt"] = (
        "agency_id,agency_name,agency_url,agency_timezone,agency_lang\n"
        "a,Test Transit,https://example.org,America/New_York,english\n"
    )
    site = Site(slug="test", url="https://example.org/g.zip")
    pages = build_site(make_zip(files), site, today=MONDAY)
    assert json.loads(pages["site.json"])["languages"] == ["en"]
    assert (
        "<p>The feed's language code english is not understood.</p>"
        in pages["index.html"].decode()
    )


def test_a_page_set_per_feed_language():
    files = build("languages")
    pages = {path for path in files if path.endswith(".html")}
    # fr (feed_lang) at the root, en (translations.txt) under en/.
    assert pages == {
        "index.html",
        "1/index.html",
        "traverse/index.html",
        "en/index.html",
        "en/1/index.html",
        "en/traverse/index.html",
    }
    assert json.loads(files["site.json"])["languages"] == ["fr", "en"]
    home, en_home = files["index.html"], files["en/index.html"]
    assert '<html lang="fr">' in home and 'data-mode="ferry">Traversier<' in home
    assert '<html lang="en">' in en_home and 'data-mode="ferry">Ferry<' in en_home
    alternates = (
        '<link rel="alternate" hreflang="fr" href="https://sites.gtfs.zone/test/">\n'
        '<link rel="alternate" hreflang="en" href="https://sites.gtfs.zone/test/en/">\n'
        '<link rel="alternate" hreflang="x-default" href="https://sites.gtfs.zone/test/">'
    )
    assert alternates in home and alternates in en_home
    assert ' href="./" lang="fr" hreflang="fr" aria-current="page">Français</a>' in home
    assert ' href="en/" lang="en" hreflang="en">English</a>' in home
    assert ' href="../" lang="fr" hreflang="fr">Français</a>' in en_home
    # The en set shares the site's assets and data files.
    assert '<link rel="stylesheet" href="../style.css">' in en_home
    assert 'data-src="../map.json"' in en_home
    assert 'href="../../" aria-label="sites.gtfs.zone"' in en_home
    route = files["en/1/index.html"]
    assert '<link rel="canonical" href="https://sites.gtfs.zone/test/en/1/">' in route
    assert ' href="../../1/" lang="fr" hreflang="fr">Français</a>' in route
    assert 'data-src="../../1/live.json"' in route
    assert '<script type="module" src="../../map.js"></script>' in route
    assert 'href="../">All routes</a>' in route
    assert "en/1/live.json" not in files


def test_page_sets_show_translated_feed_text():
    files = build("languages")
    fr, en = files["1/index.html"], files["en/1/index.html"]
    assert "Gare centrale" in fr and "Central Station" not in fr
    assert "Central Station" in en and "City Hall" in en and "Downtown" in en
    assert "Gare centrale" not in en and "Hôtel de ville" not in en
    # Untranslated text stays the feed's.
    assert "Quai nord" in files["en/traverse/index.html"]
    assert "Transit Corporation" in files["en/index.html"]
    assert "Société de transport" in files["index.html"]
    # The shared system map keeps the default language's text.
    assert '"title":"1 Centre-ville"' in files["map.json"]


def test_kana_readings_beside_names():
    translations = (
        "table_name,field_name,language,translation,record_id\n"
        "stops,stop_name,ja-Hrkt,がーる,A\n"
        "stops,stop_name,en,Central Station,A\n"
        "stops,stop_name,ja-Hrkt,おてる,B\n"
    )
    body = fixture_files("languages") | {"translations.txt": translations}
    files = build_site(make_zip(body), Site(slug="test", url="u"), today=MONDAY)
    fr, en = files["1/index.html"].decode(), files["en/1/index.html"].decode()
    reading = 'class="reading text-base-content/70" lang="ja-Hrkt">がーる</small>'
    assert reading in fr
    assert "おてる" in fr
    # A name shown translated has no reading; an untranslated one keeps it.
    assert "がーる" not in en and "おてる" in en
    assert json.loads(files["site.json"])["languages"] == ["fr", "en"]


def test_a_language_without_a_catalog_has_english_text():
    files = build("languages", locale="eu")
    home = files["index.html"]
    assert '<html lang="eu">' in home and 'data-mode="ferry">Ferry<' in home
    assert ' href="./" lang="eu" hreflang="eu" aria-current="page">Euskara</a>' in home
    # The pin replaces the feed's default (fr); en stays.
    assert json.loads(files["site.json"])["languages"] == ["eu", "en"]


def test_a_one_language_site_has_no_picker():
    home = build("branching")["index.html"]
    assert "hreflang" not in home and "dropdown" not in home


def test_other_language_pages_follow_the_site_noindex():
    summary = json.loads(build("languages", index=False)["site.json"])
    assert "en/index.html" in summary["noindex"]
    assert "en/1/index.html" in summary["noindex"]


def test_sitemap_lists_language_alternates():
    summaries = [{"slug": "a", "title": "A", "routes": 1, "languages": ["fr", "en"]}]
    summaries[0]["noindex"] = ["en/old/index.html"]
    pages = {
        "a": {
            "index.html": "2026-09-01",
            "en/index.html": "2026-09-02",
            "old/index.html": "2026-09-01",
            "en/old/index.html": "2026-09-01",
        }
    }
    sitemap = build_root(summaries, MONDAY, pages)[
        f"sitemaps/{shard_of('a'):02d}.xml"
    ].decode()
    assert (
        "<url><loc>https://sites.gtfs.zone/a/en/</loc><lastmod>2026-09-02</lastmod>"
        '<xhtml:link rel="alternate" hreflang="fr" href="https://sites.gtfs.zone/a/"/>'
        '<xhtml:link rel="alternate" hreflang="en" href="https://sites.gtfs.zone/a/en/"/>'
        '<xhtml:link rel="alternate" hreflang="x-default" href="https://sites.gtfs.zone/a/"/>'
        "</url>"
    ) in sitemap
    # A page whose other version is noindex has no alternates.
    assert (
        "<url><loc>https://sites.gtfs.zone/a/old/</loc><lastmod>2026-09-01</lastmod></url>"
        in sitemap
    )
    assert "a/en/old/" not in sitemap


def test_root_and_country_pages_per_ui_language():
    ca = {
        "country_code": "CA",
        "country": "Canada",
        "routes": 1,
        "valid_through": "2026-12-01",
    }
    summaries = [
        {"slug": "a", "title": "Alpha", "languages": ["en"]} | ca,
        {"slug": "b", "title": "Bravo", "languages": ["en", "fr"]} | ca,
        {
            "slug": "c",
            "title": "Charlie",
            "languages": ["fr", "en"],
            "titles": {"en": "Charles"},
        }
        | ca,
        {"slug": "d", "title": "Delta", "languages": ["fr"], "country_code": "FR"},
        {"slug": "e", "title": "Echo", "languages": ["hur"], "country_code": "JP"},
    ]
    files = {k: v.decode() for k, v in build_root(summaries, MONDAY).items()}
    pages = {k for k in files if k.endswith("index.html")}
    # Canada: en on 3 sites, fr on 2; France: fr only; Japan: no catalog, en.
    assert pages == ROOT_PAGES | {
        "countries/ca/index.html",
        "countries/ca/fr/index.html",
        "countries/fr/index.html",
        "countries/jp/index.html",
    }

    ca_en = files["countries/ca/index.html"]
    assert '<html lang="en">' in ca_en
    assert 'hreflang="fr" href="https://sites.gtfs.zone/countries/ca/fr/"' in ca_en
    assert 'hreflang="x-default" href="https://sites.gtfs.zone/countries/ca/"' in ca_en
    # Each site in its en set, by its en title.
    assert 'href="../../b/"' in ca_en and 'href="../../c/en/"' in ca_en
    assert ">Charles<" in ca_en and ">Charlie<" not in ca_en
    assert 'href="../../countries/ca/fr/" lang="fr" hreflang="fr"' in ca_en

    ca_fr = files["countries/ca/fr/index.html"]
    assert '<html lang="fr">' in ca_fr and "<h1" in ca_fr
    assert ">Canada</h1>" in ca_fr
    assert 'href="../../../b/fr/"' in ca_fr and 'href="../../../c/"' in ca_fr
    assert 'href="../../../a/"' in ca_fr and ">Charlie<" in ca_fr
    assert 'data-src="../../../countries/ca/regions.json"' in ca_fr
    assert 'href="../../../lang/fr/"' in ca_fr
    assert '<link rel="stylesheet" href="../../../style.css">' in ca_fr

    # One language: no picker or alternates; France defaults to fr.
    fr = files["countries/fr/index.html"]
    assert '<html lang="fr">' in fr and "hreflang" not in fr
    assert '<html lang="en">' in files["countries/jp/index.html"]

    root_fr = files["lang/fr/index.html"]
    assert '<html lang="fr">' in root_fr
    assert 'src="../../root.js"' in root_fr
    assert 'data-src="../../search.json"' in root_fr
    assert 'href="../../countries/ca/fr/" data-key="ca"' in root_fr
    assert 'href="../../countries/fr/" data-key="fr"' in root_fr
    assert 'href="../../countries/jp/" data-key="jp"' in root_fr
    # Country names from CLDR in the page's language.
    assert ">Japon</span>" in root_fr and ">Japan</span>" in files["index.html"]
    assert 'href="./" lang="en"' not in root_fr
    assert 'href="../../" lang="en" hreflang="en"' in root_fr
    assert 'href="lang/fr/" lang="fr" hreflang="fr"' in files["index.html"]

    root_map = files["sitemaps/root.xml"]
    assert (
        "<url><loc>https://sites.gtfs.zone/countries/ca/fr/</loc>"
        '<xhtml:link rel="alternate" hreflang="en" href="https://sites.gtfs.zone/countries/ca/"/>'
        '<xhtml:link rel="alternate" hreflang="fr" href="https://sites.gtfs.zone/countries/ca/fr/"/>'
        '<xhtml:link rel="alternate" hreflang="x-default" href="https://sites.gtfs.zone/countries/ca/"/>'
        "</url>"
    ) in root_map
    assert "<url><loc>https://sites.gtfs.zone/countries/fr/</loc></url>" in root_map


def test_country_languages_most_offered_first():
    assert country_languages([]) == ["en"]
    assert country_languages([{"languages": ["hur"]}]) == ["en"]
    assert country_languages(
        [{"languages": ["fr", "hur"]}, {"languages": ["hur", "fr"]}, {}]
    ) == ["fr"]
    assert country_languages([{"languages": ["fr"]}, {"languages": ["en", "fr"]}]) == [
        "fr",
        "en",
    ]
    assert country_languages([{"languages": ["fr"]}, {"languages": ["en"]}]) == [
        "en",
        "fr",
    ]


def windows_files() -> dict[str, str]:
    """expired_files, plus copies of route R's trips a minute later on a
    service that ended in March and one that starts next March, and a route
    that starts in December."""
    files = expired_files()
    stop = files["stops.txt"].splitlines()[1].split(",")
    files["stops.txt"] += ",".join(["X", "Xray", *stop[2:]]) + "\n"
    files["calendar.txt"] += (
        "PAST,1,1,1,1,1,1,1,20260101,20260331\n"
        "NEXT,1,1,1,1,1,1,1,20270301,20271231\n"
        "WINTER,1,1,1,1,1,0,0,20261201,20261231\n"
    )
    files["routes.txt"] += "NEW,7,New Line,3\n"
    trips = files["trips.txt"].splitlines()
    times = files["stop_times.txt"].splitlines()
    for service, suffix in (("PAST", "_p"), ("NEXT", "_n")):
        for line in trips[1:]:
            route, _, trip, *rest = line.split(",")
            if route == "R":
                files["trips.txt"] += ",".join([route, service, trip + suffix, *rest])
                files["trips.txt"] += "\n"
        for line in times[1:]:
            trip, arrival, _, *rest = line.split(",")
            if trip == "old1":
                continue
            later = arrival[:4] + "1" + arrival[5:]
            files["stop_times.txt"] += ",".join([trip + suffix, later, later, *rest])
            files["stop_times.txt"] += "\n"
    files["trips.txt"] += "NEW,WINTER,new1,North,0\n"
    files["stop_times.txt"] += (
        "new1,06:00:00,06:00:00,A,1\nnew1,06:30:00,06:30:00,X,2\n"
    )
    return files


def test_date_filter_reads_only_trips_in_route_windows():
    site = Site(slug="test", url="https://example.org/g.zip")
    feed = site_feed(make_zip(windows_files()), site, MONDAY)
    route_r = {"north1", "north2", "east1", "back_full", "back_short", "back_skip"}
    assert set(feed.trips) == route_r | {"old1", "new1"}
    assert feed.route_services["R"] == {"DAILY", "PAST", "NEXT"}
    assert feed.trip_count == 20
    assert {"PAST", "NEXT"} <= set(feed.calendars)
    assert window_services(feed, MONDAY, 28) == {"DAILY", "SPRING", "WINTER"}


def test_date_filter_builds_the_same_files(monkeypatch):
    site = Site(slug="test", url="https://example.org/g.zip")
    body = make_zip(windows_files())
    filtered = build_site(body, site, today=MONDAY)
    states = {
        rt.route.route_id: rt.state
        for rt in site_timetables(site_feed(body, site, MONDAY), site, MONDAY)
    }
    assert states == {"R": "", "OLD": "expired", "NEW": "upcoming"}

    def unfiltered(source, site, today):
        return read_feed(
            source, keep_route=lambda r: site.routes.accepts(r.route_id, r.route_type)
        )

    # The place's and the content report's centers are weighted by the
    # stop_times read, so they move.
    def without_center(files: dict[str, bytes]) -> dict[str, bytes]:
        return {path: GEO.sub(b"", body) for path, body in files.items()}

    monkeypatch.setattr(build_module, "site_feed", unfiltered)
    whole = build_site(body, site, today=MONDAY)
    assert whole != filtered
    assert without_center(whole) == without_center(filtered)


def test_root_search_index():
    catalog = {
        "feeds": [
            {
                "i": "f-1",
                "n": "Charlie",
                "a": ["C Transit"],
                "p": ["Halifax", "Canada"],
                "pt": ["Dartmouth"],
                "cc": "CA",
                "ll": [44.6, -63.6],
                "st": "partial",
                "rs": {"scheduled": "up", "vehicles": "down"},
                "u": {
                    "scheduled": ["https://a.example/gtfs.zip", "http://a.example/x"],
                    "vehicles": ["https://user:key@rt.example:8443/vp?key=1"],
                },
                "b": 1234,
                "m": "2026-10-01",
                "ov": [["f-2", "shares"]],
            },
            {"i": "f-2", "n": "No page", "st": "down", "rs": {}, "u": {}},
        ]
    }
    summaries = [
        {
            "slug": "c",
            "title": "Charlie",
            "routes": 1,
            "valid_through": "2026-12-01",
            "languages": ["fr", "en"],
        }
    ]
    files = build_root(summaries, MONDAY, catalog=catalog, slugs={"f-1": "c"})
    assert json.loads(files["search.json"]) == {
        "feeds": [
            {
                "i": "c",
                "n": "Charlie",
                "a": ["C Transit"],
                "p": ["Halifax", "Canada"],
                "pt": ["Dartmouth"],
                "cc": "CA",
                "st": "partial",
                "m": "2026-10-01",
                "rs": {"vehicles": "down"},
                "u": {
                    "scheduled": ["https://a.example"],
                    "vehicles": ["https://rt.example"],
                },
                "lg": ["en"],
            },
            {"i": "", "n": "No page", "st": "down", "rs": {}, "u": {}},
        ]
    }


SOURCE_STATS = {
    "generated_at": "2026-11-23T13:10:29+00:00",
    "catalogs": {
        "ntd": {
            "entries": 3,
            "feeds": 2,
            "scheduleFeeds": 2,
            "onlyHere": 1,
            "fetch": {"ok": 2},
            "facts": {"ok": 1, "not_zip": 1},
            "bytes": 2_500_000_000,
            "distinctBytes": 2_000_000_000,
            "countries": 1,
        },
        "mobilitydatabase": {
            "entries": 5,
            "feeds": 4,
            "scheduleFeeds": 4,
            "onlyHere": 3,
            "fetch": {"ok": 1, "http_error": 2, "new_outcome": 1},
            "facts": {"ok": 1},
            "bytes": 29_435_589,
            "distinctBytes": 29_435_589,
            "countries": 2,
        },
    },
    "combinations": [
        {"catalogs": ["mobilitydatabase"], "feeds": 3},
        {"catalogs": ["mobilitydatabase", "ntd"], "feeds": 1},
    ],
    "pairs": [
        {
            "catalogs": ["mobilitydatabase", "ntd"],
            "feeds": 1,
            "scheduleUrls": 1,
            "bodies": 0,
        }
    ],
    "duplication": {
        "feeds": 5,
        "multiEntry": 1,
        "byEvidence": {"url": 1, "sha256": 2},
        "entriesPerFeed": {"10": 1, "2": 1, "1": 3},
    },
}


def test_sources_page_content():
    stats = sources_page(SOURCE_STATS)
    assert stats["generated"] == date(2026, 11, 23)
    assert [c["name"]["name"] for c in stats["catalogs"]] == [
        "Mobility Database",
        "National Transit Database",
    ]
    assert stats["catalogs"][0]["only_share"] == 0.75
    assert [(r["key"], r["counts"], r["known"]) for r in stats["fetch"]] == [
        ("ok", [1, 2], True),
        ("http_error", [2, 0], True),
        ("new_outcome", [1, 0], False),
    ]
    assert [r["key"] for r in stats["evidence"]] == ["sha256", "url"]
    assert stats["entries_per_feed"] == [(1, 3), (2, 1), (10, 1)]
    assert sources_page({})["catalogs"] == []


def test_sources_pages_in_every_root_language():
    files = build_root([], MONDAY, sources=SOURCE_STATS)
    en = files["sources/index.html"].decode()
    assert '<meta name="robots" content="noindex">' in en
    for text in ("Mobility Database", "2.5 GB", "29 MB", "75%"):
        assert text in en
    assert "<code>new_outcome</code>" in en
    assert "Mobility Database + National Transit Database" in en
    fr = files["lang/fr/sources/index.html"].decode()
    assert "Sources des flux" in fr and "2,5\u202fGo" in fr
    assert 'href="sources/"' in files["index.html"].decode()
    assert 'href="../../../sources/"' in fr
    sitemap = files["sitemaps/root.xml"].decode()
    assert "sources" not in sitemap
    assert "sources/index.html" not in build_root([], MONDAY)


def test_flex_route_pages_show_areas_windows_and_booking():
    files = build("flex")
    deviated, zone = files["5/index.html"], files["dial-a-ride/index.html"]
    assert "<i>area_2</i>" in deviated
    assert "In italics: an area the feed gives no name" in deviated
    assert '<span class="pm">10:00</span>' not in deviated
    assert "10:00</span>-<span>11:00</span>" in deviated
    assert '<a class="link" href="tel:555-0100">Call 555-0100</a>' in deviated
    assert "b2: must be booked, see Booking." in deviated
    assert "Any of 2 stops</summary>Alpha, Bravo</details>" in zone
    assert '<span class="pm">7:00</span>' in zone
    assert "PM times are in bold." in zone

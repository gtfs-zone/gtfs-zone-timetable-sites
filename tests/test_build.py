import json
import re
from datetime import date

import pytest
from conftest import fixture_files, fixture_zip, make_zip

from gtfs_zone_timetable_sites.build import (
    build_root,
    build_site,
    page_digest,
    site_status,
    site_timetables,
    site_title,
    source_label,
    unavailable_reason,
)
from gtfs_zone_timetable_sites.catalog import shard_of
from gtfs_zone_timetable_sites.config import CatalogFeed, Site
from gtfs_zone_timetable_sites.gtfs.reader import read_feed
from gtfs_zone_timetable_sites.pages import date_range

MONDAY = date(2026, 10, 5)


def build(name: str, **options) -> dict[str, str]:
    site = Site(slug="test", url="https://example.org/g.zip", **options)
    files = build_site(fixture_zip(name), site, today=MONDAY)
    return {path: body.decode() for path, body in files.items()}


def test_site_files():
    files = build("branching", title="Test & Co")
    assert set(files) == {
        "index.html",
        "4/index.html",
        "site.json",
        "content.json",
        "style.css",
        "logo.svg",
    }
    home = files["index.html"]
    assert "Test &amp; Co</h1>" in home
    assert 'href="4/">' in home
    assert '<link rel="canonical" href="https://sites.gtfs.zone/test/">' in home


def test_route_page_has_timetable():
    page = build("overnight")["1/index.html"]
    assert '<section class="card bg-base-100 shadow-sm" id="daily">' in page
    assert "Charlie</a> <small>CDT time</small></th>" in page
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


def test_site_with_basemap():
    files = build("branching", basemap="stadia-toner")
    assert set(files) == {
        "index.html",
        "4/index.html",
        "site.json",
        "content.json",
        "style.css",
        "logo.svg",
    }
    home = files["index.html"]
    assert '<link rel="stylesheet" href="style.css">' in home
    assert "--color-primary" not in home
    page = files["4/index.html"]
    assert '<link rel="stylesheet" href="../style.css">' in page
    assert "<nav" not in page.split("<main", 1)[1]
    assert "<details" not in page
    assert '<section class="card bg-base-100 shadow-sm" id="' in page
    assert "stamen_toner/" in page
    assert '<link rel="icon" type="image/svg+xml" href="../logo.svg">' in page
    assert 'href="../../" aria-label="sites.gtfs.zone"' in page
    assert '<a href="4/" aria-label="4 Forks" data-cap="4 Forks">' in home
    rows = re.findall(r'<th scope="row">.*?</th>', page, re.S)
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
    assert '<h2 class="card-title">Maps</h2>' in home
    maps = home.split('<h2 class="card-title">Maps</h2>', 1)[1]
    assert maps.index('<h3 class="font-semibold">Subway</h3>') < maps.index(
        '<h3 class="font-semibold">Bus</h3>'
    )
    assert maps.count('<div class="map') == 2 and home.count("<script>") == 1


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
    delta = re.search(r'<th scope="row">.*?Delta</a>', page)[0]
    assert "C20,80 34,70 34,100" in delta
    echo = re.search(r'<th scope="row">[^\n]*?Echo</a>[^\n]*?</th>', page)[0]
    charlie = re.search(r'<th scope="row">[^\n]*?Charlie</a>[^\n]*?</th>', page)[0]
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
        "country_code": None,
        "country": None,
        "subdivision": None,
    }


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
    pages = {"a": {"index.html": "2026-09-01", "r/index.html": "2026-10-05"}}
    files = {
        k: v.decode() for k, v in build_root(summaries, MONDAY, pages, "k3y").items()
    }
    shard = f"sitemaps/{shard_of('a'):02d}.xml"
    assert set(files) == {
        "index.html",
        "countries/us/index.html",
        "countries/ca/index.html",
        "countries/other/index.html",
        "error.html",
        "style.css",
        "logo.svg",
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
    assert country.index(">Maine</h2>") < country.index(">Oregon</h2>")
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
    assert "<url><loc>https://sites.gtfs.zone/</loc></url>" in root_map
    assert "<loc>https://sites.gtfs.zone/countries/us/</loc>" in root_map
    sitemap = files[shard]
    assert (
        "<loc>https://sites.gtfs.zone/a/</loc><lastmod>2026-09-01</lastmod>" in sitemap
    )
    assert (
        "<loc>https://sites.gtfs.zone/a/r/</loc><lastmod>2026-10-05</lastmod>"
        in sitemap
    )
    assert files["k3y.txt"] == "k3y"
    assert '<link rel="stylesheet" href="/style.css">' in files["error.html"]
    assert '<meta name="robots" content="noindex">' in files["error.html"]
    assert "Sitemap: https://sites.gtfs.zone/sitemap.xml" in files["robots.txt"]
    assert summaries[1]["valid_through"] == "2026-12-01"


def json_ld(page: str) -> dict:
    match = re.search(r'<script type="application/ld\+json">(.*?)</script>', page)
    assert match
    return json.loads(match.group(1))


def test_route_page_seo():
    files = build("overnight", title="Test & Co")
    page = files["1/index.html"]
    assert "<title>1 Night Owl bus schedule - Test &amp; Co</title>" in page
    assert (
        '<meta name="description" content="1 Night Owl bus schedule between Alpha'
        " and Charlie: Daily. Times at each stop and first and last trips, from"
        ' Test &amp; Co.">'
    ) in page
    assert '<meta property="og:title" content="1 Night Owl bus schedule' in page
    assert '<meta property="og:url" content="https://sites.gtfs.zone/test/1/">' in page
    assert (
        "The 1 Night Owl bus route runs between Alpha and Charlie, with Daily"
        " timetables.</p>"
    ) in page
    assert "First trip starts at 6:00 AM, last at 11:40 PM.</p>" in page
    crumbs = json_ld(page)["itemListElement"]
    assert [c["item"] for c in crumbs] == [
        "https://sites.gtfs.zone/",
        "https://sites.gtfs.zone/test/",
        "https://sites.gtfs.zone/test/1/",
    ]
    assert crumbs[1]["name"] == "Test & Co"
    home = files["index.html"]
    assert "<title>Test &amp; Co schedules and timetables</title>" in home
    assert 'content="Bus schedules for Test &amp; Co: timetables for 1 route,' in home
    assert len(json_ld(home)["itemListElement"]) == 2


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
        == "A Lines, B Lines, C Lines and 2 others"
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
    assert "<title>Horaires de Test Transit</title>" in home
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

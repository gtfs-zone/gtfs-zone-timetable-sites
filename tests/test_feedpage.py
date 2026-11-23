from datetime import date

from gtfs_zone_timetable_sites.build import build_feed_page, feed_indexable
from gtfs_zone_timetable_sites.config import CatalogFeed, OverlapLink, Site
from gtfs_zone_timetable_sites.feedpage import (
    check_text,
    dataset,
    editor_url,
    feed_view,
    overlaps_view,
    route_overlaps,
    viewer_url,
)

TODAY = date(2026, 11, 15)


def row(row_id, catalog="transitland", kind="static", check=None, **fields):
    return {
        "rowId": row_id,
        "kind": kind,
        "feedId": row_id.split(":")[1],
        "name": "Metro",
        "catalog": catalog,
        "check": check,
    } | fields


def metro(**fields):
    return CatalogFeed.model_validate(
        {
            "feedId": "f-0000000001",
            "name": "Metro",
            "subtitle": "Buses",
            "state": "partial",
            "since": "2026-09-23T13:56:17+00:00",
            "roleState": {"scheduled": "up", "vehicles": "down"},
            "urls": {
                "scheduled": ["https://x/g.zip"],
                "vehicles": ["https://x/vp.pb"],
            },
            "content": {
                "state": "ok",
                "checked": "2026-11-14",
                "serviceStart": "2026-01-01",
                "serviceEnd": "2026-12-31",
                "routes": 12,
                "stops": 1234,
                "trips": 5000,
            },
            "staticBytes": 22_280_410,
            "licenses": ["https://x/license"],
            "country": "United States",
            "subdivision": "Ohio",
            "rows": [
                row(
                    "tl:f-metro:static",
                    scheduledUrl="https://x/g.zip",
                    check={"state": "up", "code": 200, "since": "2026-09-23T00:00:00"},
                ),
                row(
                    "md:mdb-1:rt",
                    "mobilitydatabase",
                    "rt",
                    vehiclesUrl="https://x/vp.pb",
                    check={"state": "down", "error": "dns", "never_up": True},
                ),
            ],
        }
        | fields
    )


def test_feed_view_reads_state_urls_checks_and_rows():
    view = feed_view(metro(), "en")
    assert view.state_text == "Partial since Sep 23, 2026"
    assert view.kinds == "GTFS and GTFS Realtime"
    scheduled, vehicles = view.roles
    assert scheduled.urls[0].check == "Up since Sep 23, 2026 (HTTP 200)"
    assert vehicles.short == "VP" and vehicles.state == "down"
    assert vehicles.urls[0].check == "Never reached (DNS lookup failed)"
    facts = dict(view.facts)
    assert facts["Contents"] == "12 routes, 1,234 stops, 5,000 trips"
    assert facts["Last download"] == "a valid GTFS zip, checked Nov 14, 2026"
    assert facts["Schedule size"] == "22.3 MB"
    assert [r.href for r in view.rows] == [
        "https://www.transit.land/feeds/f-metro",
        "https://mobilitydatabase.org/feeds/gtfs_rt/mdb-1",
    ]
    assert [c.name for c in view.credits] == ["Transitland Atlas", "Mobility Database"]


def test_feed_view_in_french():
    view = feed_view(metro(), "fr")
    assert view.state_text == "Partiel depuis le 23 sept. 2026"
    contents = dict(view.facts)["Contenu"]
    assert contents == "12 lignes, 1\u202f234 arrêts, 5\u202f000 courses"


def test_check_text_without_a_check_or_with_a_key():
    assert check_text(None, False, "en") == "Not checked yet"
    assert check_text(None, True, "en") == "Needs an API key, not checked"


def test_app_links():
    feed = metro()
    assert editor_url(feed) == "https://edit.gtfs.zone/#load=https%3A%2F%2Fx%2Fg.zip"
    assert viewer_url(feed) == (
        "https://viz.rt.gtfs.zone/#scheduled=https%3A%2F%2Fx%2Fg.zip"
        "&rt_vp=https%3A%2F%2Fx%2Fvp.pb"
    )
    assert viewer_url(metro(urls={"scheduled": ["https://x/g.zip"]})) == ""


def test_no_site_reason():
    assert feed_view(metro(content={"state": "memory"}), "en").reason == (
        "No timetables: the last download was too large to process."
    )
    rt_only = metro(urls={"vehicles": ["https://x/vp.pb"]}, content={})
    assert "realtime only" in feed_view(rt_only, "en").reason


def test_dataset_lists_downloads():
    doc = dataset(metro(), "https://sites.gtfs.zone/metro/", "d")
    assert [d["contentUrl"] for d in doc["distribution"]] == [
        "https://x/g.zip",
        "https://x/vp.pb",
    ]
    assert doc["temporalCoverage"] == "2026-01-01/2026-12-31"
    assert doc["spatialCoverage"]["name"] == "Ohio, United States"


def test_build_feed_page():
    site = Site(slug="metro", feed="f-0000000001", catalog=metro())
    files = build_feed_page(site, TODAY)
    page = files["index.html"].decode()
    assert "<h1" in page and "Metro" in page and "Buses, Ohio, United States" in page
    assert "https://x/vp.pb" in page and "Never reached" in page
    assert '"@type":"Dataset"' in page.replace(" ", "")
    assert 'name="robots"' not in page
    assert '"feed_only": true' in files["site.json"].decode()


def test_feed_page_noindex_when_long_down_or_not_indexed():
    down = metro(state="down", since="2026-01-01T00:00:00+00:00")
    assert not feed_indexable(Site(slug="m", feed="f-0000000001", catalog=down), TODAY)
    unindexed = Site(slug="m", feed="f-0000000001", catalog=metro(), index=False)
    assert not feed_indexable(unindexed, TODAY)
    page = build_feed_page(unindexed, TODAY)["index.html"]
    assert b'<meta name="robots" content="noindex">' in page


def test_site_index_has_the_feed_card():
    from conftest import fixture_zip

    from gtfs_zone_timetable_sites.build import build_site

    site = Site(slug="metro", feed="f-0000000001", catalog=metro())
    page = build_site(fixture_zip("overnight"), site, date(2026, 10, 5))["index.html"]
    assert b'id="feed"' in page and b"https://x/vp.pb" in page
    assert b"list.gtfs.zone" not in page


def link(name, kind="shares", agencies=("A",), slug=None):
    return OverlapLink(kind=kind, agencies=agencies, name=name, slug=slug)


def test_overlaps_view_groups_kinds_and_sorts_by_name():
    view = overlaps_view(
        (
            link("b", "contains", slug="b"),
            link("Z", "containedIn"),
            link("a", "shares", ("A", "B")),
            link("c", "shares"),
            link("d", "later"),
        ),
        "en",
    )
    assert [o.name for o in view.part_of] == ["Z"]
    assert [o.name for o in view.holds] == ["b"]
    assert view.shares_text == "Shares agencies with 2 other feeds"
    assert [(r.link.name, r.kind_text) for r in view.rows] == [
        ("a", "shares some agencies"),
        ("b", "all of its agencies are in this feed"),
        ("c", "shares some agencies"),
        ("d", ""),
        ("Z", "has all of this feed's agencies"),
    ]
    assert view.rows[0].agencies_text == "Agencies in both: A and B"
    assert overlaps_view((), "en") is None


def test_route_overlaps_match_the_routes_agency():
    links = (link("x", agencies=("DB  AG", "SBB")), link("y", agencies=("SBB",)))
    assert route_overlaps(links, ("DB AG",)) == (("DB AG", (links[0],)),)
    assert route_overlaps(links, ("Other",)) == ()


def test_feed_page_shows_overlaps():
    site = Site(
        slug="metro",
        feed="f-0000000001",
        catalog=metro(),
        overlaps=(link("Region", "containedIn", slug="region"),),
    )
    page = build_feed_page(site, TODAY)["index.html"].decode()
    assert (
        'Also published as part of: <a class="link" href="../region/">Region</a>.'
        in page
    )
    assert 'id="overlaps"' in page

import json
from datetime import date

import pytest
from conftest import fixture_files, fixture_zip, make_zip

from gtfs_zone_timetable_sites.catalog import shard_of
from gtfs_zone_timetable_sites.config import CatalogFeed, Site
from gtfs_zone_timetable_sites.pipeline.publish import (
    FeedProblem,
    build_and_publish,
    classify,
    download,
    publish_root,
    publish_site,
    read_contents,
    read_entries,
    run_shard,
)

httpx = pytest.importorskip("httpx")

TODAY = date(2026, 10, 5)


class FakeStore:
    def __init__(self, objects=None):
        self.objects = dict(objects or {})
        self.puts = []

    def list_keys(self, prefix=""):
        return {key for key in self.objects if key.startswith(prefix)}

    def get(self, key):
        return self.objects.get(key)

    def put(self, key, body):
        self.puts.append(key)
        self.objects[key] = body

    def delete(self, keys):
        for key in keys:
            self.objects.pop(key, None)


def test_publish_uploads_only_changed_and_deletes_stale():
    store = FakeStore()
    files = {"index.html": b"home", "style.css": b"css", "old/index.html": b"x"}
    counts, changed, _ = publish_site(store, "s", files, {}, TODAY, "1.0.0")
    assert counts["uploaded"] == 3
    assert changed == ["index.html", "old/index.html"]
    assert store.puts == [
        "s/style.css",
        "s/index.html",
        "s/old/index.html",
        "s/manifest.json",
    ]

    store.puts.clear()
    files = {"index.html": b"new home", "style.css": b"css"}
    counts, changed, _ = publish_site(store, "s", files, {"url": "u"}, TODAY, "1.0.1")
    assert counts == {"files": 2, "uploaded": 1, "deleted": 1}
    assert changed == ["index.html"]
    assert store.puts == ["s/index.html", "s/manifest.json"]
    assert "s/old/index.html" not in store.objects
    manifest = json.loads(store.objects["s/manifest.json"])
    assert manifest["built"] == "2026-10-05"
    assert manifest["version"] == "1.0.1"
    assert manifest["source"] == {"url": "u"}


def page(day):
    return f'<p><time class="generated" datetime="{day}">{day}</time></p>'.encode()


def test_publish_dates_pages_by_content_change():
    store = FakeStore()
    publish_site(store, "s", {"index.html": page("a")}, {}, date(2026, 10, 1), "1")
    counts, changed, pages = publish_site(
        store, "s", {"index.html": page("b")}, {}, TODAY, "1"
    )
    # The build date alone changes the file but not its content date.
    assert counts["uploaded"] == 1 and changed == []
    manifest = json.loads(store.objects["s/manifest.json"])
    assert manifest["pages"]["index.html"]["modified"] == "2026-10-01"
    assert pages == manifest["pages"]

    _, changed, _ = publish_site(
        store, "s", {"index.html": page("b") + b"new"}, {}, TODAY, "1"
    )
    assert changed == ["index.html"]
    manifest = json.loads(store.objects["s/manifest.json"])
    assert manifest["pages"]["index.html"]["modified"] == "2026-10-05"


def test_publish_dates_pages_missing_from_an_old_manifest_today():
    store = FakeStore({"s/manifest.json": json.dumps({"files": {}}).encode()})
    _, changed, _ = publish_site(store, "s", {"index.html": page("a")}, {}, TODAY, "1")
    assert changed == ["index.html"]


def entry(slug, built):
    return {
        "summary": {
            "slug": slug,
            "title": slug.title(),
            "routes": 1,
            "valid_through": "2026-12-01",
        },
        "built": built,
        "pages": {"index.html": built},
    }


def test_publish_root_lists_live_sites_and_drops_the_rest():
    store = FakeStore(
        {
            "a/index.html": b"a",
            "b/index.html": b"b",
            "gone/index.html": b"x",
            "old-root.html": b"x",
            "countries/xx/index.html": b"x",
            "slugs.json": b"{}",
            "_shards/00.json": b"{}",
        }
    )
    entries = {
        "a": entry("a", "2026-10-05"),
        "b": entry("b", "2026-10-04"),
        "gone": entry("gone", "2026-10-05"),
    }
    behind = publish_root(store, entries, {"a", "b", "new"}, TODAY, "k3y")
    assert behind == ["b", "new"]
    sitemap = store.objects[f"sitemaps/{shard_of('b'):02d}.xml"].decode()
    assert (
        "<loc>https://sites.gtfs.zone/b/</loc><lastmod>2026-10-04</lastmod>" in sitemap
    )
    assert store.objects["k3y.txt"] == b"k3y"
    other = store.objects["countries/other/index.html"]
    assert b'href="../../a/"' in other and b"gone/" not in other
    assert not store.list_keys("gone/")
    assert "old-root.html" not in store.objects
    assert "countries/xx/index.html" not in store.objects
    assert {"a/index.html", "slugs.json", "_shards/00.json"} <= store.objects.keys()


def test_publish_root_redirects_an_old_slug():
    store = FakeStore(
        {
            "metro/index.html": b"old",
            "metro/route-1/index.html": b"old",
            "metro/manifest.json": b"{}",
            "aliases.json": b"{}",
        }
    )
    entries = {"metro-lyon": entry("metro-lyon", "2026-10-05")}
    entries["metro-lyon"]["pages"]["route-1/index.html"] = "2026-10-05"
    publish_root(
        store, entries, {"metro-lyon"}, TODAY, redirects={"metro": "metro-lyon"}
    )
    page = store.objects["metro/route-1/index.html"].decode()
    url = "https://sites.gtfs.zone/metro-lyon/route-1/"
    assert f'<link rel="canonical" href="{url}">' in page
    assert f'content="0; url={url}"' in page
    assert b"/metro-lyon/" in store.objects["metro/index.html"]
    assert "metro/manifest.json" not in store.objects
    assert "aliases.json" in store.objects


def test_publish_root_keeps_sites_when_the_catalog_shrinks():
    store = FakeStore({f"{s}/index.html": b"x" for s in "abcdef"})
    entries = {s: entry(s, "2026-10-05") for s in "abcdef"}
    publish_root(store, entries, {"a", "b"}, TODAY)
    assert "f/index.html" in store.objects
    assert b"C</span>" not in store.objects["countries/other/index.html"]


def test_build_and_publish_then_skip_unchanged_feed():
    store = FakeStore()
    site = Site(slug="s", url="https://x/g.zip")
    calls = []

    def handler(request):
        calls.append(request)
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(
            200, content=fixture_zip("overnight"), headers={"ETag": '"v1"'}
        )

    result = build_and_publish(store, client(handler), site, TODAY, "1")
    assert result["counts"]["uploaded"] > 0
    assert "index.html" in result["changed"]
    assert result["entry"]["built"] == "2026-10-05"
    assert result["entry"]["summary"]["slug"] == "s"
    assert result["entry"]["pages"]["index.html"] == "2026-10-05"

    assert result["content"]["counts"]["routes"] > 0

    again = build_and_publish(store, client(handler), site, TODAY, "1")
    assert again["counts"]["uploaded"] == 0 and again["changed"] == []
    assert again["entry"] == result["entry"]
    assert again["content"] == result["content"]
    assert len(calls) == 2


def test_run_shard_keeps_failed_sites_previous_entry():
    old = {"a": entry("a", "2026-10-04"), "b": entry("b", "2026-10-04")}
    store = FakeStore({"_shards/03.json": json.dumps(old).encode()})
    small = CatalogFeed(feedId="f-0000000001", name="A", staticBytes=10)
    big = CatalogFeed(feedId="f-0000000002", name="B", staticBytes=99)
    sites = [
        Site(slug="a", feed=small.feed_id, catalog=small),
        Site(slug="b", feed=big.feed_id, catalog=big),
        Site(slug="c", url="u"),
    ]
    order = []

    def publish(site):
        order.append(site.slug)
        if site.slug == "a":
            raise ValueError("bad zip")
        return built(site.slug)

    results, failures, skipped = run_shard(store, 3, sites, publish, TODAY, "1")
    assert order == ["b", "a", "c"]
    assert set(results) == {"b", "c"}
    assert failures == {"a": "ValueError: bad zip"}
    assert skipped == []
    shard = json.loads(store.objects["_shards/03.json"])
    assert shard["a"]["built"] == "2026-10-04"
    assert shard["b"]["built"] == shard["c"]["built"] == "2026-10-05"
    assert read_entries(store) == shard

    report = json.loads(store.objects["_content/03.json"])
    assert report["a"]["outcome"] == "error"
    assert report["a"]["detail"] == "ValueError: bad zip"
    assert report["b"]["routes"] == 1
    assert report["b"]["outcome"] == "ok" and report["b"]["feedId"] == big.feed_id
    assert report["b"]["head"] == {"bytes": 99, "lastModified": None}
    assert report["c"]["url"] == "u" and report["c"]["head"] == {}
    assert read_contents(store) == report


FACTS = {"routes": 1}


def built(slug):
    return {
        "counts": {},
        "changed": [],
        "entry": entry(slug, "2026-10-05"),
        "content": FACTS,
    }


def bad_zip(site):
    raise FeedProblem("not_zip", "html")


def test_run_shard_skips_known_bad_feed_until_it_changes():
    feed = CatalogFeed(
        feedId="f-0000000001",
        name="A",
        staticBytes=10,
        urls={"scheduled": ["https://x/g.zip"]},
    )
    site = Site(slug="a", feed=feed.feed_id, catalog=feed)
    store = FakeStore()
    calls = []

    def publish(site):
        calls.append(site.slug)
        return bad_zip(site)

    run_shard(store, 0, [site], publish, date(2026, 10, 1), "1")
    first = json.loads(store.objects["_content/00.json"])["a"]
    assert first["outcome"] == "not_zip" and first["detail"] == "html"
    assert first["since"] == "2026-10-01"

    # Same HEAD facts and version within the week: skipped, report kept.
    _, failures, skipped = run_shard(store, 0, [site], publish, TODAY, "1")
    assert skipped == ["a"] and failures == {} and calls == ["a"]
    assert json.loads(store.objects["_content/00.json"])["a"] == first

    # A new version retries, and the outcome keeps its first day.
    run_shard(store, 0, [site], publish, TODAY, "2")
    assert calls == ["a", "a"]
    again = json.loads(store.objects["_content/00.json"])["a"]
    assert again["since"] == "2026-10-01" and again["checked"] == "2026-10-05"

    # Changed HEAD facts retry, and a success starts a new outcome.
    changed = feed.model_copy(update={"static_bytes": 11})
    fixed = site.model_copy(update={"catalog": changed})
    _, _, skipped = run_shard(store, 0, [fixed], lambda s: built(s.slug), TODAY, "2")
    assert skipped == []
    ok = json.loads(store.objects["_content/00.json"])["a"]
    assert ok["outcome"] == "ok" and ok["since"] == "2026-10-05"
    assert "detail" not in ok


def test_run_shard_retries_known_bad_feed_after_a_week():
    site = Site(slug="a", url="https://x/g.zip")
    store = FakeStore()
    calls = []

    def publish(site):
        calls.append(site.slug)
        return bad_zip(site)

    run_shard(store, 0, [site], publish, date(2026, 9, 28), "1")
    run_shard(store, 0, [site], publish, TODAY, "1")
    assert calls == ["a", "a"]


def test_run_shard_does_not_skip_transient_failures():
    site = Site(slug="a", url="https://x/g.zip")
    store = FakeStore()
    calls = []

    def publish(site):
        calls.append(site.slug)
        raise httpx.ConnectTimeout("slow")

    run_shard(store, 0, [site], publish, TODAY, "1")
    run_shard(store, 0, [site], publish, TODAY, "1")
    assert calls == ["a", "a"]
    report = json.loads(store.objects["_content/00.json"])["a"]
    assert report["outcome"] == "timeout"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ("FeedProblem: not_zip: html", ("not_zip", "html")),
        (
            "FeedProblem: missing_files: GTFS zip is missing stops",
            ("missing_files", "GTFS zip is missing stops"),
        ),
        (
            "HTTPStatusError: Client error '403 Forbidden' for url 'https://x'",
            ("http_error", "HTTP 403"),
        ),
        (
            "ConnectError: [Errno -2] Name",
            ("http_error", "ConnectError: [Errno -2] Name"),
        ),
        ("ReadTimeout: timed out", ("timeout", "ReadTimeout")),
        ("BrokenProcessPool: worker died", ("memory", "BrokenProcessPool")),
        ("MemoryError: ", ("memory", "MemoryError")),
        ("KeyError: 'x'", ("error", "KeyError: 'x'")),
    ],
)
def test_classify(error, expected):
    assert classify(error) == expected


@pytest.mark.parametrize(
    ("body", "outcome"),
    [
        (b"<!DOCTYPE html><html></html>", "not_zip: html"),
        (b"", "not_zip: empty"),
        (make_zip({"agency.txt": "agency_id\n"}), "missing_files: GTFS zip is missing"),
        (
            make_zip(
                fixture_files("branching")
                | {
                    "trips.txt": "route_id,service_id,trip_id\n",
                    "stop_times.txt": "trip_id,stop_id,stop_sequence\n",
                }
            ),
            "empty: no route has scheduled trips",
        ),
    ],
)
def test_build_and_publish_raises_feed_problems(body, outcome):
    site = Site(slug="s", url="https://x/g.zip")

    def handler(request):
        return httpx.Response(200, content=body)

    with pytest.raises(FeedProblem, match=outcome):
        build_and_publish(FakeStore(), client(handler), site, TODAY, "1")


def test_publish_root_lists_content_reports():
    store = FakeStore({"_content/00.json": b"{}", "_content/07.json": b"{}"})
    publish_root(store, {}, set(), TODAY)
    index = json.loads(store.objects["_content/index.json"])
    assert index == {"shards": ["_content/00.json", "_content/07.json"]}
    assert "_content/00.json" in store.objects


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_download_records_source_headers():
    def handler(request):
        assert "if-none-match" not in request.headers
        return httpx.Response(200, content=b"zip", headers={"ETag": '"v1"'})

    body, source = download(client(handler), "https://x/g.zip", None)
    assert body == b"zip"
    assert source == {"url": "https://x/g.zip", "etag": '"v1"', "last_modified": None}


def test_download_not_modified():
    previous = {"url": "https://x/g.zip", "etag": '"v1"', "last_modified": None}

    def handler(request):
        assert request.headers["if-none-match"] == '"v1"'
        return httpx.Response(304)

    assert download(client(handler), "https://x/g.zip", previous) == (None, previous)


def test_download_ignores_previous_for_another_url():
    previous = {"url": "https://old/g.zip", "etag": '"v1"', "last_modified": None}

    def handler(request):
        assert "if-none-match" not in request.headers
        return httpx.Response(200, content=b"zip")

    body, _ = download(client(handler), "https://x/g.zip", previous)
    assert body == b"zip"


def test_publish_root_lists_unbuilt_sites_from_the_catalog():
    feed = CatalogFeed(
        feedId="f-0123456789",
        name="Never Built",
        urls={"scheduled": ("https://example.org/feeds/g.zip",)},
        country="United States",
        country_code="US",
        subdivision="Ohio",
    )
    site = Site(slug="never", feed=feed.feed_id, catalog=feed)
    store = FakeStore()
    contents = {"never": {"outcome": "http_error", "detail": "HTTP 404"}}
    publish_root(store, {}, {"never"}, TODAY, contents=contents, sites=[site])
    country = store.objects["countries/us/index.html"].decode()
    assert "Never Built" in country and "example.org: g.zip" in country
    assert "Download failed: HTTP 404" in country
    assert 'href="../../never/"' not in country

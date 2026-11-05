import obstore
from obstore.store import MemoryStore

from gtfs_zone_timetable_sites.pipeline.bucket import Bucket, BucketSettings

SETTINGS = BucketSettings(
    endpoint="http://garage:3900",
    bucket="sites.gtfs.zone",
    access_key="key",
    secret_key="secret",
    region="garage",
)


def test_put_get_list_delete():
    bucket = Bucket(SETTINGS, store=MemoryStore())
    bucket.put("a/index.html", b"<p>a</p>")
    bucket.put("a/style.css", b"p{}")
    bucket.put("b/index.html", b"<p>b</p>")
    assert bucket.get("a/index.html") == b"<p>a</p>"
    assert bucket.get("missing.html") is None
    assert bucket.list_keys("a/") == {"a/index.html", "a/style.css"}
    assert bucket.list_keys() == {"a/index.html", "a/style.css", "b/index.html"}
    bucket.delete({"a/index.html", "a/style.css", "a/gone.html"})
    bucket.delete(set())
    assert bucket.list_keys() == {"b/index.html"}


def test_put_sets_content_type_and_cache_control():
    store = MemoryStore()
    Bucket(SETTINGS, store=store).put("a/index.html", b"x")
    attributes = obstore.get(store, "a/index.html").attributes
    assert attributes["Content-Type"] == "text/html; charset=utf-8"
    assert attributes["Cache-Control"] == "public, max-age=600"


def test_s3_store_builds_from_settings():
    assert Bucket(SETTINGS).name == "sites.gtfs.zone"

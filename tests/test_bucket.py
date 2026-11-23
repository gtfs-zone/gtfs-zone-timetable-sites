import obstore
from obstore.store import MemoryStore

from gtfs_zone_timetable_sites.pipeline.bucket import (
    Bucket,
    BucketSettings,
    localities_from_env,
)

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


def test_localities_read_with_the_zips_keys(monkeypatch):
    monkeypatch.delenv("TIMETABLE_SITES_LOCALITIES", raising=False)
    assert localities_from_env() is None
    monkeypatch.setenv("TIMETABLE_SITES_LOCALITIES", "gtfs-zips/localities/l.parquet")
    monkeypatch.setenv("ZIPS_S3_ENDPOINT", "http://garage:3900")
    monkeypatch.setenv("ZIPS_S3_ACCESS_KEY", "GKzips")
    monkeypatch.setenv("ZIPS_S3_SECRET_KEY", "secret")
    monkeypatch.setenv("S3_ACCESS_KEY", "sites-key")
    localities = localities_from_env()
    assert localities.path == "gtfs-zips/localities/l.parquet"
    options = localities.filesystem.__reduce__()[1][0]
    assert options["access_key"] == "GKzips"
    assert options["region"] == "garage"
    assert options["scheme"] == "http"


def test_open_streams_an_object_to_a_file():
    bucket = Bucket(SETTINGS, store=MemoryStore())
    bucket.put("gtfs/a.zip", b"PK" * 1000)
    with bucket.open("gtfs/a.zip") as body:
        assert body.read() == b"PK" * 1000
    assert bucket.open("gtfs/missing.zip") is None


def test_zips_from_env(monkeypatch):
    monkeypatch.delenv("ZIPS_S3_ENDPOINT", raising=False)
    assert BucketSettings.zips_from_env() is None
    monkeypatch.setenv("ZIPS_S3_ENDPOINT", "http://garage:3900")
    monkeypatch.setenv("ZIPS_S3_ACCESS_KEY", "key")
    monkeypatch.setenv("ZIPS_S3_SECRET_KEY", "secret")
    settings = BucketSettings.zips_from_env()
    assert settings.bucket == "gtfs-zips" and settings.region == "garage"

import json

from conftest import fixture_zip

from gtfs_zone_timetable_sites.catalog import shard_of
from gtfs_zone_timetable_sites.cli import main


def test_build_from_local_zip(tmp_path):
    config = tmp_path / "sites.yaml"
    config.write_text("sites: [{slug: local, url: 'https://example.org/g.zip'}]")
    feed = tmp_path / "feed.zip"
    feed.write_bytes(fixture_zip("overnight"))
    out = tmp_path / "dist"
    main(
        [
            "build",
            "--site",
            "local",
            "--zip",
            str(feed),
            "--out",
            str(out),
            "--config",
            str(config),
        ]
    )
    assert (out / "local" / "index.html").read_bytes().startswith(b"<!doctype html>")


def test_dump_prints_timetables(tmp_path, capsys):
    config = tmp_path / "sites.yaml"
    config.write_text("sites: [{slug: local, url: 'https://example.org/g.zip'}]")
    feed = tmp_path / "feed.zip"
    feed.write_bytes(fixture_zip("overnight"))
    main(
        [
            "dump",
            "--site",
            "local",
            "--zip",
            str(feed),
            "--config",
            str(config),
            "--date",
            "2026-10-05",
        ]
    )
    out = capsys.readouterr().out
    assert "# 1" in out
    assert "11:40p" in out and "12:10a+1" not in out


def test_build_clears_old_files_and_writes_root(tmp_path):
    config = tmp_path / "sites.yaml"
    config.write_text("sites: [{slug: local, url: 'https://example.org/g.zip'}]")
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "local.zip").write_bytes(fixture_zip("overnight"))
    out = tmp_path / "dist"
    (out / "gone").mkdir(parents=True)
    (out / "local" / "old").mkdir(parents=True)
    main(["build", "--out", str(out), "--config", str(config), "--cache", str(cache)])
    assert not (out / "gone").exists() and not (out / "local" / "old").exists()
    assert b'href="countries/us/"' in (out / "index.html").read_bytes()
    country = (out / "countries" / "us" / "index.html").read_bytes()
    assert b'href="../../local/"' in country
    assert (out / "error.html").exists() and (out / "logo.svg").exists()
    sitemap = (out / "sitemaps" / f"{shard_of('local'):02d}.xml").read_text()
    assert "<loc>https://sites.gtfs.zone/local/1/</loc><lastmod>" in sitemap
    assert not (out / "local" / "sitemap.xml").exists()


def test_build_catalog_sites_in_parallel(tmp_path):
    config = tmp_path / "sites.yaml"
    config.write_text(
        "catalog: {countries: [US]}\n"
        "sites: [{slug: local, url: 'https://example.org/g.zip'}]"
    )
    cache = tmp_path / "cache"
    cache.mkdir()

    def feed(feed_id, name, country):
        return {
            "feedId": feed_id,
            "name": name,
            "urls": {"scheduled": [f"https://example.org/{feed_id}.zip"]},
            "roleState": {"scheduled": "up"},
            "country_code": country,
            "country": "United States",
        }

    doc = {
        "feeds": [
            feed("f-0000000001", "Metro Bus", "US"),
            feed("f-0000000002", "Metro Bus", "US"),
            feed("f-0000000003", "Elsewhere", "FR"),
        ]
    }
    (cache / "_catalog.json").write_text(json.dumps(doc))
    for slug in ("local", "metro-bus", "metro-bus-2"):
        (cache / f"{slug}.zip").write_bytes(fixture_zip("overnight"))
    out = tmp_path / "dist"
    args = ["build", "--out", str(out), "--config", str(config), "--cache", str(cache)]
    main([*args, "--workers", "2"])
    assert {p.name for p in out.iterdir() if (p / "site.json").exists()} == {
        "local",
        "metro-bus",
        "metro-bus-2",
    }
    assert json.loads((cache / "_slugs.json").read_text()) == {
        "f-0000000001": "metro-bus",
        "f-0000000002": "metro-bus-2",
    }
    us = (out / "countries" / "us" / "index.html").read_bytes()
    assert b'href="../../metro-bus-2/"' in us

    main([*args, "--listed"])
    assert not (out / "metro-bus").exists() and (out / "local").exists()
    main([*args, "--country", "us", "--limit", "1"])
    assert not (out / "local").exists()
    assert len([p for p in out.iterdir() if (p / "site.json").exists()]) == 1

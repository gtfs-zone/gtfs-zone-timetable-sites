import hashlib
import json
from datetime import date

from conftest import fixture_files, fixture_zip, make_zip

from gtfs_zone_timetable_sites.build import build_site
from gtfs_zone_timetable_sites.config import RouteFilter, Site
from gtfs_zone_timetable_sites.facts import feed_facts, sniff
from gtfs_zone_timetable_sites.gtfs.reader import read_feed
from gtfs_zone_timetable_sites.places import locate

FEED_INFO = (
    "feed_publisher_name,feed_publisher_url,feed_version,feed_start_date,feed_end_date\n"
    "Test Pub,https://pub.example,v7,20260101,20261231\n"
)


def test_feed_facts():
    body = fixture_zip("branching")
    feed = read_feed(body)
    facts = feed_facts(body, feed, filtered=False, stations=locate(feed))
    assert facts["bytes"] == len(body)
    assert facts["sha256"] == hashlib.sha256(body).hexdigest()
    assert facts["service"] == {"start": "2026-01-01", "end": "2026-12-31"}
    assert facts["agencies"] == [
        {
            "name": "Test Transit",
            "url": "https://example.org",
            "timezone": "America/New_York",
        }
    ]
    assert facts["counts"]["routes"] == 1
    assert facts["counts"]["stops"] > 0 and facts["counts"]["trips"] > 0
    assert facts["filtered"] is False
    assert "feedInfo" not in facts
    assert facts["languages"] == {"default": "en", "offered": ["en"], "readings": []}
    assert facts["location"]["bbox"] == [-73.6, 42.0, -73.0, 42.6]
    assert facts["location"]["stations"] == 7
    assert facts["placeTags"]["countries"] == ["United States"]
    assert facts["place"] == {
        "countryCode": "US",
        "country": "United States",
        "region": "",
    }


def test_feed_facts_languages():
    body = fixture_zip("languages")
    facts = feed_facts(body, read_feed(body), filtered=False, stations=[])
    assert facts["languages"] == {
        "default": "fr",
        "offered": ["fr", "en"],
        "readings": [],
    }


def test_feed_facts_feed_info_and_calendar_dates():
    body = make_zip(fixture_files("calendar-dates-only") | {"feed_info.txt": FEED_INFO})
    facts = feed_facts(body, read_feed(body), filtered=True, stations=[])
    assert facts["feedInfo"] == {
        "publisher": "Test Pub",
        "publisherUrl": "https://pub.example",
        "version": "v7",
        "start": "2026-01-01",
        "end": "2026-12-31",
    }
    assert facts["service"]["start"] <= facts["service"]["end"]
    assert facts["filtered"] is True
    assert "location" not in facts and "place" not in facts


def test_build_site_writes_content_json():
    site = Site(slug="t", url="u", routes=RouteFilter(route_types=(3,)))
    files = build_site(fixture_zip("branching"), site, date(2026, 10, 5))
    assert json.loads(files["content.json"])["filtered"] is True


def test_sniff():
    assert sniff(b"") == "empty"
    assert sniff(b"  \n<!DOCTYPE html><html>") == "html"
    assert sniff(b"<head></head><html>") == "html"
    assert sniff(b'{"error": 1}') == "json"
    assert sniff(b"<?xml version='1.0'?><Error/>") == "xml"
    assert sniff(b"route_id,agency_id") == "unknown"

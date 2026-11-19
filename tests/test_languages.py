import pytest
from conftest import fixture_files, fixture_zip, make_zip
from gtfs_zone_feed_fetch.languages import FeedLanguages

from gtfs_zone_timetable_sites.gtfs.reader import read_feed
from gtfs_zone_timetable_sites.languages import feed_languages, translate

AGENCY = "agency_id,agency_name,agency_url,agency_timezone,agency_lang\n"
FEED_INFO = "feed_publisher_name,feed_publisher_url,feed_lang\nP,https://p.example,"
TRANSLATIONS = "table_name,field_name,language,translation,record_id\n"


def languages(**files: str) -> FeedLanguages:
    body = fixture_files("branching") | {f"{k}.txt": v for k, v in files.items()}
    return feed_languages(read_feed(make_zip(body)))


def test_fixture_languages():
    feed = read_feed(fixture_zip("languages"))
    assert feed_languages(feed) == FeedLanguages(default="fr", offered=("fr", "en"))


def test_no_language_is_en():
    assert languages() == FeedLanguages(default="en", offered=("en",))


def test_agency_lang_without_feed_lang():
    agencies = AGENCY + "a,A,https://a.example,Europe/Rome,it\n"
    assert languages(agency=agencies) == FeedLanguages("it", ("it",))


def test_feed_lang_over_agency_lang():
    agencies = AGENCY + "a,A,https://a.example,Europe/Zurich,de\n"
    assert languages(agency=agencies, feed_info=FEED_INFO + "fr\n") == (
        FeedLanguages("fr", ("fr", "de"))
    )


def test_mul_takes_agency_lang_then_translations():
    agencies = AGENCY + "a,A,https://a.example,Europe/Brussels,nl\n"
    translations = (
        TRANSLATIONS + "stops,stop_name,fr,Gare,s1\nstops,stop_name,nl,Station,s1\n"
    )
    mul = FEED_INFO + "mul\n"
    assert languages(agency=agencies, feed_info=mul, translations=translations) == (
        FeedLanguages("nl", ("nl", "fr"))
    )
    assert languages(feed_info=mul, translations=translations) == (
        FeedLanguages("fr", ("fr", "nl"))
    )


def test_mul_without_any_language_raises():
    with pytest.raises(ValueError, match="mul"):
        languages(feed_info=FEED_INFO + "mul\n")


def test_kana_readings_are_not_page_languages():
    translations = (
        TRANSLATIONS
        + "stops,stop_name,ja-Hrkt,えき,s1\nstops,stop_name,en,Station,s1\n"
        + "stops,stop_name,ja-Latn,Eki,s1\n"
    )
    assert languages(feed_info=FEED_INFO + "ja\n", translations=translations) == (
        FeedLanguages("ja", ("ja", "en"), ("ja-Hrkt", "ja-Latn"))
    )


def test_translation_rows_without_language_are_skipped():
    translations = TRANSLATIONS + ",,,,\n"
    assert languages(feed_info=FEED_INFO + "de\n", translations=translations) == (
        FeedLanguages("de", ("de",))
    )


def test_translate_by_record_id_and_field_value():
    feed = read_feed(fixture_zip("languages"))
    en = translate(feed, "en")
    # By record_id, with the row's language in another case (EN).
    assert en.stops["A"].name == "Central Station"
    assert en.stops["B"].name == "City Hall"
    assert en.routes["R1"].long_name == "Downtown"
    assert en.agencies["a"].name == "Transit Corporation"
    # By field_value.
    assert en.trips["r1"].headsign == "City Hall"
    # Untranslated fields keep the feed's text.
    assert en.stops["Q1"].name == "Quai nord"
    assert en.trips["f1"].headsign == "Quai sud"
    assert en.agencies["b"].name == "Traverse du lac"
    assert en.routes["R1"].short_name == "1"


def test_translate_without_rows_is_the_same_feed():
    feed = read_feed(fixture_zip("languages"))
    assert translate(feed, "fr") is feed


def test_translate_record_id_over_field_value():
    translations = (
        "table_name,field_name,language,translation,record_id,field_value\n"
        "stops,stop_name,de,Wert,,Gare centrale\n"
        "stops,stop_name,de,Kennung,A,\n"
        "feed_info,feed_publisher_name,de,Verkehrsbetrieb,,\n"
    )
    body = fixture_files("languages") | {"translations.txt": translations}
    de = translate(read_feed(make_zip(body)), "de")
    assert de.stops["A"].name == "Kennung"
    assert de.feed_info and de.feed_info.publisher_name == "Verkehrsbetrieb"


def test_translate_ignores_fields_not_shown():
    translations = (
        TRANSLATIONS
        + "stops,tts_stop_name,en,Central,A\n"
        + "calendar,service_id,en,Daily,DAILY\n"
        + "agency,bike_policy_url,en,https://bikes.example,a\n"
        + "attributions,organization_name,en,Org,x\n"
        + "agency_jp,agency_official_name,en,Official,a\n"
    )
    body = fixture_files("languages") | {"translations.txt": translations}
    feed = read_feed(make_zip(body))
    assert translate(feed, "en") is feed


def test_translate_applies_shown_codes_and_urls():
    translations = (
        TRANSLATIONS
        + "stops,stop_code,en,C-1,A\n"
        + "routes,route_url,en,https://en.example/1,R1\n"
        + "agency,agency_phone,en,+1 555,a\n"
        + "trips,trip_short_name,en,Express,r1\n"
    )
    body = fixture_files("languages") | {"translations.txt": translations}
    en = translate(read_feed(make_zip(body)), "en")
    assert en.stops["A"].code == "C-1"
    assert en.routes["R1"].url == "https://en.example/1"
    assert en.agencies["a"].phone == "+1 555"
    assert en.trips["r1"].short_name == "Express"


def test_unknown_codes_are_listed_not_offered():
    agency = AGENCY + "a,A,https://a.example,UTC,english\n"
    translations = TRANSLATIONS + "stops,stop_name,xx-yy,Name,s1\n"
    assert languages(agency=agency, translations=translations) == FeedLanguages(
        "en", ("en",), unknown=("english", "xx-yy")
    )
    assert languages(
        feed_info=FEED_INFO + "english\n", agency=AGENCY + "a,A,u,UTC,fr\n"
    ) == FeedLanguages("fr", ("fr",), unknown=("english",))


def test_translate_skips_rows_in_unknown_codes():
    translations = TRANSLATIONS + "stops,stop_name,english,Central,A\n"
    body = fixture_files("languages") | {"translations.txt": translations}
    feed = read_feed(make_zip(body))
    assert translate(feed, "en") is feed

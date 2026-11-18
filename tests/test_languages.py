import pytest
from conftest import fixture_files, fixture_zip, make_zip

from gtfs_zone_timetable_sites.gtfs.reader import read_feed
from gtfs_zone_timetable_sites.languages import (
    FeedLanguages,
    feed_languages,
    normalize,
)

AGENCY = "agency_id,agency_name,agency_url,agency_timezone,agency_lang\n"
FEED_INFO = "feed_publisher_name,feed_publisher_url,feed_lang\nP,https://p.example,"
TRANSLATIONS = "table_name,field_name,language,translation,record_id\n"


def languages(**files: str) -> FeedLanguages:
    body = fixture_files("branching") | {f"{k}.txt": v for k, v in files.items()}
    return feed_languages(read_feed(make_zip(body)))


def test_normalize():
    assert normalize("EN") == "en"
    assert normalize("en-US") == "en"
    assert normalize("ja_JP") == "ja"
    assert normalize("zh-Hant-TW") == "zh-Hant"
    assert normalize(" fr ") == "fr"
    # Library feeds: La Union (spa), cross-solution ("de"), Japanese towns
    # (ja-hrkt), TransLink North Vancouver (hur, no CLDR locale data).
    assert normalize("spa") == "es"
    assert normalize('"de"') == "de"
    assert normalize("ja-hrkt") == "ja-Hrkt"
    assert normalize("hur") == "hur"


@pytest.mark.parametrize("code", ["", "xx", "english", "mul", "ja-Abcd", "e n"])
def test_normalize_unknown_raises(code):
    with pytest.raises(ValueError, match="unknown language code"):
        normalize(code)


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

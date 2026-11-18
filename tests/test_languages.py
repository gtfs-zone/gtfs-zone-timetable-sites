import pytest
from conftest import fixture_files, fixture_zip, make_zip

from gtfs_zone_timetable_sites.gtfs.reader import read_feed
from gtfs_zone_timetable_sites.languages import (
    FeedLanguages,
    feed_languages,
    normalize,
    translate,
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


def test_translate_ignores_unread_fields_and_raises_on_unknown():
    ignored = TRANSLATIONS + "stops,tts_stop_name,en,Central,A\n"
    body = fixture_files("languages") | {"translations.txt": ignored}
    feed = read_feed(make_zip(body))
    assert translate(feed, "en") is feed
    unknown = TRANSLATIONS + "calendar,service_id,en,Daily,DAILY\n"
    body = fixture_files("languages") | {"translations.txt": unknown}
    with pytest.raises(ValueError, match=r"calendar\.service_id not handled"):
        translate(read_feed(make_zip(body)), "en")

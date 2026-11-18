from datetime import date, timedelta
from pathlib import Path

import pytest
from babel.messages.catalog import Catalog
from babel.messages.extract import extract_from_dir
from babel.messages.frontend import parse_mapping_cfg
from babel.messages.pofile import read_po

from gtfs_zone_timetable_sites import i18n
from gtfs_zone_timetable_sites.gtfs.service import exception_name, weekday_name
from gtfs_zone_timetable_sites.i18n import (
    UI_LOCALES,
    join_and,
    long_date,
    short_date,
    t,
    weekday_date,
)
from gtfs_zone_timetable_sites.messages import KEYWORDS, po_paths
from gtfs_zone_timetable_sites.pages import date_range, modes_label

MONDAY = date(2026, 10, 5)
REPO = Path(__file__).parent.parent


def messages(locale: str) -> list:
    path = i18n.LOCALES_DIR / locale / "LC_MESSAGES" / "messages.po"
    with path.open("rb") as file:
        return [m for m in read_po(file) if m.id]


def catalog(locale: str) -> dict[str, tuple[str, ...]]:
    """A locale's .po as msgid -> its forms (one, or one per plural form)."""
    return {
        m.id if isinstance(m.id, str) else m.id[0]: (
            (m.string,) if isinstance(m.string, str) else tuple(m.string)
        )
        for m in messages(locale)
    }


def plural_keys(locale: str) -> set[str]:
    return {m.id[0] for m in messages(locale) if m.pluralizable}


def test_every_locale_has_a_catalog():
    assert {p.parent.parent.name for p in po_paths()} == UI_LOCALES >= {"en", "fr"}


def test_every_msgid_is_in_every_catalog_and_translated():
    en = catalog("en")
    for locale in UI_LOCALES:
        entries = catalog(locale)
        assert set(entries) == set(en), locale
        plurals = plural_keys(locale)
        assert plurals == plural_keys("en"), locale
        nplurals = Catalog(locale=locale).num_plurals
        for key, forms in entries.items():
            assert all(forms), (locale, key)
            assert len(forms) == (nplurals if key in plurals else 1), (locale, key)


def test_every_extracted_key_is_in_the_catalog():
    with (REPO / "babel.cfg").open() as file:
        method_map, options_map = parse_mapping_cfg(file)
    keys = {
        message
        for _, _, message, _, _ in extract_from_dir(
            REPO, method_map, options_map, keywords=KEYWORDS
        )
    }
    assert len(keys) > 100
    assert keys <= set(catalog("en"))


def test_placeholders_match_across_catalogs():
    def names(text: str) -> set[str]:
        return {part.split("}")[0] for part in text.split("{")[1:]}

    en = catalog("en")
    for locale in UI_LOCALES:
        for key, forms in catalog(locale).items():
            # A translation may leave out a value English uses, never add one.
            allowed = set().union(*map(names, en[key])) | {"modes_lower"}
            for text in forms:
                assert names(text) <= allowed, (locale, key)


def test_plural_pick():
    assert t("en", "route.trips", n=1) == "1 trip"
    assert t("en", "route.trips", n=0) == "0 trips"
    assert t("fr", "route.trips", n=0) == "0 départ"
    assert t("fr", "route.trips", n=2) == "2 départs"


def test_missing_french_key_falls_back_to_english(monkeypatch):
    french = i18n.translations("fr")
    monkeypatch.setattr(french, "_catalog", {})
    assert t("fr", "route.all_routes") == "All routes"
    assert t("fr", "route.trips", n=0) == "0 trips"


def test_plural_forms_by_cldr_category():
    assert i18n.plural_forms("en", "map.live") == {
        "one": "Live: {n} vehicle on the map",
        "other": "Live: {n} vehicles on the map",
    }
    assert i18n.plural_forms("fr", "map.live")["one"] == (
        "En direct : {n} véhicule sur la carte"
    )


def test_unknown_key_raises():
    with pytest.raises(KeyError):
        t("en", "no.such.key")


def test_period_after_an_abbreviation_is_not_doubled():
    dates = short_date(date(2026, 10, 31), "fr")
    assert t("fr", "day.also_runs", dates=dates) == "Aussi en service : 31 oct."
    assert t("fr", "day.also_runs", dates="2 mai") == "Aussi en service : 2 mai."


def test_french_dates():
    assert long_date(date(2026, 1, 1), "fr") == "1er janv. 2026"
    assert long_date(date(2026, 8, 15), "fr") == "15 août 2026"
    assert date_range(date(2026, 1, 2), date(2029, 12, 31), "fr") == (
        "du 2 janv. 2026 au 31 déc. 2029"
    )
    assert date_range(None, date(2026, 12, 1), "fr") == "jusqu'au 1er déc. 2026"


def test_french_day_types():
    assert weekday_name(range(5), locale="fr") == "Semaine"
    assert weekday_name([5], locale="fr") == "Samedi"
    assert weekday_name(range(7), locale="fr") == "Tous les jours"
    assert weekday_name([0, 1, 2, 3], locale="fr") == "Lundi au jeudi"
    assert weekday_name([1, 4], locale="fr") == "Mardi et vendredi"
    assert weekday_name([4], plural=True, locale="fr") == "Vendredis"
    horizon = [MONDAY + timedelta(days=n) for n in range(21)]
    assert exception_name([horizon[5]], horizon, "fr") == "Sam. 10 oct."
    sundays = [horizon[6], horizon[13], horizon[20]]
    assert exception_name(sundays, horizon, "fr") == (
        "Dimanches, du 11 oct. au 25 oct."
    )


def test_dates_and_lists_from_cldr():
    assert long_date(date(2026, 1, 2)) == "Jan 2, 2026"
    assert short_date(date(2026, 10, 12)) == "Oct 12"
    assert weekday_date(date(2026, 10, 31)) == "Sat, Oct 31"
    assert weekday_date(date(2026, 10, 1), "fr") == "jeu. 1er oct."
    assert join_and(["A", "B"]) == "A and B"
    assert join_and(["A"]) == "A"


def test_french_lists_and_modes():
    assert join_and(["A", "B", "C"], "fr") == "A, B et C"
    assert modes_label(["bus", "ferry"], "fr") == "Bus et traversier"


def test_finnish_weekdays_are_labels():
    assert weekday_name([5], locale="fi") == "Lauantai"

from datetime import date, timedelta

import pytest

from gtfs_zone_timetable_sites.gtfs.service import exception_name, weekday_name
from gtfs_zone_timetable_sites.i18n import EN, FR, join_and, long_date, short_date, t
from gtfs_zone_timetable_sites.pages import date_range, modes_label

MONDAY = date(2026, 10, 5)


def test_french_catalog_has_every_english_key():
    assert set(FR) == set(EN)


def test_placeholders_match_across_catalogs():
    def names(text: str) -> set[str]:
        return {part.split("}")[0] for part in text.split("{")[1:]}

    for key, text in FR.items():
        # French may leave out a value English uses, never add one.
        assert names(text) <= names(EN[key]) | {"modes_lower"}, key


def test_plural_pick():
    assert t("en", "route.trips", n=1) == "1 trip"
    assert t("en", "route.trips", n=0) == "0 trips"
    assert t("fr", "route.trips", n=0) == "0 départ"
    assert t("fr", "route.trips", n=2) == "2 départs"


def test_missing_french_key_falls_back_to_english(monkeypatch):
    monkeypatch.delitem(FR, "route.all_routes")
    assert t("fr", "route.all_routes") == "All routes"


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


def test_french_lists_and_modes():
    assert join_and(["A", "B", "C"], "fr") == "A, B et C"
    assert modes_label(["bus", "ferry"], "fr") == "Bus et traversier"

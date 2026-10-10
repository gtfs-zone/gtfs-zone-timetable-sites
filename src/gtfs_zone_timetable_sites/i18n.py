"""Per-site page language: string catalogs, `t()` and date formats.

Catalogs map keys to text with `{var}` placeholders. A key whose text
depends on a count has `_one` and `_other` variants, picked by `n`. A key
missing from a locale's catalog falls back to English.
"""

import re
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Literal

Locale = Literal["en", "fr"]

EN: dict[str, str] = {
    # Lists and dates
    "and": "{rest} and {last}",
    "others_one": "{n} other",
    "others_other": "{n} others",
    "date.long": "{month} {day}, {year}",
    "date.short": "{month} {day}",
    "date.weekday": "{weekday} {month} {day}",
    "range.both": "{start} to {end}",
    "range.from": "from {start}",
    "range.to": "to {end}",
    "range.valid": "from {start} to {end}",
    # Day types and trip notes
    "days.weekday": "Weekday",
    "days.weekday_other": "Weekdays",
    "days.weekend": "Weekend",
    "days.weekend_other": "Weekends",
    "days.daily": "Daily",
    "days.range": "{first} to {last}",
    "days.plural": "{day}s",
    "note.only_days": "{days} only",
    "note.until": "Until {date}",
    "note.from": "From {date}",
    "note.not": "Not {dates}",
    "note.only": "Only {dates}",
    "day.runs_only": "Runs only on {dates}.",
    "day.no_service": "No service {dates}.",
    "day.also_runs": "Also runs {dates}.",
    # Modes
    "mode.light rail": "light rail",
    "mode.subway": "subway",
    "mode.train": "train",
    "mode.bus": "bus",
    "mode.ferry": "ferry",
    "mode.cable car": "cable car",
    "mode.gondola": "gondola",
    "mode.funicular": "funicular",
    "mode.trolleybus": "trolleybus",
    "mode.monorail": "monorail",
    "mode.transit": "transit",
    # Timetables
    "table.to": "To {places}",
    "table.every": "then every {minutes} min until {until}",
    "legend.note": "{letter}: {note}.",
    "legend.icon": "{icon}: {text}",
    "legend.pm": "PM times are in bold.",
    "legend.next_day": "+1: after midnight, the next day.",
    "legend.later_days": "+n: after midnight, n days later.",
    "legend.evening_before": "-1: the evening before.",
    "legend.untimed": "|: stops here, no scheduled time.",
    "legend.drop_off": "d: drop off only.",
    "legend.pick_up": "p: pick up only.",
    "legend.flag": "f: flag stop, stops only on request.",
    "legend.dwell": "Two times: arrives, then departs.",
    "amenity.bikes": "Bikes allowed",
    "amenity.bikes_all": "Bikes allowed on all trips.",
    "amenity.bikes_none": "No bikes on any trip.",
    "amenity.trips": "Wheelchair accessible trip",
    "amenity.trips_all": "All trips are wheelchair accessible.",
    "amenity.trips_none": "No trips are wheelchair accessible.",
    "amenity.stops": "Wheelchair accessible stop",
    "amenity.stops_all": "All stops are wheelchair accessible.",
    "amenity.stops_none": "No stops are wheelchair accessible.",
    "amenity.wheelchair": "Wheelchair accessible",
    # Maps
    "map.title": "Map of {title}",
    "map.system": "{site} {mode}",
    # Site index
    "index.title": "{site} schedules and timetables",
    "index.description_one": "{modes} schedules for {site}: timetables for {n}"
    " route, with times at each stop.",
    "index.description_other": "{modes} schedules for {site}: timetables for {n}"
    " routes, with times at each stop.",
    "index.expired": "Expired",
    "index.expired_badge": "Expired",
    "index.ended": "Ended {date}",
    "index.starts": "Starts {date}",
    "index.maps_one": "Map",
    "index.maps_other": "Maps",
    "index.about": "About",
    "index.website": "Website",
    "index.valid": "Timetables valid {range}.",
    "index.feed_link": "About this feed on list.gtfs.zone",
    # Route page
    "route.title": "{route} {mode} schedule - {site}",
    "route.description": "{route} {mode} schedule{between}{days}. Times at each"
    " stop and first and last trips, from {site}.",
    "route.between": " between {first} and {last}",
    "route.days": ": {days}",
    "route.expired": "This route's service ended on {date}. The timetable shown"
    " is its last schedule.",
    "route.upcoming": "This route's service starts on {date}. The timetable shown"
    " is its first schedule.",
    "route.intro_both": "The {route} {mode} route runs between {first} and"
    " {last}, with {days} timetables.",
    "route.intro_ends": "The {route} {mode} route runs between {first} and {last}.",
    "route.intro_days": "The {route} {mode} route has {days} timetables.",
    "route.trips_one": "{n} trip",
    "route.trips_other": "{n} trips",
    "route.trips_start": "Trips start at {time}.",
    "route.first_last": "First trip starts at {first}, last at {last}.",
    "route.zone_time": "{zone} time",
    "route.all_routes": "All routes",
    "route.agency_page": "Route page on the agency's site",
    # Footer
    "footer.generated": "Generated {date} from the agency's GTFS feed. Check with"
    " the agency before you travel.",
    "footer.source": "Schedule data from {publisher}{feed}{terms}.",
    "footer.feed": "GTFS feed",
    "footer.licensed": ", licensed under {licenses}",
    "footer.license_n": "license {n}",
    "footer.publisher_license": "the publisher's license",
    "footer.publisher_terms": ", on the publisher's terms",
    "footer.listed": "Listed in {catalogs}.",
    "footer.mobility_database": "the Mobility Database",
    "footer.more": "More GTFS tools at {link}",
}

FR: dict[str, str] = {
    "and": "{rest} et {last}",
    "others_one": "{n} autre",
    "others_other": "{n} autres",
    "date.long": "{day} {month} {year}",
    "date.short": "{day} {month}",
    "date.weekday": "{weekday} {day} {month}",
    "range.both": "du {start} au {end}",
    "range.from": "à partir du {start}",
    "range.to": "jusqu'au {end}",
    "range.valid": "du {start} au {end}",
    "days.weekday": "Semaine",
    "days.weekday_other": "Semaine",
    "days.weekend": "Fin de semaine",
    "days.weekend_other": "Fin de semaine",
    "days.daily": "Tous les jours",
    "days.range": "{first} au {last}",
    "days.plural": "{day}s",
    "note.only_days": "{days} seulement",
    "note.until": "Jusqu'au {date}",
    "note.from": "À partir du {date}",
    "note.not": "Sauf {dates}",
    "note.only": "Seulement {dates}",
    "day.runs_only": "En service seulement : {dates}.",
    "day.no_service": "Aucun service : {dates}.",
    "day.also_runs": "Aussi en service : {dates}.",
    "mode.light rail": "tramway",
    "mode.subway": "métro",
    "mode.train": "train",
    "mode.bus": "bus",
    "mode.ferry": "traversier",
    "mode.cable car": "tramway à câble",
    "mode.gondola": "téléphérique",
    "mode.funicular": "funiculaire",
    "mode.trolleybus": "trolleybus",
    "mode.monorail": "monorail",
    "mode.transit": "transport en commun",
    "table.to": "Vers {places}",
    "table.every": "puis toutes les {minutes} min jusqu'à {until}",
    "legend.note": "{letter} : {note}.",
    "legend.icon": "{icon} : {text}",
    "legend.pm": "Les heures de l'après-midi sont en gras.",
    "legend.next_day": "+1 : après minuit, le lendemain.",
    "legend.later_days": "+n : après minuit, n jours plus tard.",
    "legend.evening_before": "-1 : la veille au soir.",
    "legend.untimed": "| : s'arrête ici, sans heure prévue.",
    "legend.drop_off": "d : descente seulement.",
    "legend.pick_up": "p : montée seulement.",
    "legend.flag": "f : arrêt sur demande seulement.",
    "legend.dwell": "Deux heures : arrivée, puis départ.",
    "amenity.bikes": "Vélos permis",
    "amenity.bikes_all": "Vélos permis sur tous les départs.",
    "amenity.bikes_none": "Vélos interdits sur tous les départs.",
    "amenity.trips": "Départ accessible en fauteuil roulant",
    "amenity.trips_all": "Tous les départs sont accessibles en fauteuil roulant.",
    "amenity.trips_none": "Aucun départ n'est accessible en fauteuil roulant.",
    "amenity.stops": "Arrêt accessible en fauteuil roulant",
    "amenity.stops_all": "Tous les arrêts sont accessibles en fauteuil roulant.",
    "amenity.stops_none": "Aucun arrêt n'est accessible en fauteuil roulant.",
    "amenity.wheelchair": "Accessible en fauteuil roulant",
    "map.title": "Carte : {title}",
    "map.system": "{site}, {mode}",
    "index.title": "Horaires de {site}",
    "index.description_one": "Horaires de {site} ({modes_lower}) : horaires de"
    " {n} ligne, avec les heures à chaque arrêt.",
    "index.description_other": "Horaires de {site} ({modes_lower}) : horaires de"
    " {n} lignes, avec les heures à chaque arrêt.",
    "index.expired": "Lignes expirées",
    "index.expired_badge": "Expirée",
    "index.ended": "Terminée le {date}",
    "index.starts": "Débute le {date}",
    "index.maps_one": "Carte",
    "index.maps_other": "Cartes",
    "index.about": "À propos",
    "index.website": "Site web",
    "index.valid": "Horaires valides {range}.",
    "index.feed_link": "À propos de ce flux sur list.gtfs.zone",
    "route.title": "Horaires du {mode} {route} - {site}",
    "route.description": "Horaires du {mode} {route}{between}{days}. Heures à"
    " chaque arrêt, premiers et derniers départs, par {site}.",
    "route.between": " entre {first} et {last}",
    "route.days": " : {days}",
    "route.expired": "Le service de cette ligne a pris fin le {date}. L'horaire"
    " affiché est son dernier.",
    "route.upcoming": "Le service de cette ligne débute le {date}. L'horaire"
    " affiché est son premier.",
    "route.intro_both": "La ligne {route} ({mode}) relie {first} et {last}."
    " Horaires : {days}.",
    "route.intro_ends": "La ligne {route} ({mode}) relie {first} et {last}.",
    "route.intro_days": "Horaires de la ligne {route} ({mode}) : {days}.",
    "route.trips_one": "{n} départ",
    "route.trips_other": "{n} départs",
    "route.trips_start": "Départs à {time}.",
    "route.first_last": "Premier départ à {first}, dernier à {last}.",
    "route.zone_time": "heure {zone}",
    "route.all_routes": "Toutes les lignes",
    "route.agency_page": "Page de la ligne sur le site de l'organisme",
    "footer.generated": "Généré le {date} à partir du flux GTFS de l'organisme."
    " Vérifiez auprès de l'organisme avant de partir.",
    "footer.source": "Données d'horaires : {publisher}{feed}{terms}.",
    "footer.feed": "flux GTFS",
    "footer.licensed": ", sous {licenses}",
    "footer.license_n": "licence {n}",
    "footer.publisher_license": "la licence de l'éditeur",
    "footer.publisher_terms": ", selon les conditions de l'éditeur",
    "footer.listed": "Répertorié dans {catalogs}.",
    "footer.mobility_database": "la Mobility Database",
    "footer.more": "Plus d'outils GTFS sur {link}",
}

CATALOGS: dict[Locale, dict[str, str]] = {"en": EN, "fr": FR}

WEEKDAYS: dict[Locale, tuple[str, ...]] = {
    "en": (
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ),
    "fr": ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"),
}
WEEKDAYS_SHORT: dict[Locale, tuple[str, ...]] = {
    "en": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
    "fr": ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."),
}
MONTHS_SHORT: dict[Locale, tuple[str, ...]] = {
    "en": (
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ),
    "fr": (
        "janv.",
        "févr.",
        "mars",
        "avr.",
        "mai",
        "juin",
        "juil.",
        "août",
        "sept.",
        "oct.",
        "nov.",
        "déc.",
    ),
}


def plural(locale: Locale, n: int) -> str:
    """'one' or 'other': French counts 0 and 1 as one."""
    if locale == "fr":
        return "one" if 0 <= n < 2 else "other"
    return "one" if n == 1 else "other"


# A placeholder followed by a period, dropped when the value ends in one.
END_PERIOD = re.compile(r"\{(\w+)\}\.")


def text(locale: Locale, key: str, values: Mapping[str, object]) -> str:
    """The unfilled text for `key`: a value `n` picks the `_one` or `_other`
    variant, and a period after a value ending in one ('oct.') is dropped.
    Unknown keys raise KeyError."""
    n = values.get("n")
    if n is not None and f"{key}_one" in EN:
        key = f"{key}_{plural(locale, int(n))}"
    raw = CATALOGS[locale].get(key) or EN[key]
    return END_PERIOD.sub(
        lambda m: m[0][:-1] if str(values.get(m[1], "")).endswith(".") else m[0],
        raw,
    )


def t(locale: Locale, key: str, /, **values: object) -> str:
    """The text for `key` with `{var}` filled from `values`."""
    return text(locale, key, values).format(**values)


def capitalize(text: str) -> str:
    """The first letter in upper case, the rest unchanged."""
    return text[:1].upper() + text[1:]


def join_and(items: Sequence[str], locale: Locale = "en") -> str:
    """'A', 'A and B', 'A, B and C'."""
    if len(items) < 2:
        return "".join(items)
    return t(locale, "and", rest=", ".join(items[:-1]), last=items[-1])


def day_number(day: date, locale: Locale) -> str:
    """The day of the month, '1er' for the first in French."""
    return "1er" if locale == "fr" and day.day == 1 else str(day.day)


def long_date(day: date, locale: Locale = "en") -> str:
    """'Jan 2, 2026', '2 janv. 2026'."""
    month = MONTHS_SHORT[locale][day.month - 1]
    return t(
        locale, "date.long", month=month, day=day_number(day, locale), year=day.year
    )


def short_date(day: date, locale: Locale = "en") -> str:
    """'Oct 12', '12 oct.'."""
    month = MONTHS_SHORT[locale][day.month - 1]
    return t(locale, "date.short", month=month, day=day_number(day, locale))


def weekday_date(day: date, locale: Locale = "en") -> str:
    """'Sat Oct 31', 'sam. 31 oct.'."""
    return t(
        locale,
        "date.weekday",
        weekday=WEEKDAYS_SHORT[locale][day.weekday()],
        month=MONTHS_SHORT[locale][day.month - 1],
        day=day_number(day, locale),
    )

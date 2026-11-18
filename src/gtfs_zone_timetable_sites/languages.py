"""The languages a feed is published in, from feed_info, agency and
translations.txt. No I/O."""

from dataclasses import dataclass

from babel import Locale
from babel.core import get_global, parse_locale

from gtfs_zone_timetable_sites.gtfs.reader import Feed

# feed_lang for a feed whose text mixes languages, given per row in
# translations.txt.
MULTIPLE = "mul"

# CLDR's language codes, less those naming no one language (multiple,
# undetermined, no linguistic content, uncoded), its script codes and its
# aliases (`spa` -> `es`, `iw` -> `he`).
LANGUAGES = frozenset(Locale("en").languages) - {MULTIPLE, "und", "zxx", "mis"}
SCRIPTS = frozenset(Locale("en").scripts)
ALIASES: dict[str, str] = get_global("language_aliases")
# Codes that write a reading of a language's text, not a language of their
# own, so they get no page set: Japanese names in kana (17 of 21 measured
# Japanese feeds) and in romaji (Muroto's agency name, f-0191bb2ecd).
READINGS = frozenset({"ja-Hrkt", "ja-Latn"})


@dataclass(frozen=True, slots=True)
class FeedLanguages:
    default: str
    # Every language the feed has text in, the default first, then sorted.
    offered: tuple[str, ...]
    # Readings of the feed's text in another script (`ja-Hrkt`, `ja-Latn`),
    # sorted.
    readings: tuple[str, ...] = ()


def is_reading(code: str) -> bool:
    """Whether a normalized code is a reading (`ja-Hrkt`, `ja-Latn`)."""
    return code in READINGS


def normalize(code: str) -> str:
    """A GTFS language code as a BCP 47 tag: CLDR's language code in lowercase,
    the script in title case, the region dropped (`EN` -> `en`, `spa` -> `es`,
    `pl-PL` -> `pl`, `ja-hrkt` -> `ja-Hrkt`, `zh-Hant-TW` -> `zh-Hant`).
    Surrounding double quotes are dropped. Raises ValueError for a language or
    script CLDR does not name."""
    text = code.strip().strip('"').replace("_", "-")
    try:
        language, _, script, _ = parse_locale(text, sep="-")
    except ValueError as error:
        raise ValueError(f"unknown language code {code!r}") from error
    language = ALIASES.get(language, language)
    if language not in LANGUAGES or (script and script not in SCRIPTS):
        raise ValueError(f"unknown language code {code!r}")
    return f"{language}-{script}" if script else language


def feed_languages(feed: Feed) -> FeedLanguages:
    """The default language is feed_lang, else the first agency_lang, else
    `en`; with feed_lang `mul`, the first agency_lang, else the first
    translations.txt language. Offered is the default plus every agency_lang and
    translations.txt language, less readings (`ja-Hrkt`, `ja-Latn`), which go in
    `readings`. translations.txt rows with no language are skipped (Hofbus and
    Kajaani have rows with every field empty)."""
    feed_lang = feed.feed_info.lang if feed.feed_info else ""
    agency_langs = [
        normalize(agency.lang) for agency in feed.agencies.values() if agency.lang
    ]
    translated = [
        normalize(code)
        for code in dict.fromkeys(row.language for row in feed.translations)
        if code
    ]
    if feed_lang.lower() == MULTIPLE:
        candidates = [*agency_langs, *translated]
        if not candidates:
            raise ValueError("feed_lang is mul with no agency_lang or translations")
        default = candidates[0]
    elif feed_lang:
        default = normalize(feed_lang)
    else:
        default = agency_langs[0] if agency_langs else "en"
    codes = {*agency_langs, *translated} - {default}
    readings = {code for code in codes if is_reading(code)}
    return FeedLanguages(
        default=default,
        offered=(default, *sorted(codes - readings)),
        readings=tuple(sorted(readings)),
    )

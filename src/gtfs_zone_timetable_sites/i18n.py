"""Per-site page language: gettext catalogs, `t()` and date formats.

Catalogs live in `locales/<lang>/LC_MESSAGES/messages.po`, compiled to `.mo`.
Msgids are keys, msgstrs have `{var}` placeholders, and a key whose text
depends on a count is a plural message, picked by `n`. A key missing from a
locale's catalog falls back to English.
"""

import re
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from functools import cache
from gettext import GNUTranslations
from pathlib import Path
from typing import Annotated

from babel import Locale as BabelLocale
from babel.dates import format_date, get_day_names, match_skeleton
from babel.lists import format_list
from babel.numbers import format_currency
from pydantic import AfterValidator

LOCALES_DIR = Path(__file__).parent / "locales"
DOMAIN = "messages"
FALLBACK = "en"

# The UI languages: one directory per catalog under locales/.
UI_LOCALES = frozenset(p.name for p in LOCALES_DIR.iterdir() if p.is_dir())


def ui_locale(value: str) -> str:
    if value not in UI_LOCALES:
        raise ValueError(f"no UI catalog for {value!r}")
    return value


Locale = Annotated[str, AfterValidator(ui_locale)]


@cache
def translations(locale: str) -> GNUTranslations:
    """The compiled catalog for `locale`, falling back to English."""
    path = LOCALES_DIR / locale / "LC_MESSAGES" / f"{DOMAIN}.mo"
    try:
        with path.open("rb") as file:
            catalog = GNUTranslations(file)
    except FileNotFoundError:
        raise FileNotFoundError(
            f"{path} missing: run `pnpm run build:mo` to compile the catalogs"
        ) from None
    if locale != FALLBACK:
        catalog.add_fallback(translations(FALLBACK))
    return catalog


def message(locale: str, key: str, n: int | None = None) -> str:
    """The raw text for `key`, the plural form for `n` when the key has
    plurals. Unknown keys raise KeyError."""
    catalog = translations(locale)
    if n is not None:
        text = catalog.ngettext(key, key, n)
        if text != key:
            return text
    text = catalog.gettext(key)
    if text == key:
        raise KeyError(key)
    return text


# A placeholder followed by a period, dropped when the value ends in one.
END_PERIOD = re.compile(r"\{(\w+)\}\.")


def text(locale: str, key: str, values: Mapping[str, object]) -> str:
    """The unfilled text for `key`: a value `n` picks the plural form, and a
    period after a value ending in one ('oct.') is dropped."""
    n = values.get("n")
    raw = message(locale, key, None if n is None else int(str(n)))
    return END_PERIOD.sub(
        lambda m: m[0][:-1] if str(values.get(m[1], "")).endswith(".") else m[0],
        raw,
    )


def t(locale: str, key: str, /, **values: object) -> str:
    """The text for `key` with `{var}` filled from `values`."""
    return text(locale, key, values).format(**values)


# Counts that reach every CLDR plural category of the UI languages.
PLURAL_SAMPLES = (*range(200), 1000, 1_000_000)


def plural_forms(locale: str, key: str) -> dict[str, str]:
    """A plural key's text by CLDR category ('one', 'other', ...), as
    `Intl.PluralRules` names them."""
    rule = BabelLocale.parse(locale).plural_form
    forms: dict[str, str] = {}
    for n in PLURAL_SAMPLES:
        forms.setdefault(rule(n), message(locale, key, n))
    return forms


def capitalize(text: str) -> str:
    """The first letter in upper case, the rest unchanged."""
    return text[:1].upper() + text[1:]


def weekday_names(locale: str) -> tuple[str, ...]:
    """'Monday' to 'Sunday' in `locale`."""
    names = get_day_names("wide", locale=locale)
    return tuple(names[day] for day in range(7))


def join_and(items: Sequence[str], locale: str = "en") -> str:
    """'A', 'A and B', 'A, B, and C'."""
    return format_list(list(items), locale=locale)


# A day field in a CLDR date pattern, outside quoted literals.
DAY_FIELD = re.compile(r"(?<![d'])d(?!d)")


def date_text(day: date, skeleton: str, locale: str) -> str:
    """`day` in the CLDR pattern for `skeleton`. CLDR has no ordinal day, so
    French writes the first of the month as '1er' here."""
    babel_locale = BabelLocale.parse(locale)
    patterns = babel_locale.datetime_skeletons
    pattern = patterns[match_skeleton(skeleton, patterns)].pattern
    if babel_locale.language == "fr" and day.day == 1:
        pattern = DAY_FIELD.sub("'1er'", pattern)
    return format_date(day, pattern, locale=babel_locale)


def long_date(day: date, locale: str = "en") -> str:
    """'Jan 2, 2026', '2 janv. 2026'."""
    return date_text(day, "yMMMd", locale)


def short_date(day: date, locale: str = "en") -> str:
    """'Oct 12', '12 oct.'."""
    return date_text(day, "MMMd", locale)


def weekday_date(day: date, locale: str = "en") -> str:
    """'Sat, Oct 31', 'sam. 31 oct.'."""
    return date_text(day, "MMMEd", locale)


def price(amount: Decimal, currency: str, locale: str = "en") -> str:
    """'$1.25', '1,25 $US'."""
    return format_currency(amount, currency, locale=locale)

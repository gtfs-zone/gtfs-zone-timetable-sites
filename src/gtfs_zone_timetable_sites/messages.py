"""Catalog tooling: compiling `.po` to `.mo`, and extracting `t()` keys.

Not imported by the build: the CLI and the tests use it.
"""

from collections.abc import Collection, Iterator, Mapping
from pathlib import Path
from typing import IO

from babel.messages.extract import extract_javascript
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po
from jinja2.ext import babel_extract

from gtfs_zone_timetable_sites.i18n import DOMAIN, LOCALES_DIR

# Python calls t(locale, key): the key is the second argument.
KEYWORDS = {"t": (2,)}

type Extracted = tuple[int, str, tuple[str | None, ...], list[str]]


def po_paths() -> list[Path]:
    return sorted(LOCALES_DIR.glob(f"*/LC_MESSAGES/{DOMAIN}.po"))


def compile_catalogs(stale_only: bool = False) -> list[Path]:
    """Write each `.po` as `.mo` beside it; with `stale_only`, only those
    older than their `.po`. Returns the `.mo` files written."""
    written = []
    for po in po_paths():
        mo = po.with_suffix(".mo")
        if stale_only and mo.exists() and mo.stat().st_mtime >= po.stat().st_mtime:
            continue
        with po.open("rb") as file:
            catalog = read_po(file)
        with mo.open("wb") as file:
            write_mo(file, catalog)
        written.append(mo)
    return written


def shifted(results: Iterator[tuple]) -> Iterator[Extracted]:
    """Extractor results with the arguments moved one right, so t(key) in a
    template reads like t(locale, key) in Python."""
    for lineno, funcname, messages, comments in results:
        args = messages if isinstance(messages, tuple) else (messages,)
        yield lineno, funcname, (None, *args), comments


def extract_jinja(
    fileobj: IO[bytes],
    keywords: Collection[str],
    comment_tags: Collection[str],
    options: Mapping[str, object],
) -> Iterator[Extracted]:
    """Babel extractor for Jinja templates, which call t(key, ...)."""
    return shifted(babel_extract(fileobj, keywords, comment_tags, options))


def extract_js(
    fileobj: IO[bytes],
    keywords: Collection[str],
    comment_tags: Collection[str],
    options: Mapping[str, object],
) -> Iterator[Extracted]:
    """Babel extractor for the page scripts, which call t(key, ...)."""
    return shifted(extract_javascript(fileobj, keywords, comment_tags, options))

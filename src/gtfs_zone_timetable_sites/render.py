"""Jinja environment over templates/, rendering pages to bytes."""

from datetime import date
from functools import cache

from jinja2 import Environment, PackageLoader, StrictUndefined, pass_context
from jinja2.runtime import Context
from markupsafe import Markup

from gtfs_zone_timetable_sites import i18n


@pass_context
def translate(context: Context, key: str, **values: object) -> Markup:
    """`i18n.t` in the page's `locale`. Catalog text is trusted markup; values
    that are not Markup are escaped."""
    text = i18n.text(context["locale"], key, values)
    return Markup(text).format(**values)


@pass_context
def long_date(context: Context, day: date) -> str:
    return i18n.long_date(day, context["locale"])


@cache
def environment() -> Environment:
    env = Environment(
        loader=PackageLoader("gtfs_zone_timetable_sites", "templates"),
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.globals["t"] = translate
    env.filters["long_date"] = long_date
    return env


def asset(name: str) -> bytes:
    """A static file from templates/."""
    env = environment()
    return env.loader.get_source(env, name)[0].encode()


def render(template: str, **context: object) -> bytes:
    """Render templates/<template>."""
    return environment().get_template(template).render(**context).encode()

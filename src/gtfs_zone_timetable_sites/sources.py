"""feed-catalog's source-stats.json as the sources page shows it."""

from collections import Counter
from datetime import date

# Catalog names and links, as feed-catalog's pages name them.
CATALOGS = {
    "transitland": ("Transitland", "https://github.com/transitland/transitland-atlas"),
    "mobilitydatabase": ("Mobility Database", "https://mobilitydatabase.org"),
    "ntd": ("National Transit Database", "https://www.transit.dot.gov/ntd"),
    "gtfszone": ("rt.gtfs.zone", "https://rt.gtfs.zone"),
}
# Outcomes and evidence kinds with a `sources.<group>.<key>` text; others
# show their key.
FETCH_OUTCOMES = frozenset({"ok", "not_modified", "http_error", "error", "too_large"})
FACTS_OUTCOMES = frozenset(
    {
        "ok",
        "empty",
        "http_error",
        "missing_files",
        "not_zip",
        "parse_error",
        "timeout",
        "memory",
        "error",
    }
)
EVIDENCE = frozenset({"url", "sha256", "cleanedHash", "reference", "realtimeSibling"})


def catalog_name(key: str) -> dict:
    """A catalog's key, name and link ("" for a catalog not in CATALOGS,
    named by its key)."""
    name, url = CATALOGS.get(key, (key, ""))
    return {"key": key, "name": name, "url": url}


def outcome_rows(catalogs: list[dict], field: str, known: frozenset[str]) -> list[dict]:
    """One row per outcome in any catalog's `field`, most common over all
    catalogs first, with its count per catalog in `catalogs` order."""
    totals: Counter[str] = Counter()
    for catalog in catalogs:
        totals.update(catalog.get(field) or {})
    return [
        {
            "key": key,
            "known": key in known,
            "counts": [(catalog.get(field) or {}).get(key, 0) for catalog in catalogs],
            "total": total,
        }
        for key, total in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def sources_page(stats: dict) -> dict:
    """The sources page's content from source-stats.json: catalogs with the
    most feeds first, each with its share of schedule feeds no other catalog
    lists; download and contents outcomes per catalog; the combinations and
    pairs as given; feeds joined by each kind of evidence, most first; and
    entries per feed in order. A missing field is empty."""
    catalogs = sorted(
        (
            {"name": catalog_name(key)} | value
            for key, value in (stats.get("catalogs") or {}).items()
        ),
        key=lambda c: (-c.get("feeds", 0), c["name"]["key"]),
    )
    for catalog in catalogs:
        schedules = catalog.get("scheduleFeeds", 0)
        catalog["only_share"] = (
            catalog.get("onlyHere", 0) / schedules if schedules else None
        )
    duplication = stats.get("duplication") or {}
    evidence = duplication.get("byEvidence") or {}
    return {
        "generated": (made := stats.get("generated_at"))
        and date.fromisoformat(made[:10]),
        "catalogs": catalogs,
        "fetch": outcome_rows(catalogs, "fetch", FETCH_OUTCOMES),
        "facts": outcome_rows(catalogs, "facts", FACTS_OUTCOMES),
        "combinations": [
            {"catalogs": [catalog_name(k) for k in c["catalogs"]], "feeds": c["feeds"]}
            for c in stats.get("combinations") or []
        ],
        "pairs": [
            p | {"catalogs": [catalog_name(k) for k in p["catalogs"]]}
            for p in stats.get("pairs") or []
        ],
        "feeds": duplication.get("feeds", 0),
        "multi_entry": duplication.get("multiEntry", 0),
        "evidence": [
            {"key": key, "known": key in EVIDENCE, "feeds": n}
            for key, n in sorted(evidence.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        "entries_per_feed": sorted(
            (
                (int(n), feeds)
                for n, feeds in (duplication.get("entriesPerFeed") or {}).items()
            ),
        ),
    }

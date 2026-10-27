# AGENTS.md

Generates a static timetable website per agency from GTFS, for the feeds in
`sites.yaml` plus the feed-catalog feeds its `catalog:` filter takes, served
from a Garage bucket at `sites.gtfs.zone/<slug>/`. A `v*` tag builds the image
and commits its digest into `gtfs-zone-infra/gtfs`; pushes to `main` do not deploy.

## Commands

The `timetable-sites` CLI (`dev`, `build`, `dump`, `serve`, `sizes`) and the
pipeline's env vars are in [README.md](README.md).

```bash
uv sync --all-extras          # include the Dagster pipeline
pnpm run build:css            # rebuild templates/style.css (committed) after template changes
```

## Architecture

| Module | What it does |
|---|---|
| `config.py` | Pydantic models for `sites.yaml` and a feeds.json entry (`CatalogFeed`); defaults merged into each site |
| `catalog.py` | feeds.json plus `sites.yaml` to resolved sites: catalog filter, romanized slugs pinned with aliases for renames and moved feeds, shard per slug |
| `pool.py` | Runs a function per item in memory-capped worker processes, one process per item |
| `gtfs/reader.py` | GTFS zip to typed rows, streaming, only needed columns |
| `gtfs/service.py` | Calendars to day types (Weekday / Saturday / Sunday / exceptions) |
| `timetable.py` | Route + direction + day type to a `Timetable`, plus a text dump; stop order is gtfs-zone-web-common's topological sort, falling back to an LCS fold on cycles |
| `strip.py` | Stop-column rail lanes, endpoints and minority stops, ported from gtfs-zone-web-common's `route-graph.ts` / `route-strip.ts` |
| `colors.py` | Hashed color for a route without `route_color`, a hex-exact port of gtfs-zone-web-common's `route-colors.ts` |
| `build.py` | `build_site(zip_bytes, site) -> {path: bytes}`, the only entry point |
| `facts.py` | What a parsed zip contains (feed_info, service range, agencies, counts) for the content report |
| `maps/svg.py` | Route and system maps as inline SVG: Web Mercator, Douglas-Peucker, label placement (none on bus-only system maps), basemap tiles |
| `maps/split.py` | A mode's routes to system maps by grid density: local clusters, then long routes grouped by overlap with the rest sharing one map; one map when that fragments the mode |
| `i18n.py` | `en` and `fr` string catalogs, `t()` with `_one` / `_other` plurals, weekday and month names for dates |
| `render.py` | Jinja environment over `templates/`, with `t()` as a global in the page's `locale` |
| `templates/` | Page templates and CSS (Tailwind + daisyUI, `style.css` built by `pnpm run build:css` and committed) |
| `tests/fixtures/<name>/` | Synthetic GTFS feeds as text files, zipped by `fixture_zip` |
| `cli.py` | `timetable-sites build`, `dump`, `sizes`, `serve` and `dev`; downloads and writes files locally, with the root pages built as in the bucket |
| `pipeline/` | Dagster code location (a partition per shard of sites) in feed-catalog's instance: download, build and upload changed files per site in workers, shard file and content report, skip known-bad feeds, rewrite the root, Gatus heartbeat |

- The core is pure: nothing outside `cli.py` and `pipeline/` does network or
  disk I/O, so `build_site` can later run in Pyodide or on the fly.
- Page text comes from the catalogs in `i18n.py`: templates call `t(key, ...)`,
  Python takes a `locale` argument. A new string goes in both `EN` and `FR`.
  GTFS field names and values stay literal.
- `timetable.py`, `strip.py` and `colors.py` port gtfs-zone-web-common logic.
  Keep them in step with the TypeScript (`colors.py` is hex-exact).

## Conventions

- **Commits**: Conventional Commits, enforced by the `commit-msg` hook. Never add
  Co-Authored-By trailers. Setup and release are in [CONTRIBUTING.md](CONTRIBUTING.md).
- **Verification**: no Playwright or other browser automation; pages are checked
  by hand on a phone and a desktop.
- **Logging**: module loggers are named `log`, never `logger`.
- **Cross-repo work**: sibling gtfs.zone repos live under the same parent
  directory and may be read and edited when a change spans repos.
- **Plans**: write plans to `CURRENT_PLAN.md` at the repo root as a
  checklist (`- [ ]`), ticked off as work lands. It is neither tracked nor
  gitignored: never stage or commit it.

# gtfs-zone-timetable-sites

[![CI](https://img.shields.io/github/actions/workflow/status/gtfs-zone/gtfs-zone-timetable-sites/check.yml?branch=main&label=CI)](https://github.com/gtfs-zone/gtfs-zone-timetable-sites/actions/workflows/check.yml?query=branch%3Amain) [![License: AGPL-3.0-or-later](https://img.shields.io/badge/license-AGPL--3.0--or--later-blue)](LICENSE.txt) [![sites.gtfs.zone](https://img.shields.io/website?url=https%3A%2F%2Fsites.gtfs.zone&label=sites.gtfs.zone)](https://sites.gtfs.zone) [![Container image](https://img.shields.io/badge/image-ghcr.io-blue?logo=docker&logoColor=white)](https://github.com/gtfs-zone/gtfs-zone-timetable-sites/pkgs/container/gtfs-zone-timetable-sites)

Static timetable websites generated from GTFS, served at
[sites.gtfs.zone](https://sites.gtfs.zone). One site per agency, listed in
`sites.yaml` or taken from the gtfs.zone feed catalog: an index of routes, printed-style timetables per route, direction
and service day, and a simple route map. Plain HTML and CSS, no JavaScript
needed to read a timetable.

## Running Locally

```bash
uv sync
uv run timetable-sites dev                                   # build the dev: feeds in sites.yaml and the home page, serve, rebuild on changes
uv run timetable-sites dev --site columbia-county            # the same, one site only (faster rebuilds)
uv run timetable-sites dev --refresh                         # download the feeds again instead of using .cache/feeds/
uv run timetable-sites build                                 # clear dist/, build every site and the home page
uv run timetable-sites build --listed                        # only the sites listed in sites.yaml, not the catalog's
uv run timetable-sites build --dev                           # only the dev: feeds in sites.yaml
uv run timetable-sites build --country US --limit 50 --workers 4 --cache .cache/feeds   # a sample of catalog sites
uv run timetable-sites build --site columbia-county         # rebuild dist/columbia-county/ and the home page
uv run timetable-sites build --site columbia-county --zip feed.zip   # use a local zip
uv run timetable-sites serve                                 # serve dist/ on the LAN, port 8000
uv run timetable-sites sizes                                 # largest built pages, gzip and raw
```

`build` resolves a site's `feed:` id to its download URL through
`data.gtfs.zone/feeds.json` at build time, or uses the site's `url:` directly.
With `--cache`, feeds.json and the slugs assigned to catalog feeds are kept
there too.

## Configuration

`sites.yaml` holds `defaults`, an optional `catalog` filter and a list of
`sites`.

`catalog` makes a site of every feed in `data.gtfs.zone/feeds.json` whose
schedule is up and that passes `countries` (ISO codes, empty for all),
`max_bytes` (zip size) and `exclude` (feed ids). Its slug comes from the feed
name and is pinned in the bucket's `slugs.json`, so it never changes or goes to
another feed.

Each listed site needs exactly one of `feed` or `url`, a `slug` when it has a
`url` (optional for a `feed`), and may override any default. A listed `feed` is
built whatever the catalog filter says:

| Option | Values | Default |
|---|---|---|
| `map` | `svg`, `none` | `svg` |
| `horizon_days` | days used to derive day types | `28` |
| `time_format` | `12h`, `24h` | `12h` |
| `timepoints` | `auto`, `all`, `timepoint-flag` | `auto` |
| `basemap` | `none`, a Stadia style, or `{light: <style>, dark: <style>}` to follow the color scheme (tiles under the svg map) | `none` |

Stadia styles: `stadia-alidade-smooth`, `stadia-alidade-smooth-dark`,
`stadia-alidade-bright`, `stadia-alidade-satellite`, `stadia-outdoors`,
`stadia-osm-bright`, `stadia-toner`, `stadia-toner-lite`, `stadia-toner-dark`,
`stadia-toner-blacklite`, `stadia-toner-background`, `stadia-terrain`,
`stadia-terrain-background`, `stadia-watercolor`.

`title` names the site, and `routes` filters routes by `route_types`,
`route_ids`, `exclude_route_types` and `exclude_route_ids`. Unknown keys are an
error.

Every site's footer credits the publisher (from `feed_info.txt`, else the first
agency), links the GTFS download and the license, and links the feed's
Transitland and Mobility Database pages. The license comes from `feeds.json`'s
`licenses`; `license_url` on a site sets or overrides it, and is the only
source for a `url:` site.

Routes without a valid `route_color` get a color hashed from their `route_id`,
the same one gtfs-zone-web-common and gtfs-zone-editor use.

## Library

`gtfs_zone_timetable_sites.build.build_site(zip_bytes, site)` returns `{path: bytes}` for one
site and does no I/O, so it can run anywhere Python does.

## Pipeline

`gtfs_zone_timetable_sites.pipeline.definitions` is a Dagster code location with 16
partitions, each a shard of the sites by slug. A daily schedule at 11:00 UTC
runs every shard. A run resolves the sites from `sites.yaml` and feeds.json,
then for each of its shard's sites, in worker processes: download the feed,
build it, upload the files whose hash changed to the `sites.gtfs.zone` bucket,
delete ones no longer built and write `<slug>/manifest.json`. A site that fails
keeps its previous pages. The run then writes `_shards/<nn>.json` and
`_content/<nn>.json`, and rewrites the bucket root from every shard file (country
index, a page per country, a sitemap index over one sitemap per shard,
`robots.txt`, `error.html`), deleting sites no longer resolved unless the site
count fell by more than 20%. A Gatus heartbeat is pushed once 95% of sites have
been built that day, not counting feeds known to be unusable.

The content report `_content/<nn>.json`, listed in `_content/index.json`, holds
per site the outcome of its last download (`ok`, `not_zip`, `missing_files`,
`parse_error`, `http_error`, `timeout`, `memory` or `error`) and the day that
outcome began. For an `ok` feed it also holds the zip's size and hash,
feed_info, service range, agencies, counts and route types. feed-catalog merges
it into feeds.json. A feed that was `not_zip`, `missing_files` or
`parse_error` is not downloaded again until its catalog size or Last-Modified,
its URL or the timetable-sites version changes, or a week passes.

```bash
uv sync --extra pipeline
S3_ENDPOINT=... S3_ACCESS_KEY=... S3_SECRET_KEY=... uv run dagster dev -m gtfs_zone_timetable_sites.pipeline.definitions
```

Environment: `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`
(default `sites.gtfs.zone`), `S3_REGION` (default `garage`), `GATUS_URL`,
`GATUS_TOKEN`, `TIMETABLE_SITES_CONFIG` (default `sites.yaml`), `TIMETABLE_SITES_WORKERS`
(default 4), `TIMETABLE_SITES_WORKER_MEMORY` (address space per worker in bytes,
default 2500000000).

## License

AGPL-3.0-or-later. See `LICENSE.txt`.

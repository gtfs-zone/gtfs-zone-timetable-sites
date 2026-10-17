## v0.14.0 (2026-10-17)

### Feat

- **reader**: treat a route_color shared by every route as no color

## v0.13.0 (2026-10-10)

### Feat

- **catalog**: take feeds from every country, with romanized slugs and aliases
- add a per-site locale option with French page text

## v0.12.1 (2026-10-02)

## v0.12.0 (2026-10-01)

### BREAKING CHANGE

- the module is now gtfs_zone_timetable_sites

### Refactor

- rename the package to gtfs-zone-timetable-sites

## v0.11.1 (2026-10-01)

## v0.11.0 (2026-09-30)

### Feat

- **maps**: split system maps into local and long-route maps by grid density

## v0.10.1 (2026-09-29)

### Fix

- **root**: label feeds that built with no routes as having no routes

## v0.10.0 (2026-09-29)

### Feat

- **root**: list every file with its source, date range and status; keep expired routes

## v0.9.0 (2026-09-29)

### Feat

- build whole catalog feeds, group routes and maps by mode, show feed status

### Fix

- **timetable**: keep time cells under the sticky stop column

## v0.8.0 (2026-09-29)

### Feat

- **footer**: credit the publisher, license and catalogs
- **content**: publish a per-site content report and skip known-bad feeds
- **colors**: hash a color for routes without route_color

### Fix

- **timetable**: draw the stop rail above table rows

## v0.7.0 (2026-09-29)

### Feat

- **catalog**: build sites from geometry-car feeds.json in shards

## v0.6.0 (2026-09-29)

### Feat

- **maps**: label trip ends and caption stops on hover

## v0.5.0 (2026-09-29)

### Feat

- **seo**: sitemap, IndexNow, amenity legend, multiday service and page updates

## v0.4.0 (2026-09-28)

### Feat

- **root**: brand color dot per agency

## v0.3.0 (2026-09-28)

### Feat

- **timetable**: branching rail strip and one-cell dwell times

### Fix

- **pipeline**: serve svg as image/svg+xml, rebuild every site on a new version

## v0.2.0 (2026-09-28)

### Feat

- dev command, basemap styles, clickable system map and logo
- dagster pipeline publishing sites to the bucket
- timetables, route and home pages, SVG maps and page size report
- scaffold package, config, build and serve CLI

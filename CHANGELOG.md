## v0.23.1 (2026-11-19)

### Fix

- **pages**: fares, translations and language codes no longer fail a site

## v0.23.0 (2026-11-18)

### Feat

- **content**: stop location, place and place tags in the content report
- **reader**: read only trips whose service runs in a route's window
- **build**: build_site reads the zip from bytes or a binary file
- **i18n**: Dutch UI catalog
- **i18n**: Swedish UI catalog
- **i18n**: Finnish UI catalog
- **i18n**: German UI catalog
- **i18n**: Polish UI catalog
- **i18n**: Spanish UI catalog
- **i18n**: Italian UI catalog
- **i18n**: Japanese UI catalog
- **i18n**: root and country pages in each UI language, picker, hreflang and sitemap alternates
- **i18n**: translations.txt applied to each page set's feed text, kana readings beside stop names
- **i18n**: a page set per feed language with picker, hreflang and sitemap alternates
- **i18n**: root, country and error pages through the catalogs, root.js text as JSON
- **i18n**: gettext catalogs for en and fr, dates and lists from Babel
- **i18n**: read feed_lang, agency_lang and translations.txt, feed languages in the content report
- **fares**: site fares page with every fare by network or agency and transfers in words
- **fares**: Fares v2 on route pages, by area with media and timeframes
- **fares**: zone fare charts and zone badges on route pages
- **fares**: flat Fares v1 card on route pages
- **reader**: read stop zones and Fares v1 and v2 tables
- **live**: one white border for vehicle dots and arrows
- **maps**: route map stops never overlap, insets where they reveal hidden stops
- **timetable**: block continuations as lettered column notes
- **live**: mark each running trip's next stop instead of the live edge
- **timetable**: stop codes, platforms, block continuations and per-day validity
- **timetable**: show every stop, dim approximate times, one system map line width for all modes

### Fix

- **pages**: service range and table dates from the calendars, feed_info dates shown beside them
- **i18n**: fi, sv and nl route titles and intros start with a word, not the lowercase mode
- **i18n**: day labels inside trip notes start lowercase where weekday names do
- **i18n**: stand-alone weekday names, tests follow the set of UI catalogs

### Perf

- **pipeline**: stream each download to a temporary file
- **reader**: thin shapes with Douglas-Peucker as they are read
- **reader**: share repeated stop ids, times and trip strings
- **service**: index service_ids by date once per feed, drop unused calendars on read

## v0.22.3 (2026-11-14)

### Fix

- **publish**: retry out-of-memory sites alone with the whole memory cap

## v0.22.2 (2026-11-14)

### Fix

- **map**: fetch realtime feeds through cors.gtfs.zone

## v0.22.1 (2026-11-13)

### Fix

- **publish**: upload only changed root files and fewer threads per site

## v0.22.0 (2026-11-13)

### Feat

- **publish**: skip date-only page changes and upload in parallel

## v0.21.2 (2026-11-11)

### Fix

- **titles**: name the town for sites that share a feed name

## v0.21.1 (2026-11-05)

### Fix

- **docker**: install git in the builder for the gtfs-zone-vocab git dependency

## v0.21.0 (2026-11-05)

### Feat

- route modes from gtfs-zone-vocab, with coach, air, taxi and misc modes

## v0.20.0 (2026-11-05)

### Feat

- **pipeline**: bucket over obstore instead of boto3

## v0.19.1 (2026-11-05)

### Fix

- **timetable**: show every stop for extended rail, metro, tram and ferry route types

## v0.19.0 (2026-11-04)

### Feat

- agency search, world map and region maps on the root pages
- live times in timetables, rt-viewer style vehicles with popups

### Fix

- **maps**: scale vehicles with zoom, small but visible zoomed out
- **maps**: fetch rt.gtfs.zone vehicles without the CORS proxy

## v0.18.1 (2026-11-03)

### Fix

- **maps**: fetch rt.gtfs.zone vehicles without the CORS proxy

## v0.18.0 (2026-11-03)

### Feat

- MapLibre route and system maps with a route accordion

## v0.17.0 (2026-10-28)

### Feat

- link the GitHub repo from the header

## v0.16.0 (2026-10-28)

### Feat

- **seo**: name the state, province or country a feed runs in in titles, descriptions and JSON-LD

### Fix

- **catalog**: redirect a moved feed's slug to the feed it joined
- **deps**: upgrade packages with published security advisories

## v0.15.0 (2026-10-25)

### Feat

- **build**: merge routes that share agency, type, names and colors
- **seo**: build a site with the same zip as another as not indexed
- **seo**: add an index site option, set from catalog.index_countries
- **seo**: noindex expired routes and fully expired sites, and leave them out of the sitemap

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

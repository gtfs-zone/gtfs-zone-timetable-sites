"""Write the country and region boundaries the root pages map and group by.

    uv run --with shapely python scripts/boundaries.py --cache DIR

Countries come from Natural Earth admin-0 50m, regions (ADM1) per country from
geoBoundaries gbOpen. Both are simplified to a share of each map's extent and
rounded, then written as GeoJSON to src/gtfs_zone_timetable_sites/boundaries/.
Downloads are kept in --cache, so a rerun only fetches what is missing.
"""

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path

from shapely import make_valid, set_precision
from shapely.geometry import MultiPolygon, mapping, shape

NE_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
    "v5.1.2/geojson/ne_50m_admin_0_countries.geojson"
)
GB_API = "https://www.geoboundaries.org/api/current/gbOpen/{iso3}/ADM1/"
OUT = Path(__file__).parent.parent / "src/gtfs_zone_timetable_sites/boundaries"
# Simplification tolerance as a share of the map's extent.
WORLD_TOLERANCE = 0.0006
REGION_TOLERANCE = 0.003
WORLD_DIGITS = 2
REGION_DIGITS = 3
# Polygon parts smaller than this share of the map's extent squared are dropped.
MIN_AREA = 0.0001


def fetch(url: str, path: Path) -> Path:
    """`url` saved at `path`, downloaded only if missing."""
    if not path.exists():
        print("fetch", url)
        with urllib.request.urlopen(url, timeout=120) as response:
            path.write_bytes(response.read())
    return path


def simplified(geometry: dict, extent: float, share: float, digits: int) -> dict:
    """The geometry simplified to `share` of `extent` degrees, on a grid of
    `digits` decimals."""
    geom = make_valid(shape(geometry))
    parts = getattr(geom, "geoms", [geom])
    polygons = [p for p in parts if p.geom_type == "Polygon"]
    kept = [p for p in polygons if p.area >= MIN_AREA * extent**2]
    # The largest part stays, so a small country or region keeps its shape.
    geom = MultiPolygon(kept or [max(polygons, key=lambda p: p.area)])
    geom = set_precision(geom.simplify(extent * share), 10**-digits)
    return mapping(geom)


def extent(geometries: list) -> float:
    """The side of a square with the geometries' total area, in degrees; a
    bounding box would span the globe for a country across the antimeridian."""
    return sum(g.area for g in geometries) ** 0.5


def country_code(props: dict) -> str | None:
    """A Natural Earth country's ISO 3166-1 alpha-2 code; France and Norway
    have theirs only in ISO_A2_EH."""
    for key in ("ISO_A2", "ISO_A2_EH"):
        code = props.get(key)
        if code and code != "-99":
            return code
    return None


def write(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, separators=(",", ":"), ensure_ascii=False))
    print(f"{path.name}: {path.stat().st_size // 1024} KB")


def world(cache: Path) -> list[tuple[str, str]]:
    """Write world.json; (alpha-2, alpha-3) of each country."""
    path = fetch(NE_URL, cache / "ne_50m_admin_0_countries.geojson")
    doc = json.loads(path.read_text())
    features, codes = [], []
    for feature in doc["features"]:
        props = feature["properties"]
        code = country_code(props)
        if not code:
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {"key": code.lower(), "name": props["NAME"]},
                "geometry": simplified(
                    feature["geometry"], 360, WORLD_TOLERANCE, WORLD_DIGITS
                ),
            }
        )
        codes.append((code, props["ADM0_A3"]))
    write(OUT / "world.json", {"type": "FeatureCollection", "features": features})
    return codes


def regions(cache: Path, code: str, iso3: str) -> None:
    """Write <code>.json with the country's ADM1 regions, if geoBoundaries has them."""
    try:
        meta_path = fetch(GB_API.format(iso3=iso3), cache / f"gb-{iso3}.json")
    except urllib.error.HTTPError as error:
        print(f"skip {code}: {error}")
        return
    meta = json.loads(meta_path.read_text())
    doc = json.loads(
        fetch(
            meta["simplifiedGeometryGeoJSON"], cache / f"gb-{iso3}-adm1.geojson"
        ).read_text()
    )
    shapes = [shape(f["geometry"]) for f in doc["features"]]
    size = extent(shapes)
    features = []
    for feature in doc["features"]:
        props = feature["properties"]
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "id": props.get("shapeISO") or props["shapeID"],
                    "name": props["shapeName"],
                },
                "geometry": simplified(
                    feature["geometry"], size, REGION_TOLERANCE, REGION_DIGITS
                ),
            }
        )
    write(
        OUT / f"{code.lower()}.json",
        {
            "type": "FeatureCollection",
            "name": meta["boundaryName"],
            "license": meta["boundaryLicense"],
            "license_url": license_url(meta["boundaryLicense"]),
            "source": meta.get("boundarySource", ""),
            "source_url": release_url(meta["simplifiedGeometryGeoJSON"]),
            "year": meta.get("boundaryYearRepresented", ""),
            "features": features,
        },
    )


# geoBoundaries license names to their texts; Public Domain has none.
LICENSE_URLS = {
    "Open Data Commons Open Database License 1.0": "https://opendatacommons.org/licenses/odbl/1-0/",
    "Creative Commons Attribution 4.0 International (CC BY 4.0)": "https://creativecommons.org/licenses/by/4.0/",
    "Creative Commons Attribution 4.0 (CC BY 4.0)": "https://creativecommons.org/licenses/by/4.0/",
    "Creative Commons Attribution 3.0 Intergovernmental Organisations (CC BY 3.0 IGO)": "https://creativecommons.org/licenses/by/3.0/igo/",
    "Creative Commons Attribution 3.0 License": "https://creativecommons.org/licenses/by/3.0/",
    "Creative Commons Attribution 2.5 Generic": "https://creativecommons.org/licenses/by/2.5/",
    "Creative Commons Attribution 2.5 India (CC BY 2.5 IN)": "https://creativecommons.org/licenses/by/2.5/in/",
    "Creative Commons Attribution-ShareAlike 2.0": "https://creativecommons.org/licenses/by-sa/2.0/",
    "Creative Commons Attribution-ShareAlike 3.0 Unported": "https://creativecommons.org/licenses/by-sa/3.0/",
    "CC0 1.0 Universal (CC0 1.0) Public Domain Dedication": "https://creativecommons.org/publicdomain/zero/1.0/",
    "Etalab Open License 2.0": "https://www.etalab.gouv.fr/licence-ouverte-open-licence/",
    "Open Government Canada 2.0": "https://open.canada.ca/en/open-government-licence-canada",
    "Data license Germany - Attribution - Version 2.0": "https://www.govdata.de/dl-de/by-2-0",
    "Federal Office of Topography swisstopo License": "https://www.swisstopo.admin.ch/en/terms-of-use-free-geodata-and-geoservices",
}


def license_url(name: str) -> str:
    """The text of a geoBoundaries license, by its name."""
    if name not in LICENSE_URLS and name != "Public Domain":
        print(f"no license URL for {name!r}")
    return LICENSE_URLS.get(name, "")


def release_url(geometry_url: str) -> str:
    """The geoBoundaries GitHub folder holding a release's metadata and
    citation, from the URL of one of its files."""
    folder = geometry_url.rsplit("/", 1)[0]
    return folder.replace("/raw/", "/tree/", 1)


SOURCES_HEAD = """\
# Boundary data

Generated by `scripts/boundaries.py`; do not edit by hand.

- `world.json`: country borders from [Natural Earth](https://www.naturalearthdata.com)
  admin-0 50m, version 5.1.2, public domain.
- `<cc>.json`: ADM1 regions per country from
  [geoBoundaries](https://www.geoboundaries.org) gbOpen, simplified. geoBoundaries
  itself is CC BY 4.0: Runfola, D. et al. (2020) geoBoundaries: A global database
  of political administrative boundaries. PLoS ONE 15(4): e0231866.
  https://doi.org/10.1371/journal.pone.0231866. Each country's data stays under
  the license of its original source, below. Share-alike licenses (ODbL,
  CC BY-SA) apply to the derived files here and to the `regions.json` files built
  from them, which are published under the same license.

| File | Country | Source | Year | License |
|---|---|---|---|---|
"""


def cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def write_sources() -> None:
    """SOURCES.md: the source and license of every region file in OUT."""
    rows = []
    for path in sorted(OUT.glob("*.json")):
        if path.name == "world.json":
            continue
        doc = json.loads(path.read_text())
        license_text = cell(doc["license"])
        if doc.get("license_url"):
            license_text = f"[{license_text}]({doc['license_url']})"
        source = cell(doc.get("source", ""))
        if doc.get("source_url"):
            source = f"[{source or 'geoBoundaries'}]({doc['source_url']})"
        rows.append(
            f"| `{path.name}` | {cell(doc.get('name', ''))} | "
            f"{source} | {doc.get('year', '')} | {license_text} |"
        )
    (OUT / "SOURCES.md").write_text(SOURCES_HEAD + "\n".join(rows) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--countries", nargs="*", help="alpha-2 codes; default all")
    args = parser.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    wanted = {c.upper() for c in args.countries or ()}
    for code, iso3 in world(args.cache):
        if not wanted or code in wanted:
            regions(args.cache, code, iso3)
    write_sources()


if __name__ == "__main__":
    main()

import json
import re
from datetime import date

from conftest import fixture_files, make_zip

from gtfs_zone_timetable_sites.build import build_site, script_json
from gtfs_zone_timetable_sites.config import CatalogFeed, Site
from gtfs_zone_timetable_sites.maps.geo import lines_of, simplify, tolerance

MONDAY = date(2026, 10, 5)


def build(files: dict[str, str], **options) -> dict[str, str]:
    site = Site(slug="test", url="https://example.org/g.zip", **options)
    built = build_site(make_zip(files), site, today=MONDAY)
    return {path: body.decode() for path, body in built.items()}


def route_data(page: str) -> dict:
    found = re.search(
        r'<script type="application/json" class="route-data">(.*?)</script>', page
    )
    assert found
    return json.loads(found.group(1))


def test_simplify_drops_collinear_points():
    points = [(0.0, 0.0), (1.0, 0.1), (2.0, 0.0), (3.0, 5.0)]
    assert simplify(points, 1.0) == [(0.0, 0.0), (2.0, 0.0), (3.0, 5.0)]


def test_simplify_keeps_short_paths():
    assert simplify([(0.0, 0.0), (1.0, 1.0)], 1.0) == [(0.0, 0.0), (1.0, 1.0)]


def test_lines_round_to_lon_lat_and_drop_repeats():
    # A 2 m wiggle on a 1 km line goes at a 6 m tolerance.
    path = [(42.0, -73.0), (42.00002, -72.994), (42.0, -72.988), (42.0, -72.988)]
    assert lines_of([path], 6.0) == [((-73.0, 42.0), (-72.988, 42.0))]


def test_lines_drop_what_an_earlier_line_retraces():
    out = [(42.0, -73.0), (42.0, -72.9), (42.05, -72.9)]
    back = [(42.00003, -72.95), (42.00003, -73.0)]
    spur = [(42.0, -72.95), (42.0, -72.95), (41.95, -72.95)]
    lines = lines_of([out, back[::-1], back, spur], 6.0)
    # The return trip is gone; the spur keeps only its new part.
    assert lines[0] == ((-73.0, 42.0), (-72.9, 42.0), (-72.9, 42.05))
    assert len(lines) == 2 and lines[1][-1] == (-72.95, 41.95)


def test_tolerance_grows_with_the_map():
    city = [[(42.0, -73.0), (42.1, -73.1)]]
    country = [[(25.0, -125.0), (49.0, -67.0)]]
    assert tolerance(city, 6.0, 1 / 20_000) == 6.0
    assert 200 < tolerance(country, 6.0, 1 / 20_000) < 300


def test_route_map_from_stop_sequences_numbers_stops_like_the_timetable():
    page = build(fixture_files("branching"))["4/index.html"]
    data = route_data(page)
    assert data["lines"] and data["rt"] is None
    names = {num: name for _, _, num, name, _ in data["stops"]}
    assert [num for _, _, num, _, _ in data["stops"]] == sorted(names)
    rows = re.findall(r'<th scope="row" data-num="(\d+)">.*?>([A-Z][a-z]+)<', page)
    assert rows and all(names[int(num)] == name for num, name in rows)


def test_route_map_marks_trip_ends():
    data = route_data(build(fixture_files("branching"))["4/index.html"])
    # Charlie is where back_short ends; Bravo, Echo and Foxtrot are passed through.
    ends = {name for *_, name, end in data["stops"] if end}
    assert ends == {"Alpha", "Charlie", "Delta", "Golf"}


def test_rare_short_turns_are_not_ends():
    files = fixture_files("branching")
    # 20 more full northbound trips put back_short's one end under the 5% share.
    times = [
        f"n{i},07:{i:02d}:00,07:{i:02d}:00,{s},{k}"
        for i in range(20)
        for k, s in enumerate("ABCD", 1)
    ]
    trips = [f"R,DAILY,n{i},North,0" for i in range(20)]
    files["stop_times.txt"] = "\n".join([files["stop_times.txt"].rstrip(), *times])
    files["trips.txt"] = "\n".join([files["trips.txt"].rstrip(), *trips])
    data = route_data(build(files)["4/index.html"])
    assert "Charlie" not in {name for *_, name, end in data["stops"] if end}


def with_shape(files: dict[str, str]) -> dict[str, str]:
    trips = files["trips.txt"].splitlines()
    files["trips.txt"] = "\n".join(
        [trips[0] + ",shape_id", *(row + ",S" for row in trips[1:])]
    )
    files["shapes.txt"] = (
        "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n"
        "S,42.0,-73.0,1\nS,42.0,-73.6,2\nS,42.6,-73.6,3\n"
    )
    return files


def test_route_map_uses_shapes_when_present():
    data = route_data(build(with_shape(fixture_files("branching")))["4/index.html"])
    assert data["lines"] == [[[-73.0, 42.0], [-73.6, 42.0], [-73.6, 42.6]]]


def test_system_map_shares_lines_and_lists_transfers():
    files = with_shape(fixture_files("branching"))
    # A second route running the same trips over the same shape and stops.
    files["routes.txt"] += "S,5,Spur,3\n"
    head, *rows = files["trips.txt"].splitlines()
    copies = [
        ",".join(["S", cols[1], f"s-{cols[2]}", *cols[3:]])
        for cols in (row.split(",") for row in rows)
    ]
    files["trips.txt"] = "\n".join([head, *rows, *copies]) + "\n"
    times = files["stop_times.txt"].splitlines()[1:]
    files["stop_times.txt"] += "".join(f"s-{line}\n" for line in times)
    system = json.loads(build(files)["map.json"])
    assert [r["badge"] for r in system["routes"]] == ["4", "5"]
    assert system["lines"] == [
        {"r": [0, 1], "c": [[-73.0, 42.0], [-73.6, 42.0], [-73.6, 42.6]]}
    ]
    assert {name for *_, name, _ in system["stops"]} >= {"Alpha", "Delta"}
    assert all(owners == [0, 1] for *_, owners in system["stops"])


def test_map_none_leaves_map_data_out():
    files = build(fixture_files("branching"), map="none")
    assert "map.json" not in files
    assert 'class="route-data"' not in files["4/index.html"]
    assert 'class="card system' not in files["index.html"]
    # The accordion still needs the script.
    assert '<script type="module" src="map.js"></script>' in files["index.html"]


def test_vehicles_from_the_catalog_when_up():
    feed = CatalogFeed(
        feedId="f-0123456789",
        name="Test",
        urls={
            "scheduled": ("https://example.org/g.zip",),
            "vehicles": ("https://example.org/vp.pb",),
        },
        roleState={"vehicles": "up"},
    )
    site = Site(slug="test", feed=feed.feed_id, catalog=feed)
    built = build_site(make_zip(fixture_files("branching")), site, today=MONDAY)
    data = route_data(built["4/index.html"].decode())
    assert data["rt"]["url"] == "https://example.org/vp.pb"
    assert data["rt"]["routes"] == ["R"]
    assert json.loads(built["map.json"])["rt"] == "https://example.org/vp.pb"
    down = feed.model_copy(update={"role_state": {"vehicles": "down"}})
    assert Site(slug="test", feed=feed.feed_id, catalog=down).vehicles_url() is None


def test_script_json_cannot_close_the_script():
    assert script_json({"name": "</script>"}) == '{"name":"\\u003c/script>"}'

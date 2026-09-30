"""A tiny synthetic OSM world, so the tests need neither Geofabrik nor real data.

Area requested: a ~800 x 670 m rectangle near Parma. Around it:
- a building inside the area, one 120 m outside (inside the 200 m buffer), one 2 km away
- a road that starts inside and runs 7 km east (osmium must keep it whole)
Two regions: SMALL (a box around the area) and BIG (a much larger box containing SMALL).
"""
from __future__ import annotations

import json
from pathlib import Path

import osmium
import pytest

from noise_worker.settings import Settings

AREA = {"type": "Polygon", "coordinates": [[
    [10.300, 44.800], [10.310, 44.800], [10.310, 44.806], [10.300, 44.806], [10.300, 44.800]]]}


def square(lon: float, lat: float, size: float = 0.0002) -> list[tuple[float, float]]:
    return [(lon, lat), (lon + size, lat), (lon + size, lat + size), (lon, lat + size)]


def write_pbf(path: Path, buildings: list[tuple[float, float]], roads: list[list[tuple[float, float]]]) -> None:
    """Nodes first, then ways, both in ID order: osmium extract requires a sorted file."""
    nodes, ways = [], []
    for lon, lat in buildings:
        ids = []
        for loc in square(lon, lat):
            nodes.append(loc)
            ids.append(len(nodes))
        ways.append((ids + [ids[0]], {"building": "yes"}))
    for line in roads:
        ids = []
        for loc in line:
            nodes.append(loc)
            ids.append(len(nodes))
        ways.append((ids, {"highway": "primary"}))
    path.unlink(missing_ok=True)  # the writer refuses to overwrite
    writer = osmium.SimpleWriter(str(path))
    try:
        for i, loc in enumerate(nodes, start=1):
            writer.add_node(osmium.osm.mutable.Node(id=i, location=loc, version=1))
        for i, (refs, tags) in enumerate(ways, start=1):
            writer.add_way(osmium.osm.mutable.Way(id=i, nodes=refs, tags=tags, version=1))
    finally:
        writer.close()


def write_poly(path: Path, lon1: float, lat1: float, lon2: float, lat2: float) -> None:
    path.write_text(f"{path.stem}\n1\n   {lon1} {lat1}\n   {lon2} {lat1}\n   {lon2} {lat2}\n"
                    f"   {lon1} {lat2}\n   {lon1} {lat1}\nEND\nEND\n", encoding="utf-8")


@pytest.fixture
def osm_dir(tmp_path: Path) -> Path:
    d = tmp_path / "osm"
    d.mkdir()
    buildings = [(10.305, 44.803), (10.3115, 44.803), (10.330, 44.803)]
    roads = [[(10.305, 44.801), (10.400, 44.801)]]
    write_pbf(d / "small-latest.osm.pbf", buildings, roads)
    write_pbf(d / "big-latest.osm.pbf", buildings, roads)
    write_poly(d / "small.poly", 10.25, 44.75, 10.45, 44.85)
    write_poly(d / "big.poly", 9.0, 44.0, 12.0, 46.0)
    registry = {"version": 1, "countries": {
        "SMALL": {"name": "Small", "pbf_file": "small-latest.osm.pbf",
                  "poly_url": "https://download.geofabrik.de/europe/italy/small.poly"},
        "BIG": {"name": "Big", "pbf_file": "big-latest.osm.pbf",
                "poly_url": "https://download.geofabrik.de/europe/big.poly"},
    }}
    (d / "countries.json").write_text(json.dumps(registry), encoding="utf-8")
    return d


@pytest.fixture
def settings(osm_dir: Path) -> Settings:
    return Settings(osm_data_dir=osm_dir, registry_path=osm_dir / "countries.json",
                    buffer_m=200, max_src_dist_m=150, osmium_bin="osmium")
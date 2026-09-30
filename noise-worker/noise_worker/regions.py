"""Pick the master PBF that covers the requested area (registry + Geofabrik .poly)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from shapely.geometry import Polygon
from shapely.ops import unary_union


class RegionError(ValueError):
    pass


@dataclass(frozen=True)
class Region:
    code: str
    name: str
    pbf_path: Path
    boundary: object  # shapely Polygon or MultiPolygon, lon/lat


def parse_poly(text: str):
    """Osmosis .poly format: a name line, then rings ("!" = hole) each closed by END, then END."""
    lines = [line.strip() for line in text.splitlines()]
    outers, holes = [], []
    i = 1  # line 0 is the file name
    while i < len(lines):
        header = lines[i]
        i += 1
        if header == "END":
            break
        if not header:
            continue
        points = []
        while lines[i] != "END":
            if lines[i]:
                lon, lat = lines[i].split()[:2]
                points.append((float(lon), float(lat)))
            i += 1
        i += 1
        (holes if header.startswith("!") else outers).append(Polygon(points))
    if not outers:
        raise RegionError("poly file has no outer ring")
    area = unary_union(outers)
    if holes:
        area = area.difference(unary_union(holes))
    return area


def load_regions(registry_path: Path, data_dir: Path) -> list[Region]:
    """Registry entries whose .poly is on the volume (the updater downloads it with the PBF)."""
    try:
        raw = json.loads(Path(registry_path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RegionError(f"registry not found: {registry_path}")
    regions = []
    for code, entry in raw.get("countries", {}).items():
        poly_url = entry.get("poly_url")
        if not poly_url:
            continue
        poly_path = data_dir / PurePosixPath(urlparse(poly_url).path).name
        if not poly_path.exists():
            continue
        regions.append(Region(
            code=code.upper(),
            name=entry.get("name", code),
            pbf_path=data_dir / entry["pbf_file"],
            boundary=parse_poly(poly_path.read_text(encoding="utf-8")),
        ))
    return regions


def find_region(area, regions: list[Region]) -> Region:
    """The smallest region whose boundary contains the whole (buffered) area."""
    covering = [r for r in regions if r.boundary.contains(area)]
    if not covering:
        partial = [r.code for r in regions if r.boundary.intersects(area)]
        if partial:
            raise RegionError(f"area crosses the boundary of {', '.join(partial)}: no single file covers it")
        raise RegionError("area is outside every region in the registry (lon/lat swapped?)")
    best = min(covering, key=lambda r: r.boundary.area)
    if not best.pbf_path.exists():
        raise RegionError(f"{best.code}: {best.pbf_path.name} not downloaded yet")
    return best
"""Cut the buffered area out of the master PBF with osmium (§4)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import osmium
from shapely.geometry import mapping


class ExtractError(RuntimeError):
    pass


def run_osmium_extract(src: Path, area, out_dir: Path, osmium_bin: str = "osmium") -> Path:
    """Write the area as GeoJSON and run `osmium extract --strategy smart`. Returns the new PBF."""
    out_dir.mkdir(parents=True, exist_ok=True)
    area_file = out_dir / "extract_area.geojson"
    area_file.write_text(json.dumps({"type": "Feature", "properties": {}, "geometry": mapping(area)}),
                         encoding="utf-8")
    out = out_dir / "area.osm.pbf"
    cmd = [osmium_bin, "extract", "--strategy", "smart", "--polygon", str(area_file),
           "--output", str(out), "--overwrite", str(src)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        out.unlink(missing_ok=True)  # never leave a half-written extract behind
        raise ExtractError(f"osmium extract failed (exit {res.returncode}): {res.stderr.strip()}")
    return out


def count_features(pbf: Path) -> dict[str, int]:
    """Quick sanity check on the extract: buildings (ways and multipolygons) and roads."""
    counts = {"buildings": 0, "highways": 0}
    for obj in osmium.FileProcessor(str(pbf), osmium.osm.WAY | osmium.osm.RELATION):
        if "building" in obj.tags:
            counts["buildings"] += 1
        if obj.is_way() and "highway" in obj.tags:
            counts["highways"] += 1
    return counts

def extract_for_job(polygon_geojson, job_dir: Path, settings) -> dict:
    """Phase 2 end to end: validate, buffer, pick the region, extract, count. Returns a summary."""
    from .geometry import buffer_wgs84, check_buffer, parse_polygon
    from .regions import find_region, load_regions

    check_buffer(settings.buffer_m, settings.max_src_dist_m)
    polygon = parse_polygon(polygon_geojson)
    area = buffer_wgs84(polygon, settings.buffer_m)
    region = find_region(area, load_regions(settings.registry_path, settings.osm_data_dir))

    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "input_polygon.geojson").write_text(json.dumps(mapping(polygon)), encoding="utf-8")
    out = run_osmium_extract(region.pbf_path, area, job_dir, settings.osmium_bin)
    counts = count_features(out)
    if counts["highways"] == 0:
        raise ExtractError("extract contains no roads: nothing to compute noise from")

    summary = {"region": region.code, "source_pbf": str(region.pbf_path), "extract": str(out),
               "buffer_m": settings.buffer_m, "counts": counts}
    (job_dir / "extract.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
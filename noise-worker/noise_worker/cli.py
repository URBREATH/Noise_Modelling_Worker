"""Command line: python -m noise_worker {extract,regions}."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from .extract import ExtractError, extract_for_job
from .geometry import PolygonError
from .regions import RegionError, load_regions
from .settings import Settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="noise_worker")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ext = sub.add_parser("extract", help="cut the OSM data for a polygon into a job folder")
    p_ext.add_argument("--polygon", type=Path, required=True, help="GeoJSON Polygon file (lon/lat)")
    p_ext.add_argument("--job-dir", type=Path, required=True)
    p_ext.add_argument("--buffer", type=float, help="override EXTRACT_BUFFER_M (metres)")

    sub.add_parser("regions", help="list the regions available on the OSM volume")

    args = parser.parse_args(argv)
    settings = Settings.from_env()

    if args.command == "regions":
        for r in load_regions(settings.registry_path, settings.osm_data_dir):
            status = "ok" if r.pbf_path.exists() else "PBF missing"
            print(f"{r.code:6} {r.pbf_path.name:32} {status}")
        return 0

    if args.buffer is not None:
        settings = replace(settings, buffer_m=args.buffer)
    try:
        polygon = json.loads(args.polygon.read_text(encoding="utf-8"))
        summary = extract_for_job(polygon, args.job_dir, settings)
    except (PolygonError, RegionError, ValueError) as e:
        print(f"invalid request: {e}", file=sys.stderr)
        return 2
    except ExtractError as e:
        print(f"extract failed: {e}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2))
    return 0
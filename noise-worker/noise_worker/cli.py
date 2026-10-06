"""Command line: python -m noise_worker {extract,regions}."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
import os

from .extract import ExtractError, extract_for_job
from .geometry import PolygonError
from .regions import RegionError, load_regions
from .settings import Settings
import logging                                  
from .nm import PipelineError                    
from .runner import JobInputError, run_job       

def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO,                                   
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")  
    parser = argparse.ArgumentParser(prog="noise_worker")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ext = sub.add_parser("extract", help="cut the OSM data for a polygon into a job folder")
    p_ext.add_argument("--polygon", type=Path, required=True, help="GeoJSON Polygon file (lon/lat)")
    p_ext.add_argument("--job-dir", type=Path, required=True)
    p_ext.add_argument("--buffer", type=float, help="override EXTRACT_BUFFER_M (metres)")

    sub.add_parser("regions", help="list the regions available on the OSM volume")

    p_run = sub.add_parser("run", help="run a full job: extract + NoiseModelling")       
    p_run.add_argument("--polygon", type=Path, required=True, help="GeoJSON Polygon file (lon/lat)")                          
    
    p_run.add_argument("--job-dir", type=Path, required=True)
    sub.add_parser("serve", help="start the HTTP API (POST /jobs, GET /jobs/{id})")   

    args = parser.parse_args(argv)

    args = parser.parse_args(argv)
    settings = Settings.from_env()

    if args.command == "regions":
        for r in load_regions(settings.registry_path, settings.osm_data_dir):
            status = "ok" if r.pbf_path.exists() else "PBF missing"
            print(f"{r.code:6} {r.pbf_path.name:32} {status}")
        return 0

    if args.command == "serve":                                                           
        import uvicorn                                                                   
        uvicorn.run("noise_worker.api:app", host="0.0.0.0",                               
                    port=int(os.environ.get("PORT", "8000")))                             
        return 0                                                                          


    if args.command == "run":                                                          
        try:                                                                           
            polygon = json.loads(args.polygon.read_text(encoding="utf-8"))             
            summary = run_job(polygon, args.job_dir, settings)                         
        except (JobInputError, ValueError) as e:                                       
            print(f"invalid request: {e}", file=sys.stderr)                            
            return 2                                                                   
        except (ExtractError, PipelineError) as e:                                     
            print(f"job failed: {e}", file=sys.stderr)                                 
            return 1                                                                   
        print(json.dumps(summary, indent=2))                                           
        return 0                                                                       



    # from here on: the "extract" command, unchanged
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
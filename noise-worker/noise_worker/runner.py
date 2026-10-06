"""Run one complete noise job: OSM extract (Phase 2) + NoiseModelling (Phase 3)."""
import logging
import time
from dataclasses import replace
from pathlib import Path
from .extract import ExtractError, extract_for_job
from .geometry import PolygonError
from .regions import RegionError, load_regions
from .settings import Settings

# same imports as in cli.py:
# extract_for_job, Settings, PolygonError, RegionError, ExtractError
from .nm import RESULT_NAME, run_pipeline

log = logging.getLogger(__name__)

# Large working files removed after a successful job (GeoJSON and logs are kept)
CLEANUP_GLOBS = ("h2gisdb*.db", "area.osm.pbf")


class JobInputError(Exception):
    """The request itself is wrong (bad polygon, area not covered, ...)."""


def run_job(polygon: dict, job_dir: Path, settings: Settings, params: dict | None = None) -> dict:
    """Run the whole job in job_dir and return a summary with timings."""
    params = params or {}
    src_dist = params.get("confMaxSrcDist")
    if src_dist is not None and src_dist > settings.buffer_m:
        # The extract must contain every source NoiseModelling will look for
        settings = replace(settings, buffer_m=float(src_dist))
        log.info("Extract buffer raised to %.0f m to match confMaxSrcDist", float(src_dist))

    job_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()

    try:
        extract = extract_for_job(polygon, job_dir, settings)
    except (PolygonError, RegionError, ValueError) as e:
        raise JobInputError(str(e)) from e
    t1 = time.monotonic()

    result = run_pipeline(job_dir,params)
    t2 = time.monotonic()

    for pattern in CLEANUP_GLOBS:
        for f in job_dir.glob(pattern):
            f.unlink()

    summary = {
        "extract": extract,
        "params": params,
        "result_file": RESULT_NAME,
        "result_bytes": result.stat().st_size,
        "timings_s": {"extract": round(t1 - t0, 1),
                      "noisemodelling": round(t2 - t1, 1),
                      "total": round(t2 - t0, 1)},
    }
    log.info("Job done in %.1f s (extract %.1f s, NoiseModelling %.1f s)",
             t2 - t0, t1 - t0, t2 - t1)
    return summary
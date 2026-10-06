"""Run the NoiseModelling Groovy pipeline (Phase 3) as a subprocess."""
import logging
import os
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

NM_HOME = Path(os.environ.get("NM_HOME", "/opt/noisemodelling"))
NM_SCRIPT = os.environ.get("NM_SCRIPT", "urbreath_pipeline.groovy")
NM_TIMEOUT_S = int(os.environ.get("NM_TIMEOUT_S", "3600"))
RESULT_NAME = "lden_isocontours.geojson"
NM_LOG_NAME = "nm.log"


class PipelineError(RuntimeError):
    """NoiseModelling failed or produced no result."""


def run_pipeline(job_dir: Path, params: dict | None = None) -> Path:
    """Run the pipeline on job_dir and return the path of the GeoJSON result."""
    job_dir = job_dir.resolve()
    result = job_dir / RESULT_NAME
    result.unlink(missing_ok=True)  # never mistake an old result for a new one

    cmd = [str(NM_HOME / "bin" / "wps_scripts"),
           "-w", str(job_dir), "-s", NM_SCRIPT, "-jobDir", str(job_dir)]
    for key, value in (params or {}).items():
        text = str(value).lower() if isinstance(value, bool) else str(value)
        cmd += [f"-{key}", text]
    log.info("Running NoiseModelling: %s", " ".join(cmd))

    start = time.monotonic()
    with (job_dir / NM_LOG_NAME).open("w", encoding="utf-8") as out:
        try:
            proc = subprocess.run(cmd, cwd=NM_HOME, stdout=out,
                                  stderr=subprocess.STDOUT, timeout=NM_TIMEOUT_S)
        except subprocess.TimeoutExpired as exc:
            raise PipelineError(f"NoiseModelling timed out after {NM_TIMEOUT_S} s") from exc
    elapsed = time.monotonic() - start

    if proc.returncode != 0:
        raise PipelineError(f"NoiseModelling exited with code {proc.returncode}, see {NM_LOG_NAME}")
    if not result.is_file() or result.stat().st_size == 0:
        raise PipelineError(f"NoiseModelling produced no {RESULT_NAME}, see {NM_LOG_NAME}")

    log.info("NoiseModelling done in %.1f s, result %d bytes", elapsed, result.stat().st_size)
    return result
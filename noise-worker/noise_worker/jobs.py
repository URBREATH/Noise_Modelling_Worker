"""Job store on disk (one folder per job) and single-consumer FIFO queue."""
import json
import logging
import os
import queue
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .runner import JobInputError, run_job

log = logging.getLogger(__name__)

STATUS_NAME = "status.json"
REQUEST_NAME = "request.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobStore:
    """Job state lives only in jobs/<id>/status.json: no database, no memory."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def job_dir(self, job_id: str) -> Path:
        return self.root / job_id

    def read(self, job_id: str) -> dict | None:
        try:
            return json.loads((self.job_dir(job_id) / STATUS_NAME).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None

    def write(self, job_id: str, status: dict) -> None:
        path = self.job_dir(job_id) / STATUS_NAME
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(status, indent=2), encoding="utf-8")
        os.replace(tmp, path)  # atomic: a reader never sees a half-written file

    def update(self, job_id: str, **fields) -> None:
        status = self.read(job_id)
        status.update(fields)
        self.write(job_id, status)

    def create(self, request: dict) -> str:
        job_id = uuid.uuid4().hex
        self.job_dir(job_id).mkdir()
        (self.job_dir(job_id) / REQUEST_NAME).write_text(json.dumps(request), encoding="utf-8")
        self.write(job_id, {"id": job_id, "status": "queued", "created_at": _now()})
        return job_id

    def fail_interrupted(self) -> None:
        """At startup, jobs left queued/running by a previous process can never finish."""
        for path in self.root.glob(f"*/{STATUS_NAME}"):
            job_id = path.parent.name
            status = self.read(job_id)
            if status and status["status"] in ("queued", "running"):
                self.update(job_id, status="failed", finished_at=_now(),
                            error={"type": "internal", "message": "worker restarted"})
                log.warning("Job %s marked failed: worker restarted", job_id)


class JobQueue:
    """FIFO queue with ONE consumer thread: one NoiseModelling run at a time."""

    def __init__(self, store: JobStore, settings):
        self.store = store
        self.settings = settings
        self._queue: queue.Queue[str] = queue.Queue()
        self._thread = threading.Thread(target=self._consume, name="job-consumer", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def submit(self, request: dict) -> str:
        job_id = self.store.create(request)
        self._queue.put(job_id)
        log.info("Job %s queued (%d waiting)", job_id, self._queue.qsize())
        return job_id

    def join(self) -> None:
        """Wait until every submitted job is finished (used by tests)."""
        self._queue.join()

    def _consume(self) -> None:
        while True:
            job_id = self._queue.get()
            try:
                self._run_one(job_id)
            except Exception:
                log.exception("Unexpected error while handling job %s", job_id)
            finally:
                self._queue.task_done()

    def _run_one(self, job_id: str) -> None:
        job_dir = self.store.job_dir(job_id)
        request = json.loads((job_dir / REQUEST_NAME).read_text(encoding="utf-8"))
        self.store.update(job_id, status="running", started_at=_now())
        log.info("Job %s started", job_id)
        try:
            summary = run_job(request["polygon"], job_dir, self.settings, request.get("params"))
        except JobInputError as e:
            self.store.update(job_id, status="failed", finished_at=_now(),
                              error={"type": "input", "message": str(e)})
            log.info("Job %s rejected: %s", job_id, e)
        except Exception as e:
            self.store.update(job_id, status="failed", finished_at=_now(),
                              error={"type": "internal", "message": str(e)})
            log.exception("Job %s failed", job_id)
        else:
            self.store.update(job_id, status="done", finished_at=_now(), summary=summary)
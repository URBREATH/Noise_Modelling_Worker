"""HTTP API of the noise worker: POST /jobs and GET /jobs/{id} (design doc §7.2)."""
import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from .params import  validate_params
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from .jobs import JobQueue, JobStore
from .nm import RESULT_NAME
from .runner import Settings

JOBS_DIR = Path(os.environ.get("JOBS_DIR", "/jobs"))
JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class JobRequest(BaseModel):
    polygon: dict
    mode: Literal["preset", "custom"] = "preset"
    params: dict[str, Any] = Field(default_factory=dict)


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = JobStore(JOBS_DIR)
    store.fail_interrupted()
    job_queue = JobQueue(store, Settings.from_env())
    job_queue.start()
    app.state.store = store
    app.state.queue = job_queue
    yield


app = FastAPI(title="noise worker", lifespan=lifespan)


@app.post("/jobs", status_code=202)
def create_job(body: JobRequest, request: Request) -> dict:
    if body.mode == "preset" and body.params:
        raise HTTPException(400, "params are only allowed in custom mode")
    job= body.model_dump()
    if body.mode == "custom":
        try:
            job["params"] = validate_params(body.params)
        except ValueError as e:
            raise HTTPException(400, str(e))
    job_id = request.app.state.queue.submit(job)
    return {"id": job_id, "status": "queued"}


@app.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request) -> dict:
    store: JobStore = request.app.state.store
    if not JOB_ID_RE.match(job_id):  # also blocks paths like ../../etc
        raise HTTPException(404, "job not found")
    status = store.read(job_id)
    if status is None:
        raise HTTPException(404, "job not found")
    if status["status"] == "done":
        result_path = store.job_dir(job_id) / RESULT_NAME
        status["result"] = json.loads(result_path.read_text(encoding="utf-8"))
    return status

@app.get("/health")                                                             
def health(request: Request) -> dict:                                           
    """Liveness check for Docker: the API answers and the queue thread is alive."""
    job_queue: JobQueue = request.app.state.queue                               
    if not job_queue._thread.is_alive():                                       
        raise HTTPException(503, "job consumer thread is not running")          
    return {"status": "ok", "waiting": job_queue._queue.qsize()}                
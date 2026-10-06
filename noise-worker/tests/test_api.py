import json

import pytest
from fastapi.testclient import TestClient

from noise_worker import api, jobs
from noise_worker.jobs import JobStore
from noise_worker.runner import JobInputError

POLYGON = {"type": "Polygon", "coordinates": [[[7.42, 43.73], [7.43, 43.73],
                                               [7.43, 43.74], [7.42, 43.73]]]}
FAKE_RESULT = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": None,
                                                          "properties": {"ISOLABEL": "55-60"}}]}


@pytest.fixture
def client(tmp_path, monkeypatch):
    calls = []

    def fake_run_job(polygon, job_dir, settings, params=None):
        """Stand-in for extract + NoiseModelling: instant and predictable."""
        calls.append(params)
        if polygon.get("type") != "Polygon":
            raise JobInputError("expected a GeoJSON Polygon")
        (job_dir / "lden_isocontours.geojson").write_text(json.dumps(FAKE_RESULT), encoding="utf-8")
        return {"params": params or {}}

    monkeypatch.setattr(api, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(jobs, "run_job", fake_run_job)
    with TestClient(api.app) as c:  # "with" runs the lifespan (queue thread)
        c.calls = calls
        yield c


def wait_all(client):
    client.app.state.queue.join()


def test_preset_job_done_with_result(client):
    r = client.post("/jobs", json={"polygon": POLYGON})
    assert r.status_code == 202
    job_id = r.json()["id"]
    wait_all(client)
    body = client.get(f"/jobs/{job_id}").json()
    assert body["status"] == "done"
    assert body["result"] == FAKE_RESULT
    assert "finished_at" in body


def test_bad_polygon_fails_as_input_error(client):
    job_id = client.post("/jobs", json={"polygon": {"type": "Point", "coordinates": [7.4, 43.7]}}).json()["id"]
    wait_all(client)
    body = client.get(f"/jobs/{job_id}").json()
    assert body["status"] == "failed"
    assert body["error"]["type"] == "input"
    assert "result" not in body


def test_custom_params_reach_the_job(client):
    r = client.post("/jobs", json={"polygon": POLYGON, "mode": "custom",
                                   "params": {"confMaxSrcDist": 300, "confReflOrder": 0}})
    assert r.status_code == 202
    wait_all(client)
    assert client.calls == [{"confMaxSrcDist": 300.0, "confReflOrder": 0}]


@pytest.mark.parametrize("payload", [
    {"polygon": POLYGON, "mode": "preset", "params": {"confReflOrder": 1}},
    {"polygon": POLYGON, "mode": "custom", "params": {"confReflOrder": 9}},
    {"polygon": POLYGON, "mode": "custom", "params": {"foo": 1}},
    {"polygon": POLYGON, "mode": "turbo"},
])
def test_bad_requests_are_rejected_without_queueing(client, payload):
    r = client.post("/jobs", json=payload)
    assert r.status_code in (400, 422)
    assert client.calls == []


@pytest.mark.parametrize("job_id", ["0" * 32, "not-a-job-id", "ABCDEF" * 5 + "AB"])
def test_unknown_or_malformed_id_is_404(client, job_id):
    assert client.get(f"/jobs/{job_id}").status_code == 404


def test_interrupted_jobs_are_marked_failed(tmp_path):
    store = JobStore(tmp_path)
    job_id = store.create({"polygon": POLYGON})
    store.update(job_id, status="running")
    JobStore(tmp_path).fail_interrupted()  # what happens at the next startup
    status = store.read(job_id)
    assert status["status"] == "failed"
    assert status["error"] == {"type": "internal", "message": "worker restarted"}
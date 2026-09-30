"""Test fixtures: a tiny synthetic PBF and a local fake Geofabrik replication server.

No real country extract is ever downloaded: the server speaks the same
state.txt / NNN/NNN/NNN.osc.gz layout that pyosmium-up-to-date consumes.
"""
from __future__ import annotations

import datetime as dt
import functools
import gzip
import hashlib
import json
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import osmium
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from osm_updater.config import Settings  # noqa: E402

NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(ts: dt.datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def write_pbf(path: Path, node_ids, *, url=None, seq=None, ts=None) -> None:
    header = osmium.io.Header()
    if url is not None:
        header.set("osmosis_replication_base_url", url)
        header.set("osmosis_replication_sequence_number", str(seq))
        header.set("osmosis_replication_timestamp", iso(ts))
    w = osmium.SimpleWriter(str(path), header=header, overwrite=True)
    try:
        for nid in node_ids:
            w.add_node(osmium.osm.mutable.Node(
                id=nid, version=1, changeset=1, uid=1, user="t",
                timestamp=ts or NOW, location=(9.0 + nid / 1000, 45.0)))
    finally:
        w.close()


def node_ids(path: Path) -> list[int]:
    return sorted(n.id for n in osmium.FileProcessor(str(path), osmium.osm.NODE))


class ReplicationServer:
    """Serves <root>/updates/ as a replication feed and <root>/ for bootstrap files."""

    def __init__(self, root: Path, port: int):
        self.root = root
        self.base = f"http://127.0.0.1:{port}"
        self.updates_url = f"{self.base}/updates/"
        self.updates = root / "updates"
        self.updates.mkdir(parents=True, exist_ok=True)
        self.seq_times: dict[int, dt.datetime] = {}

    def _state(self, seq: int, ts: dt.datetime) -> str:
        return f"#fake\nsequenceNumber={seq}\ntimestamp={iso(ts).replace(':', chr(92) + ':')}\n"

    def publish(self, seq: int, ts: dt.datetime, new_node_id: int | None = None) -> None:
        """Publish diff number seq (creating one node) and move state.txt to it."""
        d = self.updates / f"{seq // 1000000:03d}" / f"{(seq // 1000) % 1000:03d}"
        d.mkdir(parents=True, exist_ok=True)
        stem = d / f"{seq % 1000:03d}"
        body = ""
        if new_node_id is not None:
            body = (f'<create><node id="{new_node_id}" version="1" timestamp="{iso(ts)}" '
                    f'lat="45.1" lon="9.1" changeset="2" uid="1" user="t"/></create>')
        stem.with_suffix(".osc.gz").write_bytes(gzip.compress(
            f'<?xml version="1.0"?><osmChange version="0.6">{body}</osmChange>'.encode()))
        stem.with_suffix(".state.txt").write_text(self._state(seq, ts))
        (self.updates / "state.txt").write_text(self._state(seq, ts))
        self.seq_times[seq] = ts


@pytest.fixture
def server(tmp_path):
    root = tmp_path / "www"
    root.mkdir()
    handler = functools.partial(_QuietHandler, directory=str(root))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield ReplicationServer(root, httpd.server_address[1])
    httpd.shutdown()


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    return d


def make_registry(path: Path, server: ReplicationServer, codes=("MC",)) -> Path:
    reg = {"version": 1, "countries": {}}
    for code in codes:
        slug = code.lower()
        reg["countries"][code] = {
            "name": code,
            "pbf_file": f"{slug}-latest.osm.pbf",
            "download_url": f"{server.base}/{slug}-latest.osm.pbf",
            "md5_url": f"{server.base}/{slug}-latest.osm.pbf.md5",
            "updates_url": server.updates_url,
            "poly_url": f"{server.base}/{slug}.poly",
        }
    path.write_text(json.dumps(reg))
    return path


def publish_extract(server: ReplicationServer, code: str, seq: int, ts: dt.datetime,
                    nodes=(1, 2, 3), bad_md5: bool = False) -> Path:
    """Put a Geofabrik-like <code>-latest.osm.pbf (+ .md5, .poly) on the fake server."""
    slug = code.lower()
    pbf = server.root / f"{slug}-latest.osm.pbf"
    write_pbf(pbf, nodes, url=server.updates_url, seq=seq, ts=ts)
    digest = hashlib.md5(pbf.read_bytes()).hexdigest()
    if bad_md5:
        digest = "0" * 32
    (server.root / f"{slug}-latest.osm.pbf.md5").write_text(f"{digest}  {pbf.name}\n")
    (server.root / f"{slug}.poly").write_text(f"{slug}\n1\n 9.0 45.0\n 9.2 45.0\n 9.2 45.2\nEND\nEND\n")
    return pbf


@pytest.fixture
def make_settings(data_dir, tmp_path):
    def _make(registry: Path, **overrides) -> Settings:
        base = dict(
            data_dir=data_dir, registry_path=data_dir / "countries.json",
            default_registry=registry, update_time_utc="22:30", run_on_start=True,
            auto_bootstrap=True, diff_size_mb=1024, max_update_iterations=10,
            stale_warn_days=7, stale_crit_days=60, rebootstrap_after_days=85,
            auto_rebootstrap=True, alert_webhook_url=None, alert_command=None,
            health_max_hours=36, disk_margin=1.2, http_timeout=10,
        )
        base.update(overrides)
        return Settings(**base)
    return _make


@pytest.fixture(autouse=True)
def no_retry_wait(monkeypatch):
    from osm_updater import bootstrap
    monkeypatch.setattr(bootstrap, "RETRY_WAIT_S", 0)

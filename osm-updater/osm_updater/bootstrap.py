"""Initial full download of a country's master PBF (Phase 1 bootstrap).

Download goes to a temp file next to the master, is checked against Geofabrik's
.md5 and against the PBF header, and only then atomically renamed into place.
"""
from __future__ import annotations

import hashlib
import logging
import urllib.request
import time
from pathlib import Path

from .config import Settings
from .fsutil import atomic_replace, remove_quietly, tmp_path_for
from .pbf import read_replication_info
from .registry import Country
from .state import State, now_iso

log = logging.getLogger(__name__)

CHUNK = 1024 * 1024
USER_AGENT = "urbreath-osm-updater/0.1"
ATTEMPTS = 3
RETRY_WAIT_S = 5  # seconds


class BootstrapError(RuntimeError):
    pass


def _open(url: str, timeout: int):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(req, timeout=timeout)


def download(url: str, dest: Path, timeout: int) -> str:
    """Stream url into dest, return its md5 hex digest."""
    md5 = hashlib.md5()
    with _open(url, timeout) as resp, open(dest, "wb") as out:
        while chunk := resp.read(CHUNK):
            md5.update(chunk)
            out.write(chunk)
    return md5.hexdigest()


def fetch_md5(url: str, timeout: int) -> str:
    with _open(url, timeout) as resp:
        text = resp.read().decode("ascii", errors="replace").strip()
    digest = text.split()[0].lower() if text else ""
    if len(digest) != 32:
        raise BootstrapError(f"unexpected md5 file content at {url!r}")
    return digest

def _retry(what: str, fn, *args):
    """Geofabrik occasionally times out or returns a broken response: retry a few times."""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return fn(*args)
        except BootstrapError:
            raise
        except Exception as e:
            if attempt == ATTEMPTS:
                raise
            log.warning("%s failed (attempt %d/%d): %s; retrying in %ds",
                        what, attempt, ATTEMPTS, e, RETRY_WAIT_S)
            time.sleep(RETRY_WAIT_S)

def bootstrap_country(country: Country, settings: Settings, state: State, force: bool = False) -> bool:
    """Download the master PBF if missing (or if force). Returns True if a new file was installed."""
    data_dir = settings.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    target = country.pbf_path(data_dir)

    if target.exists() and not force:
        log.info("%s: master already present at %s, skipping bootstrap", country.code, target)
        return False

    tmp = tmp_path_for(target)
    log.info("%s: downloading %s", country.code, country.download_url)
    try:
                # Fetch the small .md5 first: if Geofabrik is unreachable we fail
        # before spending minutes on the big download.
        expected = None
        if country.md5_url:
            expected = _retry("md5 fetch", fetch_md5, country.md5_url, settings.http_timeout)
        digest = _retry("download", download, country.download_url, tmp, settings.http_timeout)
        if expected is not None and digest != expected:
            # Geofabrik may have published a new extract during our download.
            expected = _retry("md5 fetch", fetch_md5, country.md5_url, settings.http_timeout)
            if digest != expected:
                raise BootstrapError(f"md5 mismatch: got {digest}, expected {expected}")
        try:
            info = read_replication_info(tmp)
        except RuntimeError as e:
            raise BootstrapError(f"downloaded file is not a readable PBF: {e}")
        if info.sequence is None:
            log.warning("%s: downloaded PBF has no replication headers; "
                        "updates will fall back to %s", country.code, country.updates_url)
        atomic_replace(tmp, target)
    except Exception as e:
        remove_quietly(tmp)
        state.record(country.code, last_error=f"bootstrap: {e}", last_error_at=now_iso())
        raise BootstrapError(f"{country.code}: bootstrap failed: {e}") from e

    _download_poly(country, settings)

    size = target.stat().st_size
    log.info("%s: installed %s (%.1f MB, sequence %s, data %s)", country.code, target,
             size / 1e6, info.sequence, info.timestamp)
    state.record(country.code, bootstrapped_at=now_iso(), sequence=info.sequence,
                 data_timestamp=info.timestamp.isoformat() if info.timestamp else None,
                 size_bytes=size, last_error=None)
    return True


def _download_poly(country: Country, settings: Settings) -> None:
    poly = country.poly_path(settings.data_dir)
    if poly is None:
        return
    tmp = tmp_path_for(poly)
    try:
        _retry("boundary download", download, country.poly_url, tmp, settings.http_timeout)        
        atomic_replace(tmp, poly)
    except Exception as e:  # the boundary is a convenience for Phase 2, never fatal here
        remove_quietly(tmp)
        log.warning("%s: could not download boundary %s: %s", country.code, country.poly_url, e)
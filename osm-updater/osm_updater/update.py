"""Incremental update of a master PBF with pyosmium-up-to-date (§3.2, §3.3).

pyosmium-up-to-date returns 0 when the file is fully up to date, 1 when diffs
were applied but more remain (size cap, or a network error it gave up on), and
>1 on any error. We always write to a temp file with -o and atomically rename it
over the master, then call again while the exit code is 1.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .bootstrap import bootstrap_country
from .config import Settings
from .fsutil import atomic_replace, has_free_space, remove_quietly, tmp_path_for
from .pbf import read_replication_info, same_url
from .registry import Country
from .state import State, now_iso

log = logging.getLogger(__name__)

PYOSMIUM_UP_TO_DATE = "pyosmium-up-to-date"

UP_TO_DATE = "up_to_date"
UPDATED = "updated"
BEHIND = "behind"            # progress made, iteration cap hit; next cycle continues
REBOOTSTRAPPED = "rebootstrapped"
MISSING = "missing"
FAILED = "failed"


@dataclass
class UpdateResult:
    code: str
    status: str
    sequence_before: int | None = None
    sequence_after: int | None = None
    iterations: int = 0
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status not in (FAILED, MISSING)


Runner = Callable[[list[str]], subprocess.CompletedProcess]


def default_runner(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def _tail(text: str | None, lines: int = 15) -> str:
    return "\n".join((text or "").strip().splitlines()[-lines:])


def build_command(target: Path, tmp: Path, country: Country, header_url: str | None,
                  settings: Settings) -> list[str]:
    cmd = [PYOSMIUM_UP_TO_DATE, "-v", "-o", str(tmp), "--size", str(settings.diff_size_mb), str(target)]
    # Geofabrik files carry the replication URL in their header; pyosmium refuses a
    # different --server, so only pass ours when the header has none.
    if header_url is None:
        cmd[1:1] = ["--server", country.updates_url]
    return cmd


def update_country(country: Country, settings: Settings, state: State,
                   runner: Runner = default_runner) -> UpdateResult:
    target = country.pbf_path(settings.data_dir)
    if not target.exists():
        return UpdateResult(country.code, MISSING, message=f"master PBF not found: {target}")

    try:
        info = read_replication_info(target)
    except RuntimeError as e:
        return _fail(state, UpdateResult(country.code, FAILED, message=f"cannot read {target}: {e}"))

    result = UpdateResult(country.code, UP_TO_DATE, sequence_before=info.sequence,
                          sequence_after=info.sequence)
    age = info.age_days()

    if age is not None and age > settings.rebootstrap_after_days:
        msg = (f"data is {age:.0f} days old, beyond the incremental update window "
               f"({settings.rebootstrap_after_days} days)")
        if not settings.auto_rebootstrap:
            return _fail(state, UpdateResult(country.code, FAILED,
                                             message=msg + "; run bootstrap --force"))
        log.warning("%s: %s, downloading a fresh copy", country.code, msg)
        try:
            bootstrap_country(country, settings, state, force=True)
        except Exception as e:
            return _fail(state, UpdateResult(country.code, FAILED, message=str(e)))
        new = read_replication_info(target)
        state.record(country.code, last_success_at=now_iso())
        return UpdateResult(country.code, REBOOTSTRAPPED, info.sequence, new.sequence, message=msg)

    if info.base_url and not same_url(info.base_url, country.updates_url):
        log.warning("%s: PBF header points to %s, registry says %s; following the header",
                    country.code, info.base_url, country.updates_url)

    for i in range(1, settings.max_update_iterations + 1):
        needed = int(target.stat().st_size * settings.disk_margin)
        if not has_free_space(settings.data_dir, needed):
            return _fail(state, _with(result, FAILED,
                                      f"not enough free disk space ({needed / 1e6:.0f} MB needed)"))

        tmp = tmp_path_for(target)
        remove_quietly(tmp)
        cmd = build_command(target, tmp, country, info.base_url, settings)
        log.info("%s: iteration %d: %s", country.code, i, " ".join(cmd))
        proc = runner(cmd)
        result.iterations = i

        if proc.returncode not in (0, 1):
            remove_quietly(tmp)
            return _fail(state, _with(result, FAILED,
                                      f"pyosmium-up-to-date exit {proc.returncode}: {_tail(proc.stderr)}"))

        if tmp.exists():
            try:
                new = read_replication_info(tmp)
            except RuntimeError as e:
                remove_quietly(tmp)
                return _fail(state, _with(result, FAILED, f"updated file unreadable: {e}"))
            atomic_replace(tmp, target)
            progressed = new.sequence != info.sequence
            log.info("%s: sequence %s -> %s (data %s)", country.code, info.sequence,
                     new.sequence, new.timestamp)
            info = new
            result.sequence_after = new.sequence
            if progressed:
                result.status = UPDATED
        else:
            progressed = False

        state.record(country.code, sequence=info.sequence,
                     data_timestamp=info.timestamp.isoformat() if info.timestamp else None,
                     size_bytes=target.stat().st_size)

        if proc.returncode == 0:
            state.record(country.code, last_success_at=now_iso(), last_error=None)
            return result
        if not progressed:
            # exit 1 without progress: the server has data but we could not fetch it.
            return _fail(state, _with(result, FAILED,
                                      f"no progress while server reports more data: {_tail(proc.stderr)}"))

    state.record(country.code, last_success_at=now_iso(), last_error=None)
    return _with(result, BEHIND, f"stopped after {settings.max_update_iterations} iterations, "
                                 "more diffs remain")


def _with(result: UpdateResult, status: str, message: str) -> UpdateResult:
    result.status = status
    result.message = message
    return result


def _fail(state: State, result: UpdateResult) -> UpdateResult:
    log.error("%s: %s", result.code, result.message)
    state.record(result.code, last_error=result.message, last_error_at=now_iso())
    return result
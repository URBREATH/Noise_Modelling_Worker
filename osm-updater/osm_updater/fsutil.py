"""Atomic file replacement and locking (§3.3).

Readers that already opened the old master keep a valid descriptor on the old
inode after os.replace (POSIX semantics), so nobody ever reads a half-written file.
The temp file must live in the same directory (same filesystem) as the target.
"""
from __future__ import annotations

import contextlib
import fcntl
import os
import shutil
import time
from pathlib import Path

TMP_PREFIX = ".tmp-"


def tmp_path_for(target: Path) -> Path:
    # Keep the original suffix: pyosmium/osmium pick the output format from it.
    return target.with_name(f"{TMP_PREFIX}{os.getpid()}.{target.name}")


def fsync_file(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_replace(tmp: Path, target: Path) -> None:
    """Flush tmp to disk, then atomically point target's name at it."""
    fsync_file(tmp)
    os.replace(tmp, target)
    fsync_dir(target.parent)


def remove_quietly(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        path.unlink()


def cleanup_stale_tmp(data_dir: Path, older_than_s: int = 3600) -> list[Path]:
    """Remove temp files left behind by a crashed run."""
    removed = []
    now = time.time()
    for p in data_dir.glob(f"{TMP_PREFIX}*"):
        try:
            if now - p.stat().st_mtime > older_than_s:
                p.unlink()
                removed.append(p)
        except FileNotFoundError:
            pass
    return removed


def has_free_space(directory: Path, needed_bytes: int) -> bool:
    return shutil.disk_usage(directory).free >= needed_bytes


class LockBusy(RuntimeError):
    pass


@contextlib.contextmanager
def exclusive_lock(lock_path: Path):
    """Non-blocking process lock so two updater runs never touch the same files."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LockBusy(f"another osm-updater run holds {lock_path}")
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        yield
    finally:
        os.close(fd)
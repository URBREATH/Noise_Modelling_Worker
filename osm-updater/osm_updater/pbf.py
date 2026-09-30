"""Reading replication metadata from a PBF header."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from osmium.replication.utils import get_replication_header


@dataclass(frozen=True)
class ReplicationInfo:
    base_url: str | None
    sequence: int | None
    timestamp: dt.datetime | None

    def age_days(self, now: dt.datetime | None = None) -> float | None:
        if self.timestamp is None:
            return None
        now = now or dt.datetime.now(dt.timezone.utc)
        return (now - self.timestamp).total_seconds() / 86400


def read_replication_info(path: Path) -> ReplicationInfo:
    """Raises RuntimeError if the file is not a readable OSM file."""
    url, seq, ts = get_replication_header(str(path))
    return ReplicationInfo(base_url=url, sequence=seq, timestamp=ts)


def same_url(a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return False
    return a.rstrip("/") == b.rstrip("/")
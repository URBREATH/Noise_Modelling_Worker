"""Small JSON state file on the shared volume: last run and last result per country."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from .fsutil import atomic_replace, tmp_path_for


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def parse_iso(value: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(value) if value else None


class State:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = {"countries": {}, "last_cycle": None}
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass  # corrupt state is not fatal: it only feeds status and health
        self.data.setdefault("countries", {})

    def country(self, code: str) -> dict[str, Any]:
        return self.data["countries"].setdefault(code, {})

    def record(self, code: str, **fields: Any) -> None:
        self.country(code).update(fields)
        self.save()

    def mark_cycle(self, ok: bool) -> None:
        self.data["last_cycle"] = {"finished_at": now_iso(), "ok": ok}
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = tmp_path_for(self.path)
        tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True), encoding="utf-8")
        atomic_replace(tmp, self.path)
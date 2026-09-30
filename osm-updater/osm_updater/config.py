"""Runtime settings, read from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    val = os.environ.get(name)
    return int(val) if val not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    # Shared volume holding the master PBFs, the registry and the state file.
    data_dir: Path
    # Registry path; if missing on the volume, bootstrap seeds it from default_registry.
    registry_path: Path
    default_registry: Path
    # Daily run time (UTC, HH:MM). Geofabrik publishes around 21:00 CET.
    update_time_utc: str
    run_on_start: bool
    # Bootstrap countries whose master PBF is missing at the start of each cycle.
    auto_bootstrap: bool
    # Max diff size per pyosmium-up-to-date call, in MB (its own default is 1024).
    diff_size_mb: int
    # Safety cap on pyosmium-up-to-date iterations per country per cycle.
    max_update_iterations: int
    # Staleness thresholds on the age of the data (replication timestamp in the PBF header).
    stale_warn_days: int
    stale_crit_days: int
    # Above this data age the file can no longer be updated incrementally
    # (pyosmium refuses files > 90 days, Geofabrik keeps diffs 100 days):
    # download it again from scratch if auto_rebootstrap is on.
    rebootstrap_after_days: int
    auto_rebootstrap: bool
    # Alert hooks (both optional, both used if set).
    alert_webhook_url: str | None
    alert_command: str | None
    # Healthcheck fails if the last completed cycle is older than this.
    health_max_hours: int
    # Required free disk space as a multiple of the file being rewritten.
    disk_margin: float
    http_timeout: int

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.environ.get("OSM_DATA_DIR", "/data/osm"))
        pkg_default = Path(__file__).resolve().parent.parent / "config" / "countries.json"
        return cls(
            data_dir=data_dir,
            registry_path=Path(os.environ.get("OSM_REGISTRY", str(data_dir / "countries.json"))),
            default_registry=Path(os.environ.get("OSM_DEFAULT_REGISTRY", str(pkg_default))),
            update_time_utc=os.environ.get("OSM_UPDATE_TIME_UTC", "22:30"),
            run_on_start=_env_bool("OSM_RUN_ON_START", True),
            auto_bootstrap=_env_bool("OSM_AUTO_BOOTSTRAP", True),
            diff_size_mb=_env_int("OSM_DIFF_SIZE_MB", 1024),
            max_update_iterations=_env_int("OSM_MAX_UPDATE_ITERATIONS", 50),
            stale_warn_days=_env_int("OSM_STALE_WARN_DAYS", 7),
            stale_crit_days=_env_int("OSM_STALE_CRIT_DAYS", 60),
            rebootstrap_after_days=_env_int("OSM_REBOOTSTRAP_AFTER_DAYS", 85),
            auto_rebootstrap=_env_bool("OSM_AUTO_REBOOTSTRAP", True),
            alert_webhook_url=os.environ.get("OSM_ALERT_WEBHOOK_URL") or None,
            alert_command=os.environ.get("OSM_ALERT_COMMAND") or None,
            health_max_hours=_env_int("OSM_HEALTH_MAX_HOURS", 36),
            disk_margin=float(os.environ.get("OSM_DISK_MARGIN", "1.2")),
            http_timeout=_env_int("OSM_HTTP_TIMEOUT", 120),
        )

    @property
    def state_path(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def lock_path(self) -> Path:
        return self.data_dir / ".osm-updater.lock"
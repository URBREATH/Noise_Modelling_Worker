"""Staleness check and alert hook (§3.2: Geofabrik keeps diffs only 100 days)."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import urllib.request
from dataclasses import asdict, dataclass

from .config import Settings
from .pbf import read_replication_info
from .registry import Country

log = logging.getLogger(__name__)

WARNING = "warning"
CRITICAL = "critical"


@dataclass
class Alert:
    level: str
    country: str
    message: str
    data_age_days: float | None = None

    def to_json(self) -> str:
        return json.dumps({"source": "osm-updater", **asdict(self)})


def check_staleness(countries: list[Country], settings: Settings) -> list[Alert]:
    alerts = []
    for c in countries:
        path = c.pbf_path(settings.data_dir)
        if not path.exists():
            alerts.append(Alert(CRITICAL, c.code, f"master PBF missing: {path}"))
            continue
        try:
            age = read_replication_info(path).age_days()
        except RuntimeError as e:
            alerts.append(Alert(CRITICAL, c.code, f"master PBF unreadable: {e}"))
            continue
        if age is None:
            alerts.append(Alert(WARNING, c.code, "master PBF has no data timestamp"))
        elif age >= settings.stale_crit_days:
            alerts.append(Alert(CRITICAL, c.code,
                                f"data is {age:.1f} days old; incremental updates stop working "
                                f"around 90-100 days, a full re-download is then needed", round(age, 1)))
        elif age >= settings.stale_warn_days:
            alerts.append(Alert(WARNING, c.code, f"data is {age:.1f} days old", round(age, 1)))
    return alerts


def send_alert(alert: Alert, settings: Settings) -> None:
    """Log the alert and forward it to the webhook and/or command if configured.
    A failing hook is logged and never breaks the update cycle."""
    logger = log.critical if alert.level == CRITICAL else log.warning
    logger("ALERT [%s] %s: %s", alert.level, alert.country, alert.message)

    if settings.alert_webhook_url:
        try:
            req = urllib.request.Request(
                settings.alert_webhook_url, data=alert.to_json().encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=30).close()
        except Exception as e:
            log.error("alert webhook failed: %s", e)

    if settings.alert_command:
        env = {**os.environ, "OSM_ALERT_LEVEL": alert.level, "OSM_ALERT_COUNTRY": alert.country,
               "OSM_ALERT_MESSAGE": alert.message, "OSM_ALERT_JSON": alert.to_json()}
        try:
            subprocess.run(settings.alert_command, shell=True, env=env, timeout=60, check=True)
        except Exception as e:
            log.error("alert command failed: %s", e)
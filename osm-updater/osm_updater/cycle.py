"""One full updater cycle: bootstrap what is missing, update the rest, check staleness."""
from __future__ import annotations

import logging

from .alerts import CRITICAL, Alert, check_staleness, send_alert
from .bootstrap import bootstrap_country
from .config import Settings
from .fsutil import cleanup_stale_tmp, exclusive_lock
from .registry import ensure_registry, load_registry, select
from .state import State
from .update import MISSING, Runner, UpdateResult, default_runner, update_country

log = logging.getLogger(__name__)


def run_cycle(settings: Settings, codes: list[str] | None = None,
              runner: Runner = default_runner) -> bool:
    """Returns True if every selected country ended the cycle usable and fresh."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(settings.lock_path):
        if ensure_registry(settings.registry_path, settings.default_registry):
            log.info("seeded registry at %s", settings.registry_path)
        countries = select(load_registry(settings.registry_path), codes)
        for p in cleanup_stale_tmp(settings.data_dir):
            log.info("removed leftover temp file %s", p)

        state = State(settings.state_path)
        results: list[UpdateResult] = []
        for c in countries:
            try:
                result = update_country(c, settings, state, runner)
                if result.status == MISSING and settings.auto_bootstrap:
                    bootstrap_country(c, settings, state)
                    result = update_country(c, settings, state, runner)
            except Exception as e:  # one country never blocks the others
                log.exception("%s: unexpected error", c.code)
                result = UpdateResult(c.code, "failed", message=str(e))
            log.info("%s: %s %s", c.code, result.status, result.message)
            results.append(result)

        alerts = [Alert(CRITICAL, r.code, f"update {r.status}: {r.message}")
                  for r in results if not r.ok]
        failed = {a.country for a in alerts}
        alerts += [a for a in check_staleness(countries, settings) if a.country not in failed]
        for a in alerts:
            send_alert(a, settings)

        ok = all(r.ok for r in results) and not any(a.level == CRITICAL for a in alerts)
        state.mark_cycle(ok)
        return ok
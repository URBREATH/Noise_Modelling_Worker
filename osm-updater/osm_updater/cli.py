"""Command line: python -m osm_updater <command>."""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys

from .alerts import CRITICAL, check_staleness, send_alert
from .bootstrap import BootstrapError, bootstrap_country
from .config import Settings
from .cycle import run_cycle
from .fsutil import LockBusy, exclusive_lock
from .pbf import read_replication_info
from .registry import RegistryError, ensure_registry, load_registry, select
from .scheduler import run_daemon
from .state import State, parse_iso
from .update import update_country


def _countries(settings: Settings, codes: list[str]):
    ensure_registry(settings.registry_path, settings.default_registry)
    return select(load_registry(settings.registry_path), codes)


def cmd_bootstrap(settings: Settings, args) -> int:
    with exclusive_lock(settings.lock_path):
        state = State(settings.state_path)
        rc = 0
        for c in _countries(settings, args.countries):
            try:
                bootstrap_country(c, settings, state, force=args.force)
            except BootstrapError as e:
                logging.error("%s", e)
                rc = 1
        return rc


def cmd_update(settings: Settings, args) -> int:
    with exclusive_lock(settings.lock_path):
        state = State(settings.state_path)
        results = [update_country(c, settings, state) for c in _countries(settings, args.countries)]
    for r in results:
        print(f"{r.code}: {r.status} seq {r.sequence_before} -> {r.sequence_after} {r.message}".rstrip())
    return 0 if all(r.ok for r in results) else 1


def cmd_run(settings: Settings, args) -> int:
    return 0 if run_cycle(settings, args.countries or None) else 1


def cmd_check(settings: Settings, args) -> int:
    alerts = check_staleness(_countries(settings, args.countries), settings)
    for a in alerts:
        send_alert(a, settings) if args.notify else print(f"[{a.level}] {a.country}: {a.message}")
    if any(a.level == CRITICAL for a in alerts):
        return 2
    return 1 if alerts else 0


def cmd_status(settings: Settings, args) -> int:
    state = State(settings.state_path)
    print(f"{'code':4} {'sequence':>10} {'data age (d)':>12} {'size MB':>9}  last success / last error")
    for c in _countries(settings, args.countries):
        path = c.pbf_path(settings.data_dir)
        st = state.country(c.code)
        if path.exists():
            info = read_replication_info(path)
            age = info.age_days()
            row = f"{c.code:4} {info.sequence!s:>10} {age if age is None else round(age, 1)!s:>12} " \
                  f"{path.stat().st_size / 1e6:9.1f}"
        else:
            row = f"{c.code:4} {'-':>10} {'-':>12} {'missing':>9}"
        print(f"{row}  {st.get('last_success_at') or '-'} / {st.get('last_error') or '-'}")
    last = state.data.get("last_cycle")
    print(f"last cycle: {last}")
    return 0


def cmd_health(settings: Settings, args) -> int:
    """Docker HEALTHCHECK: unhealthy if no cycle completed recently."""
    last = State(settings.state_path).data.get("last_cycle") or {}
    finished = parse_iso(last.get("finished_at"))
    if finished is None:
        print("no completed cycle yet")
        return 1
    age_h = (dt.datetime.now(dt.timezone.utc) - finished).total_seconds() / 3600
    if age_h > settings.health_max_hours:
        print(f"last cycle finished {age_h:.1f} h ago")
        return 1
    print(f"ok, last cycle {age_h:.1f} h ago, result {'ok' if last.get('ok') else 'with problems'}")
    return 0


def cmd_daemon(settings: Settings, args) -> int:
    run_daemon(settings)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="osm-updater",
                                description="Maintain per-country Geofabrik OSM PBF masters.")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, func, help_, countries=True):
        sp = sub.add_parser(name, help=help_)
        if countries:
            sp.add_argument("countries", nargs="*", metavar="CODE",
                            help="country codes from the registry (default: all)")
        sp.set_defaults(func=func)
        return sp

    add("bootstrap", cmd_bootstrap, "download missing master PBFs") \
        .add_argument("--force", action="store_true", help="re-download even if present")
    add("update", cmd_update, "apply replication diffs to existing masters")
    add("run", cmd_run, "one full cycle: bootstrap missing, update, staleness alerts")
    add("check", cmd_check, "staleness check (exit 1 warning, 2 critical)") \
        .add_argument("--notify", action="store_true", help="send alerts through the hooks")
    add("status", cmd_status, "show sequence, data age and last result per country")
    add("health", cmd_health, "container healthcheck", countries=False)
    add("daemon", cmd_daemon, "run a cycle every day at OSM_UPDATE_TIME_UTC", countries=False)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    settings = Settings.from_env()
    try:
        return args.func(settings, args)
    except (RegistryError, LockBusy) as e:
        logging.error("%s", e)
        return 3


if __name__ == "__main__":
    sys.exit(main())
"""Long-running daemon: one cycle per day at a fixed UTC time.

A built-in loop instead of cron keeps the container to a single process that
receives SIGTERM directly and logs to stdout.
"""
from __future__ import annotations

import datetime as dt
import logging
import signal
import threading

from .config import Settings
from .cycle import run_cycle
from .fsutil import LockBusy

log = logging.getLogger(__name__)


def next_run(now: dt.datetime, hhmm: str) -> dt.datetime:
    hour, minute = (int(x) for x in hhmm.split(":"))
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return candidate if candidate > now else candidate + dt.timedelta(days=1)


def run_daemon(settings: Settings) -> None:
    stop = threading.Event()

    def _handle(signum, _frame):
        log.info("received signal %s, stopping after the current step", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)

    first = settings.run_on_start
    while not stop.is_set():
        if not first:
            now = dt.datetime.now(dt.timezone.utc)
            target = next_run(now, settings.update_time_utc)
            log.info("next cycle at %s", target.isoformat())
            if stop.wait((target - now).total_seconds()):
                break
        first = False
        try:
            ok = run_cycle(settings)
            log.info("cycle finished: %s", "ok" if ok else "with problems")
        except LockBusy as e:
            log.warning("%s; skipping this cycle", e)
        except Exception:
            log.exception("cycle crashed")
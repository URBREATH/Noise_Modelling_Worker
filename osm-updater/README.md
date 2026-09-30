# osm-updater (URBREATH)

Keeps one OpenStreetMap PBF master file per participating country
(DK, GR, RO, FI, BE, ES, IT, CZ, EE) up to date: each file is downloaded once
from Geofabrik and then updated daily with `pyosmium-up-to-date`.

## Layout

```
osm_updater/
  config.py      settings from environment variables
  registry.py    country registry (§3.4), validation, seeding onto the volume
  bootstrap.py   initial download: temp file, .md5 and header checks, atomic rename
  update.py      pyosmium-up-to-date loop (exit 1 = more diffs pending), atomic rename
  alerts.py      staleness check and alert hooks (webhook and/or command)
  cycle.py       one full cycle: bootstrap missing countries, update, staleness
  scheduler.py   daemon: one cycle per day at a fixed UTC time
  fsutil.py      atomic rename with fsync, process lock, temp file cleanup
config/countries.json   default registry of the 9 countries
tests/                  tests against a local fake replication server
Dockerfile, docker-compose.example.yml
```

## Commands

```
python -m osm_updater bootstrap [--force] [IT RO ...]   # download missing masters
python -m osm_updater update [IT ...]                   # apply diffs to existing masters
python -m osm_updater run [IT ...]                      # full cycle (what the daemon runs)
python -m osm_updater check [--notify]                  # staleness: exit 1 warning, 2 critical
python -m osm_updater status                            # sequence, data age, last result per country
python -m osm_updater health                            # container healthcheck
python -m osm_updater daemon                            # container default
```

Without country codes, every command works on the whole registry.

## How it works

**Registry.** `countries.json` lives on the shared volume (`/data/osm`). On first
start it is copied from the default bundled in the image; from then on, edit the
copy on the volume. Each entry has `pbf_file` (relative to the volume),
`download_url`, `md5_url`, `updates_url` (Geofabrik's `-updates` folder) and
`poly_url` (country boundary, downloaded next to the PBF; Phase 2 will use it to
pick the right file for a polygon). Adding a country means adding an entry; the
next cycle downloads it.

**Bootstrap.** Downloads into a temp file in the same folder as the master. It
fetches Geofabrik's `.md5` first (so a network problem fails fast), then the PBF,
computing its md5 while streaming; on a mismatch it re-reads the `.md5` once
(Geofabrik may have published a new extract during the download). Every request
is retried up to 3 times, 30 s apart. Finally it checks that the PBF header is
readable, then does `fsync` and `os.replace` onto the master. If anything fails,
the master is left untouched and the temp file is deleted.

**Update.** For each country it runs
`pyosmium-up-to-date -o <temp file> --size 1024 <master>`. On exit 0 or 1 the temp
file is atomically renamed onto the master; on exit 1 (diffs still pending) it
runs again, up to `OSM_MAX_UPDATE_ITERATIONS`. Exit 1 without the sequence
advancing, or any exit ≥ 2, is an error: master untouched, alert raised. The
replication server is read from the PBF header (Geofabrik writes it); `--server`
is passed only when the header lacks it. Before each pass it checks there is free
space equal to 1.2× the file size.

Readers that already opened the master keep reading the old version until they
close it (§3.3); a test covers this. A file lock prevents two cycles from running
on the same volume at once.

**Staleness and the 100-day window.** Data age is read from the replication
timestamp in the PBF header, not from the last run, so the alert fires even when
the updater runs but Geofabrik stops publishing. Important note from the
pyosmium 4.3.1 source: `pyosmium-up-to-date` refuses files older than **90 days**
(exit 3), before Geofabrik's 100-day diff retention. Hence:

| Threshold | Default | Effect |
|---|---|---|
| `OSM_STALE_WARN_DAYS` | 7 | `warning` alert |
| `OSM_STALE_CRIT_DAYS` | 60 | `critical` alert |
| `OSM_REBOOTSTRAP_AFTER_DAYS` | 85 | automatic full re-download (if `OSM_AUTO_REBOOTSTRAP=true`), otherwise an error |

The container also has a `HEALTHCHECK` that turns unhealthy when the last
completed cycle is older than `OSM_HEALTH_MAX_HOURS` (36 h), which covers the
case where the updater itself has stopped.

**Alert hooks.** Every alert is logged. In addition, when configured:
- `OSM_ALERT_WEBHOOK_URL`: JSON POST `{"source","level","country","message","data_age_days"}`;
- `OSM_ALERT_COMMAND`: shell command run with `OSM_ALERT_LEVEL`, `OSM_ALERT_COUNTRY`,
  `OSM_ALERT_MESSAGE`, `OSM_ALERT_JSON` in its environment.

A failing hook is logged and never stops the cycle.

## Environment variables

| Variable | Default | |
|---|---|---|
| `OSM_DATA_DIR` | `/data/osm` | shared volume holding the masters |
| `OSM_REGISTRY` | `$OSM_DATA_DIR/countries.json` | registry on the volume |
| `OSM_UPDATE_TIME_UTC` | `22:30` | daily cycle time (Geofabrik publishes around 21:00 CET) |
| `OSM_RUN_ON_START` | `true` | run a cycle as soon as the daemon starts |
| `OSM_AUTO_BOOTSTRAP` | `true` | download missing countries on every cycle |
| `OSM_DIFF_SIZE_MB` | `1024` | pyosmium `--size` per pass |
| `OSM_MAX_UPDATE_ITERATIONS` | `50` | max passes per country per cycle |
| `OSM_DISK_MARGIN` | `1.2` | required free space, as a multiple of the file size |
| `OSM_HTTP_TIMEOUT` | `120` | bootstrap HTTP timeout (s) |

Plus the thresholds and hooks described above.

## Docker

```
docker build -t urbreath/osm-updater .
docker compose -f docker-compose.example.yml up -d
docker compose -f docker-compose.example.yml exec osm-updater python -m osm_updater status
```

The first start downloads all 9 countries (Italy alone is ~2.1 GB): plan disk
space for the total plus the update margin. The noise-worker will mount the
`osm-data` volume read-only.

When testing locally with Docker Desktop on WSL, bind-mount a folder under your
home directory, not `/tmp` (Docker Desktop sees its own empty `/tmp`, so the
container seeds the full registry and starts downloading), and set
`OSM_AUTO_BOOTSTRAP=false` so a wrong mount never triggers downloads:

```
docker run --rm -e OSM_AUTO_BOOTSTRAP=false -v ~/osm-test:/data/osm urbreath/osm-updater run
```

## Tests

```
pip install osmium==4.3.1 pytest
python -m pytest tests
```

The tests download nothing from Geofabrik: they build a tiny PBF and a local fake
replication server with Geofabrik's layout (`state.txt`, `000/000/NNN.osc.gz`),
and run the real `pyosmium-up-to-date` against it. They cover bootstrap with a
good and a bad md5, retries on transient errors, full update, looping on exit 1,
the iteration cap, pyosmium errors, an unreachable server, a too-old file
(re-bootstrap), disk space, readers holding the file during the swap, staleness
thresholds, hooks, a multi-country cycle, locking and the scheduler.

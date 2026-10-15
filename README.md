# Noise Modelling Worker

Road-traffic noise maps on demand. Send a polygon, get back the **Lden noise contours** (GeoJSON) for that area,
computed with [NoiseModelling 5.0.1](https://github.com/Universite-Gustave-Eiffel/NoiseModelling) from OpenStreetMap data.

Two services, run together with Docker Compose:

| Service | What it does |
|---|---|
| `osm-updater` | Downloads the OSM extracts (Geofabrik `.osm.pbf`) of the configured regions and keeps them up to date every night with the OSM diffs. |
| `noise-worker` | HTTP API. For each job: cuts the OSM data around the polygon, runs the NoiseModelling pipeline, returns the noise contours. One job at a time, in arrival order. |

Both share the Docker volume `osm-data`: the updater writes it, the worker only reads it.

```
client ──POST /jobs──▶ noise-worker ──reads──▶ osm-data volume ◀──writes── osm-updater ◀── Geofabrik
       ◀─GET /jobs/{id}─┘   │
                            └─ osmium extract ─▶ NoiseModelling (Java, in the same container) ─▶ GeoJSON
```

## Requirements

- Docker with the Compose plugin (`docker compose version`).
- About 5 GB of free disk for the 9 configured regions, plus some space per job.
- Internet access to `download.geofabrik.de` from the updater container.

## Quick start

```bash
# 1. Build the NoiseModelling image (the worker copies NoiseModelling from it)
docker build -t urbreath-nm noise-worker/pipeline

# 2. Build and start both services
docker compose up -d --build

# 3. Follow the first download (about 3.5 GB, roughly one hour)
docker compose logs -f osm-updater
docker compose exec osm-updater python -m osm_updater status
```

The worker answers on `http://localhost:8001` (change it with `NOISE_WORKER_PORT`).
Interactive API docs: `http://localhost:8001/docs`.
Jobs on a region that is not downloaded yet fail with an `input` error; that is expected.

## API

### `POST /jobs`

```json
{
  "mode": "preset",
  "polygon": {
    "type": "Polygon",
    "coordinates": [[[24.7362, 59.4325], [24.7538, 59.4325], [24.7538, 59.4415], [24.7362, 59.4415], [24.7362, 59.4325]]]
  }
}
```

- `polygon`: GeoJSON `Polygon`, longitude/latitude WGS84 (EPSG:4326). It must lie inside one configured region.
- `mode`: `preset` (NoiseModelling defaults) or `custom` (with `params`, see below).

Response `202`: `{"id": "<32 hex chars>", "status": "queued"}`.
Obvious mistakes (unknown or out-of-range parameter, `params` with `preset`) are rejected at once with `400`/`422`.

### `GET /jobs/{id}`

`status` is `queued`, `running`, `done` or `failed`.

- `done`: the response contains `result`, a GeoJSON FeatureCollection (EPSG:4326) of Lden noise bands.
- `failed`: the response contains `error = {"type": "input" | "internal", "message": "..."}`.
  `input` means the request is wrong (bad polygon, area outside every region): retrying the same request will not help.
  `internal` means something broke on the worker side (NoiseModelling, extract, restart): retrying makes sense.

If the worker restarts, jobs that were queued or running are marked `failed` with `worker restarted`.

Results can be several MB: Swagger UI may fail to display them. Use `curl -o result.json` (or `Invoke-WebRequest -OutFile` on Windows).
A trimmed example response is in `noise-worker/examples/job_done_response_sample.json`.

### `GET /health`

`200` when the job queue is alive, `503` otherwise. Used by the Docker healthcheck.

### Custom mode

```json
{ "mode": "custom", "polygon": { ... }, "params": { "confMaxSrcDist": 300, "confReflOrder": 0 } }
```

| Parameter | Type | Range | Meaning |
|---|---|---|---|
| `confMaxSrcDist` | number (m) | 10 - 500 | max source-receiver distance (NoiseModelling default 150) |
| `confMaxReflDist` | number (m) | 0 - 500 | max distance of reflecting walls |
| `confReflOrder` | integer | 0 - 3 | number of reflections on buildings |
| `paramWallAlpha` | number | 0 - 0.99 | wall absorption coefficient |
| `confDiffVertical` | boolean | | diffraction on vertical edges |
| `confDiffHorizontal` | boolean | | diffraction on horizontal edges |
| `confHumidity` | number (%) | 0 - 100 | relative humidity |
| `confTemperature` | number (°C) | -40 - 50 | air temperature |

All parameters are optional; missing ones keep the NoiseModelling default.
When `confMaxSrcDist` is larger than the extract buffer (200 m), the buffer grows to the same distance.

## Configuration

Environment variables read by the root `docker-compose.yml`:

| Variable | Default | Meaning |
|---|---|---|
| `NOISE_WORKER_PORT` | `8001` | host port of the worker API |
| `EXTRACT_BUFFER_M` | `200` | buffer around the polygon for the OSM extract (m) |
| `NM_TIMEOUT_S` | `3600` | max duration of one NoiseModelling run (s) |
| `OSM_UPDATE_TIME_UTC` | `22:30` | daily update time of the OSM extracts |
| `OSM_STALE_WARN_DAYS` / `OSM_STALE_CRIT_DAYS` | `7` / `60` | data-age alert thresholds |
| `OSM_REBOOTSTRAP_AFTER_DAYS` | `85` | full re-download age (OSM diffs are not usable after 90 days) |

More updater settings (alerts by webhook or command, disk margin, ...) are in `osm-updater/README.md`.

### Regions

The regions are listed in `osm-updater/config/countries.json` (one Geofabrik extract per entry, with its `.poly` boundary).
The worker picks the smallest region whose boundary contains the buffered polygon.

On first start the file is copied to the `osm-data` volume, and **the copy on the volume is the one in use**.
After editing the repo file, copy it again and download the new region:

```bash
docker compose cp osm-updater/config/countries.json osm-updater:/data/osm/countries.json
docker compose exec osm-updater python -m osm_updater bootstrap <CODE>
```

## Tests

The unit tests run in containers (no NoiseModelling run, no download):

```bash
docker compose build
docker run --rm --entrypoint sh -v "$PWD/noise-worker:/src" -w /src noise-worker:0.1.0 -c "pip install -q pytest httpx && python -m pytest -q"
docker run --rm --entrypoint sh -v "$PWD/osm-updater:/src" -w /src osm-updater:0.1.0 -c "pip install -q pytest && python -m pytest -q"
```

On Windows PowerShell use `${PWD}` instead of `$PWD`.

Example requests: `noise-worker/examples/` (`cities/` has one small polygon per configured city).

## Repository layout

```
docker-compose.yml          both services together (use this one)
osm-updater/                OSM extract downloader/updater (Python, pyosmium)
  config/countries.json     configured regions
  docker-compose.example.yml  updater alone (development)
noise-worker/               worker API (Python, FastAPI) + extract (osmium)
  pipeline/                 NoiseModelling image and Groovy pipeline
  docker-compose.yml        worker alone (development)
  examples/                 example requests and responses
```

The two compose files inside the subfolders start one service alone and are meant for development tests only.
Do not run them together with the root compose: they use the same port.

## License

This project is licensed under the **GNU Affero General Public License v3.0** (AGPL-3.0). See [LICENSE](LICENSE).

In short: you may use, modify and redistribute it, but if you distribute it or make a modified version
available to users over a network (for example as a web service), you must publish the corresponding source code
under the same license.

Third-party components keep their own licenses:

- [NoiseModelling](https://github.com/Universite-Gustave-Eiffel/NoiseModelling): GPL-3.0.
- OpenStreetMap data, downloaded from [Geofabrik](https://download.geofabrik.de/): © OpenStreetMap contributors,
  [ODbL 1.0](https://opendatacommons.org/licenses/odbl/). Noise maps produced from it are derived works:
  when you publish them, credit "© OpenStreetMap contributors".

## Contact

Maintainer: <Francesco Nudo>, <francesco.nudo@eng.it>

Bug reports and questions: open an issue on this repository, or write to the address above.
# Deploying OpenEarth v2 (Docker Compose)

One command brings up the whole stack — the FastAPI backend and the React/MapLibre
frontend behind an SSE-safe nginx proxy:

```bash
docker compose up --build      # → http://localhost:8080
```

`web` (nginx) serves the built SPA and reverse-proxies `/api` to the `api` container.
All state lives under `./data` (bind-mounted), so it survives `docker compose down`.

The app **boots with no credentials**. Earth Engine routes return `503` until an EE
project + credentials are configured; the EMIT V002 plume fallback returns `502`
until an Earthdata token is set. Everything else (the catalog, saved AOIs/workspaces,
the DB, the UI) works immediately.

## Layout

| Piece | Where |
|---|---|
| API image (multi-stage uv) | `docker/api.Dockerfile` — `uv sync --frozen --no-dev --package openearth-api` (no torch/smp) |
| Web image (Vite → nginx) | `docker/web.Dockerfile` + `docker/nginx.conf` |
| Compose | `compose.yaml` (repo root) |
| Persistent state | `./data` → `/data` (`OPENEARTH_DATA_DIR=/data`) |

## Environment

Set these in a `.env` file next to `compose.yaml` (Compose reads it automatically) or
in your shell. All are optional — omit them and the corresponding routes degrade
gracefully.

| Variable | Purpose | Default |
|---|---|---|
| `OPENEARTH_EE_PROJECT` | GCP project with Earth Engine enabled. Required for **any** EE route. | unset → EE routes 503 |
| `EARTHDATA_TOKEN` | NASA Earthdata Login bearer token, for the EMIT **V002** plume fallback (post-Oct-2024 windows). | unset → V002 fetch 502 |
| `GOOGLE_APPLICATION_CREDENTIALS` | In-container path to a service-account JSON, for **headless** EE auth (see below). | unset |
| `OPENEARTH_DATA_DIR` | State directory. Pinned to `/data` in the image; the compose bind-mount points it at `./data`. | `/data` |

The frozen GEE V001 EMIT mirror (windows ≤ 2024-10-26) needs **no** Earthdata token —
only V002 does.

## Earth Engine authentication in containers

`ee.Initialize(project=OPENEARTH_EE_PROJECT)` uses the standard Google credential
chain, so either path works — pick one and mount it into the `api` service in
`compose.yaml` (both mounts are pre-written and commented there):

**Personal use — mounted user credentials.** After running
`earthengine authenticate` (or `earthaccess`/`gcloud`) on the host, mount the stored
credentials read-only:

```yaml
    volumes:
      - ./data:/data
      - ${HOME}/.config/earthengine:/home/openearth/.config/earthengine:ro
```

(The container runs as the non-root user `openearth`, whose home is `/home/openearth`.)

**Headless / server — service account.** Create a service account with the *Earth
Engine Resource Viewer* role, register it for EE, download its JSON key, then:

```yaml
    environment:
      GOOGLE_APPLICATION_CREDENTIALS: /run/secrets/ee-sa.json
    volumes:
      - ./data:/data
      - ./ee-service-account.json:/run/secrets/ee-sa.json:ro
```

Either way, set `OPENEARTH_EE_PROJECT`. The startup EE init is **non-fatal** — if it
fails, the app still serves; only EE-touching routes return `503` (with the reason
surfaced at `GET /api/config`).

## Earthdata (EMIT V002) authentication

`earthaccess.login(strategy="environment")` reads `EARTHDATA_TOKEN` (or
`EARTHDATA_USERNAME`/`EARTHDATA_PASSWORD`, or a mounted `~/.netrc`). Generate a token at
<https://urs.earthdata.nasa.gov> → *Generate Token*. Without it, the frozen GEE V001
EMIT path still works; only the V002 fallback 502s.

## Persistence & backup

Everything the app writes lives under `./data`:

- `openearth.db` — SQLite (WAL): jobs, saved AOIs/workspaces, methane sites +
  detections + reference events, timelapse renders.
- `cache/` — diskcache (thumbnails, series, EMIT plume lists, embedding seeds …).
- `detections/`, `timelapse/`, `exports/` — analysis artifacts (npz, movies, GeoTIFFs).
- `catalog.d/` — user TOML datasets.

To back up: stop the stack (`docker compose down`) and copy `./data`, or snapshot it
live (SQLite WAL tolerates a hot copy for a backup, but a quiesced copy is safest).

## CI

- Every PR/push runs `docker compose config -q` (validates the compose file) — cheap.
- Both images are built on **tags only** (`v*`) to keep PR CI fast; they are not
  pushed to a registry by default (add credentials + `push: true` to publish).

## Licensing reminder (public deployments)

The ML tier's weights are a **CH4Net derivative under CC-BY-NC-ND 4.0**. The **ND**
term forbids redistributing them, so no weights / ONNX / manifest are ever committed
or baked into an image — they ship out-of-band under `data_dir/ml/models/`. **A public
deployment must not serve these weights** (retrain on a redistributable dataset first).
The API image builds without the ML stack entirely, so a default deployment is already
clean; the ML scan route simply 503s until a model is mounted for private use. See
`docs/methane_methods.md` §9.

# Geo Service

**Port:** 8009 · **Image:** `ghcr.io/jmiahman1/sharedllm-geo:latest` · **Code:** `services/geo/`

Life360-style family location / mapping layer for the de-Googled phone stack
(S26-Setup). A self-hosted, privacy-respecting replacement for Google Maps
location history and Life360 sharing.

## Why this exists

On the Galaxy S25 de-Google setup, Google Maps (location history, place
search, navigation) and Life360-style sharing were disabled. This service
restores that capability without sending location to Google.

## Architecture decision: OSM + MapLibre

- **OpenStreetMap (OSM)** is the map *data*. There is no serious fully-free
  global map dataset rival (OpenHistoricalMap / OpenSeaMap are OSM forks).
- **"Better than OSM" means a better renderer on OSM data.** The best open
  renderer is **MapLibre GL JS** (BSD-2): vector tiles, smooth pan/zoom, 3D,
  mobile-friendly. Raster Leaflet/OSM is inferior UX. Organic Maps / OsmAnd are
  great offline *apps* but not embeddable web renderers like MapLibre.
- Tile / style sources (all self-hostable or free-tier): Protomaps (PMTiles,
  single static file, serverless — recommended), OpenMapTiles (self-host vector
  tiles), `tile.openstreetmap.org` (raster, usage-policy limited — dev only).

## Backend: wrap Home Assistant (already running)

Home Assistant (`https://ha.sumemail.com:8095`) already provides, for free:

| HA concept | Geo use |
|------------|---------|
| `person` | One aggregated person from one+ device_trackers |
| `device_tracker` | lat / lon / gps_accuracy / battery per device |
| `zone` | geofences (home/away + custom), passive zones |
| History / Recorder | location point history → trip replay |
| REST `/api/states` + websocket `subscribe_entities` | live push of states |
| `device_tracker.see` service | programmatically set a tracker from lat/lon |

The HA Android companion app already pushes GPS/battery/geocode. So HA is the
backend; this service is a thin Jarvis adapter that exposes HA states as GeoJSON
and can accept pushes.

### Why not Traccar as primary?
Traccar (Apache-2) is the *most complete* FOSS GPS platform (geofencing,
sharing, history, apps). It is kept as a **documented upgrade path** if HA's
sharing/geofence UX proves insufficient — HA has a `traccar_server` integration,
so the client can switch backends without rework. HA is preferred first because
it is already deployed and bridges to Jarvis Execution 8003.

## Endpoints (implemented in `services/geo/main.py`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | liveness + `ha_configured` flag |
| GET | `/people` | GeoJSON FeatureCollection of all `person` + `device_tracker` entities (HA states → points) |
| GET | `/zones` | GeoJSON FeatureCollection of HA `zone` entities (geofences) |
| GET | `/people/{entity_id}/history?samples=` | trip points from HA Recorder (minimal_response) for replay |
| POST | `/people/{entity_id}/see` | push location via HA `device_tracker.see` (guarded by `X-Internal-Secret`) |

Live push to the Android client is done by the client subscribing to HA
websocket `subscribe_entities` directly; a `/stream` proxy can be added here
later if the Gateway needs to fan out updates.

## Configuration

Resolves `HA_URL` / `HA_TOKEN` at boot from Identity (`resolve_runtime_config`),
exactly like every other service — no hardcoded URLs. Calls HA with the
insecure pooled client (`services.common.http.get_client_insecure()`) because HA
uses a self-signed cert.

Registered in `config.py` as `GEO_SVC_URL` (default `http://geo:8009`),
exposed in compose as `BRIDGE_GEO_SVC_URL`, routed via Caddy `:8009`, and added
to the build matrix in `.github/workflows/build-images.yml`.

## Client

A self-contained MapLibre web client is served at `GET /` (file
`services/geo/static/index.html`) — no build step, loads MapLibre GL JS from
CDN and OSM raster tiles (no API key). It renders `/people` + `/zones`, lets
you tap a person to replay their `/people/{id}/history` trail, and auto-refreshes
every 15s. Open `http://<host>:8009/` in any browser (or Brave on the phone).
It works as a PWA-style target and is the stopgap until a native Android view
is built into the SharedLLM app. The native client would render the same
endpoints via MapLibre (Protomaps/OSM vector tiles) and call
`POST /people/{id}/see` for push (requires `X-Internal-Secret`).

## License notes
MapLibre GL JS: BSD-2 · OSM data: ODbL · Protomaps: see their license · HA
core: Apache-2 · Traccar: Apache-2. All self-hostable / FOSS.

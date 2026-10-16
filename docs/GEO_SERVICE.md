# Geo Service

**Port:** 8009 · **Image:** `ghcr.io/jmiahman1/sharedllm-geo:latest` · **Code:** `services/geo/`

Life360-style family location and activity layer: live positions, places,
trips, steps, workouts, achievements and stars — self-hosted, nothing sent to
Google.

## Data sources

- **Home Assistant** (`HA_URL` / `HA_TOKEN` from Identity via
  `resolve_runtime_config`): `person` / `device_tracker` states, `zone`
  geofences ("Places"), Recorder history for replay, `device_tracker.see` for
  pushes. Called with the insecure pooled client (HA uses a self-signed cert).
- **The Jarvis app**: phones post GPS to `POST /api/users/{user}/location`
  (gateway → Identity), which stores `user_location:{user}` and forwards to
  HA and to geo (`/people/{id}/record`). The Android app's native
  `LocationTrackingService` keeps posting with the app closed when the user
  allowed "Allow all the time" (see `docs/FAMILY_HUB.md`).
- **OSRM** (`osrm` roads, `osrm-foot` paths) for map matching and ETAs, and
  **Nominatim** for place names — both self-hosted (below). With `OSRM_URL`
  unset or OSRM down, map matching returns nothing and raw fixes are used.

## Endpoints (`services/geo/main.py`; gateway proxies under `/api/geo/*`)

| Area | Routes |
|---|---|
| Health / client | `GET /health`, `GET /` (static map client) |
| People and places | `GET /people`, `GET /zones`, `GET /people/{id}/history`, `POST /people/{id}/see` (internal secret), `POST /people/{id}/record`, `GET /closest`, `GET /people/{id}/eta`, `GET /people/{id}/telemetry`, `GET /telemetry/{id}`, `GET /android_auto` |
| Presence alerts | `GET/PUT /presence-alerts/{viewer}` (arrive/leave notices, on by default) |
| Trips | `GET /trips`, `GET/PATCH /trips/{id}`, `PATCH /trips/{id}/share`, `GET /trips/{id}/route`, `GET /trips/{id}/locations`, `GET /locations/suggestions` |
| Steps and metrics | `POST/GET /steps`, `GET /steps/sources`, `GET /steps/ranges`, `GET/PUT /steps/goal`, `GET /metrics/ranges`, `GET /metrics/catalog`, `GET /events` |
| Workouts | `GET /workouts`, `POST /workouts/start`, `GET /workouts/active`, `POST /workouts/stop`, `GET /workouts/{id}/route` |
| Goals, achievements, stars | `GET/PUT /goals`, `GET /achievements`, `GET /points`, `GET/POST /api/geo/stars` (`docs/ACHIEVEMENTS.md`) |
| Sharing | `GET /activity/summary`, `GET /activity/feed` (consent-gated) |
| Trends | `GET /trends/activity` (read-only), `POST /trends/activity/analyze` (explicit opt-in, per-user lock) |
| Vehicles | `GET/POST /vehicles`, `DELETE /vehicles/{id}`, `GET /vehicles/assigned/{user}`, `POST /vehicles/assign`, `GET /vehicle-lookup/*`, `GET /fuel-prices` |
| Maps | `GET /map/regions` |

## Trips: how a drive is recorded

Every fix that reaches `record_point` (accuracy ≤ 100 m, not an impossible
jump checked against OSRM) goes through `process_trip_point`:

- **Start / advance** at ≥ 10 mph. Speed is the fix's own, else derived from
  the previous breadcrumb **only when the two are ≤ 120 s apart**.
- **Finish** after 5 min without moving (`TRIP_IDLE_S`). A background
  sweeper (`_idle_trip_sweeper`, every 60 s) finishes idle drives on the
  server's clock; reads of `/trips` do too. Before the sweeper a drive ended
  only on that phone's next fix, and a phone that went quiet lost it.
- On finish the trail is map-matched (OSRM): road distance wins; a trail that
  fits no road, or matches below the confidence floor, is discarded as a
  phantom; anything under 0.2 mi is dropped.

So trip detection needs a phone that reports every ~30 s while moving. A
phone that only sends its 5–10 minute heartbeat (no "Allow all the time",
battery optimisation on, or an OEM that kills the service) never produces a
trip — its fixes are stored, but no two are close enough in time to measure a
speed. Seen live 2026-10-15: Michele's phone reported every ~600 s for days,
so none of the 15 trips were hers.

`GET /trips?scope=family&viewer=…` is what Wander shows: the viewer's trips
plus those of everyone who shares their activity with them
(`_viewer_may_see`, one sharing lookup per request), newest first, with
shared rides paired across people (`group_shared_trips` only pairs trips in
the same list). `user_id=…` returns one person's (consent-gated); the
`all` bucket is admin-only.

Trip start/end locations store `{name, latitude, longitude, address?}`.
Coordinates and distance are locked GPS telemetry, but `PATCH /trips/{id}`
accepts `start_name` / `end_name` / `start_address` / `end_address`, so a user
can rename a pin (or pick a nearby store/zone from `/locations/suggestions`).
User-picked names win on read (`source: "stored"`).

## Configuration

| Setting | Source | What |
|---|---|---|
| `HA_URL`, `HA_TOKEN` | Identity | Home Assistant |
| `OSRM_URL`, `OSRM_FOOT_URL` | compose env | road / foot routing |
| `NOMINATIM_URL` | compose env | self-hosted geocoding (public Nominatim is the fallback outside the import) |
| `FAMILY_CHAT_TOKEN` | env | room for achievement cards (silent when unset) |
| `APP_TIMEZONE` | env | day boundaries (default `America/Phoenix`) |

Registered in `services/config.py` as `GEO_SVC_URL` (`BRIDGE_GEO_SVC_URL`),
routed via Caddy `:8009`.

## Maps on the server (live on 205 since 2026-10-07)

- `osrm` (roads, ~770 MB) and `osrm-foot` (paths, ~1.2 GB), both rebuilt by
  GitHub Actions from `osrm/regions.txt` (`.github/workflows/osrm-map.yml`);
  the control plane redeploys both after a build (`services/control_plane/osrm_map.py`).
- `nominatim` (Arizona, ~10 GB on disk in `data/nominatim`, ~1.5 GB RAM). It
  does not grow with `osrm/regions.txt`: places outside Arizona use the public
  service. Re-importing a merged extract would cover more.
- Leave-by reminders need the person's own events and a fresh position;
  phones without the battery exemption or "Allow all the time" go quiet.

## Clients

- **In-app** (React): Leaflet maps — `components/geo/LiveFamilyMap.tsx`,
  `MiniRouteMap.tsx`, `TripLocationsMap.tsx`. Freshness: live < 2 min, recent
  < 15 min, last seen up to 24 h (dimmed pin); members within 60 m cluster;
  the map starts at the home zone and auto-fits once. The UI reads positions
  from `GET /api/users/location/all` (signed-in members only, as is
  `GET /api/users/{user}/location`) and ages a pin by when the fix was taken
  (`timestamp`), not when it arrived (`updated_at`): a phone re-sends a held
  fix when it gets no new one.
- **Wander cards** (`lib/familyCards.ts`) merge each HA `person` with the
  app's own fix, whichever is newer. Route thumbnails are one TanStack query
  per trip (`routeQueryKey`), shared with the Route Map modal.
- **Standalone**: a MapLibre page at `GET /` (`services/geo/static/index.html`,
  OSM raster tiles, no build step) for any browser on the LAN.

## Why Home Assistant and not Traccar

HA was already deployed and its companion app pushes GPS/battery. Traccar
(Apache-2) stays the documented upgrade path if HA's sharing/geofence model
proves insufficient; HA has a `traccar_server` integration, so clients would
not need rework.

## License notes

Leaflet: BSD-2 · MapLibre GL JS: BSD-2 · OSM data: ODbL · OSRM: BSD-2 ·
Nominatim: GPL-2 · HA core: Apache-2.

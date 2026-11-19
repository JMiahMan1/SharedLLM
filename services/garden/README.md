# Jarvis Garden Service

Port **8012**, container `sharedllm_garden`.  Phase 1 of the Garden hub:
species-smart houseplant care with an adaptive cadence engine, self-calibrating
"Still damp" snooze, personal plant names, and a read-only botanical seed.

## Databases

- `/data/garden.db` — household store (`GARDEN_DATABASE_URL` / `GARDEN_DB_PATH`):
  `garden_profile`, `locations`, `plants`, `care_rules`, `care_logs`.
- `/data/plants-seed.db` — read-only botanical seed (`PLANT_SEED_PATH`), built
  offline by `scripts/build_plants_seed.py` from `data/plants_seed/*.json`.
  Absent seed degrades gracefully: the service runs without it.

## Auth

All routes except `GET /health` require `X-Internal-Secret`.  The Gateway
forwards `X-User-Id`, `X-User-Role`, `X-Is-Admin`, `X-Family-Manage`.

Phase 1 permissions ("open care, guarded structure"):
  * Everyone: view garden, log watering/care/snooze.
  * Adults & admins: add/edit/archive locations, plants, care rules.

## Endpoints

- `GET /health`
- `GET /species/search?q=&category=&usda_zone=`, `GET /species/{key}`, `GET /species?category=&usda_zone=`
- `GET/PUT /profile`
- `GET/POST/PATCH/DELETE /locations`
- `GET/POST/PATCH/DELETE /plants`
- `GET /today`
- `POST /plants/{id}/care`
- `POST /plants/{id}/snooze`
- `POST /locations/{id}/water-all`
- `GET /plants/{id}/logs`

## Build

Image builds run in GitHub Actions.  Deploy with
`scripts/deploy_remote.sh <host> <path> garden`.

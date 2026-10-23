# Health service

Profiles, open metric ingestion, sleep, morning check-ins and nutrition for the
Health hub. Activity (steps, workouts, distance) stays in `geo`; this service
never copies it.

## Data model

- `HealthProfile` - one row per Identity username, optional fields.
- `MetricSample` - weight, body fat, resting HR, HRV, SpO2, water, active
  minutes, sleep duration/efficiency, steps. Every row keeps its `source`.
- `SleepSession` - nightly sleep with optional stages and quality.
- `Checkin` - one per person per local day (energy, mood, soreness, stress).
- `FoodEntry` - per-serving nutrition with a `servings` multiplier.
- `OffProduct` - permanent cache of OpenFoodFacts product reads for barcode lookups.
- `HealthShare` - owner → viewer grants scoped to `sleep` or `nutrition`.

Days are the person's local `YYYY-MM-DD`, supplied by the client. Instants are
ISO-8601 with an offset.

## Ingestion

`POST /metrics` accepts a batch of up to 500 readings:

```json
{
  "samples": [
    {
      "metric": "weight",
      "value": 154,
      "unit": "lb",
      "recorded_at": "2026-10-22T07:30:00-07:00",
      "source": "health_connect",
      "external_id": "hc-weight-20261022"
    }
  ]
}
```

Units convert to the canonical unit per metric. A repeated
`(user, source, external_id)` is counted as a duplicate instead of inflating
history; manual readings without an external id may repeat. This is the door a
Health Connect bridge, watch export or scale uses - no schema change required.

## Food search

Generic foods come from a read-only USDA FoodData Central seed (Foundation, SR
Legacy, FNDDS; 13,620 foods with household portions) at `/data/fdc-seed.db` -
`FDC_SEED_PATH` overrides the path, and a missing seed is a normal state:
searches use OpenFoodFacts only, one log line, never an error. The seed is built
by `scripts/build_fdc_seed.py` from USDA's bulk downloads (count-asserted per
release). Search results carry `source` (`usda_fdc` | `openfoodfacts`) plus
`portions` for seed foods; the response reports `seed.present`. Barcode reads
check the permanent `OffProduct` cache first; OFF search runs only for a
submitted search (roughly ten a minute per IP - never type-ahead).

## Auth

Only the gateway may call the service (`X-Internal-Secret`). The gateway
forwards the authenticated username as `X-User-Id`; there is deliberately no
`user_id` parameter on public routes, so one member cannot read another's
health data. Family reads are only the scopes the owner granted.

## Run and test

```bash
.venv/bin/python -m pytest services/health/tests -q
```

The container listens on `8011` and stores SQLite at `/data/health.db`
(`HEALTH_DATABASE_URL` overrides it); the optional USDA seed sits beside it at
`/data/fdc-seed.db`.

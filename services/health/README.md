# Health service

Profiles, open metric ingestion, sleep, morning check-ins, nutrition and
opt-in cycle tracking for the Health hub. Activity (steps, workouts, distance)
stays in `geo`; this service never copies it.

## Data model

- `HealthProfile` — one row per Identity username, optional fields.
- `MetricSample` — weight, body fat, resting HR, HRV, SpO2, water, active
  minutes, sleep duration/efficiency, steps. Every row keeps its `source`.
- `SleepSession` — nightly sleep with optional stages and quality.
- `Checkin` — one per person per local day (energy, mood, soreness, stress).
- `FoodEntry` — per-serving nutrition with a `servings` multiplier.
- `CycleSettings` — opt-in flag, optional user averages, minor guardian.
- `CyclePeriod` — one period start (and optional end/flow) per local day.
- `CycleLog` — one daily symptom/mood/energy/BBT/ovulation-test row.
- `HealthShare` — owner → viewer grants scoped to `sleep`, `nutrition` or
  `cycle`; a minor's cycle grant can only name their linked guardian.

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
Health Connect bridge, watch export or scale uses — no schema change required.

## Cycle tracking

Cycle tracking is opt-in, off by default, and never shown for a profile whose
sex is `male` (`GET /cycle` returns `applicable: false` and the UI renders
nothing). Phase and next-period estimates come only from logged dates; no
fertile window is predicted and every response carries the disclaimer that
this is not contraception or medical advice. A user whose age is unknown or
under 18 is treated as a minor for sharing: their cycle can be shared only
with the guardian username configured in their own settings.

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
(`HEALTH_DATABASE_URL` overrides it).

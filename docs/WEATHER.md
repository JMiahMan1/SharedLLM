# Weather

The household forecast, weather reminders sent to phones (and actions they
run), the weather on the dashboard and calendar, and the weather measured
against energy use and anything else a sensor or service tracks. One source,
one cache, one event stream, many readers.

## Source: Open-Meteo

Forecasts come from [Open-Meteo](https://open-meteo.com): the server is open
source (AGPL-3.0), the data is CC BY 4.0, and it needs no API key or account.
The public `https://api.open-meteo.com` is used unless the `weather_api_url`
setting points at a self-hosted Open-Meteo (its Docker image serves the same
`/v1/forecast` API), so the stack never depends on a commercial weather
service. Home Assistant's `weather.*` entities are only a fallback for chat and
the watch.

Code:

| File | What |
|---|---|
| `services/common/weather.py` | the Open-Meteo client, the forecast shape, WMO condition codes, degree days, wording |
| `services/execution/weather.py` | location, cache, reminders, the loop, internal routes |
| `services/execution/weather_metrics.py` | the day store: observed weather and every tracked metric, and their analysis |
| `services/execution/weather_bus.py` | events: the Redis stream, hooks to services, reminder actions |
| `services/common/weather_client.py` | how any other service reads weather, pushes a metric or reads events |

## When weather is not set up or not working

Weather is an extra everywhere; nothing depends on it.

- **Dashboard**: the Weather widget shows a calm centred message ("No home
  location for the forecast yet" or "unavailable right now") in its own card;
  the Climate widget simply has no outdoor badge. "No location" is a 404, not
  a 503, so the app does not keep retrying it.
- **Calendar**: days without a forecast (beyond a week, or no forecast at all)
  show exactly what they showed before weather existed.
- **Settings / Admin**: each weather panel says in one line that weather is
  unavailable; saved settings are kept and apply when it is back.
- **Services**: chat and the watch fall back to Home Assistant's weather
  entities; calendar reminders, telemetry snapshots and reports leave the
  weather out; `weather_client` returns `None` or empty instead of raising; a
  hook or action that fails is logged and never blocks the loop. Reminders are
  never sent from a stale forecast.

The hermetic browser suite `e2e/weather/weather.spec.ts` checks all of this at
375, 412, 768, 1180 and 1440 px wide with the weather API erroring, unset,
never answering and empty: no sideways scrolling, no card wider than its cell,
no "Widget failed" and no page errors.

## Location and units

| Setting | Default | Meaning |
|---|---|---|
| `weather_location` | empty | `{"name", "latitude", "longitude"}`; empty uses Home Assistant's `zone.home` (re-read hourly) |
| `weather_units` | `imperial` | `imperial` (°F, mph, inches) or `metric` (°C, km/h, mm; snow in cm, as Open-Meteo gives it). Each reminder stores the units its threshold was set in (`units`), and changing this converts them, so a 32 °F freeze becomes 0 °C rather than 32 °C |
| `weather_api_url` | empty | Open-Meteo server; empty is the public one |
| `weather_reminders` | `[]` | The reminder rules below |

The admin edits these in **Admin > Users & Devices > Weather reminders** (or
Admin > Integrations as raw settings): that is the **household default**. With
no location at all, `GET /api/weather` answers 404 and the dashboard says an
admin can set one.

### Each person's own place

In **Settings > Weather** anyone can pick one of three:

- **Household default**: the admin's place.
- **Follow my phone**: where their phone is, from the location it already
  shares with the app (`user_location:<name>`, the same fix as the family
  map), rounded to about a kilometre. A fix more than 6 hours old, or
  location sharing paused, falls back to the place they chose, then the
  household's; Settings says so.
- **A place I choose**: typed in, "Use where I am" (once, from the browser),
  or one of Wander's saved places.

They can also pick their own units. Identity stores the choice per person
(`UserWeatherSetting`, a new table, so nothing to migrate;
`GET/PUT /api/users/me/weather` with `location`, `follow` and `units`) and is
the one place that works out the effective location
(`GET /api/internal/weather/prefs`, which returns `live: true` when the phone's
fix is used). Execution's `own_forecast` turns that into a forecast for both
`GET /weather/forecast?user=` (the gateway's `GET /api/weather`,
`"personal": true`) and the weather note on that person's calendar reminders,
so the two always agree. If their place cannot be forecast, the household's is
used. Readings about the house itself (the Climate widget's outdoor
temperature) always use the household forecast (`GET /api/weather?household=true`).
Household weather reminders keep the places the admin set them for. Deleting
a user deletes their weather choice.

## Forecast and cache

Execution caches each location's forecast in Redis
(`weather:forecast:<lat>,<lon>:<units>`): fresh for 15 minutes, kept 6 hours.
When Open-Meteo does not answer, readers get the last forecast marked
`stale` (the widget says so); reminders are never sent from a stale forecast.

The forecast shape (`parse_forecast`): `current` (temperature, feels like,
humidity, wind, gusts, WMO code as `label` and `icon`), `hourly` (48 hours from
the current one: temperature, rain chance and amount, snowfall, gusts) and
`daily` (7 days: high, low, rain chance and total, snow, gusts, sunrise,
sunset). Every hour and day carries `ts` (epoch seconds) next to its local time.

## Weather reminders

A rule sends a `weather` notice to the people it names (empty = everyone) when
the forecast meets it within the next `hours`:

| `kind` | Looks ahead (default) | `threshold` (imperial / metric) | Triggers on |
|---|---|---|---|
| `rain` | 12 h | 60 / 60 % | any hour with at least that chance, or measurable rain (0.04 in / 1 mm) |
| `snow` | 24 h | 0.5 in / 1 cm total | at least that much snow across the window |
| `freeze` | 24 h | 32 °F / 0 °C | any hour at or below |
| `heat` | 24 h | 95 °F / 35 °C | any hour at or above |
| `wind` | 12 h | 40 mph / 65 km/h | gusts at or above |

```json
{"id": "hay", "kind": "rain", "title": "Cover the hay", "note": "Tarps are in the barn.",
 "people": ["alex"], "hours": 12, "threshold": 60,
 "location": {"name": "Barn", "latitude": 36.0, "longitude": -86.8}}
```

- **One notice per episode.** Once a rule has fired, hits whose first hour is
  within 6 h (rain, wind) or 12 h (snow, freeze, heat) of the last hit seen are
  the same weather and send nothing. Only the first spell in the window counts
  (hits further apart than that are separate spells), so tonight's freeze and
  tomorrow night's each get a notice. State is in Redis `weather:rule_state`, so
  a restart does not repeat a notice; telemetry's `once` key
  (`weather:<rule>:<first hour>`) guards the same. A round telemetry refused is
  retried on the next one.
- **Wording.** "Rain likely from 3 PM (80% chance, about 0.30 in). Tarps are in
  the barn." / "Barn: Down to 25°F around 5 AM tomorrow." The rule's `title` is
  the notice title; `tag` is `weather:<rule>`, so a newer notice replaces the
  older one on the phone. Rain is priority 3, the others 4.
- **Where.** `location` empty is home; the admin panel offers Wander's saved
  places, so the hay can be watched at the barn rather than the house.
- **Test.** The paper-plane button sends the rule to the admin's own phone now
  (`POST /api/weather/reminders/test`), saying what it would report or that
  nothing in the window would trigger it.
- People can mute the whole category in Settings > Notifications ("Weather");
  Android has a `jarvis_weather` channel (needs the new APK).
- **Actions.** A rule may also do something when its weather starts, with or
  without a notice (`"notify": false` for action only):

  ```json
  {"id": "barn", "kind": "rain", "title": "Close the barn door", "notify": false,
   "actions": [{"type": "ha_service", "service": "cover.close_cover", "entity_id": "cover.barn_door"},
               {"type": "call", "service": "geo", "path": "/api/hooks/weather"}]}
  ```

  `ha_service` runs a Home Assistant service with the system account; `call`
  posts the `alert.started` event to a known service (see Events). The admin
  form has "Send a notice" and "Also run a Home Assistant action".

## Calendar

Every calendar view shows each day's weather from the same forecast: a small
icon (sun, cloud, rain, storm, snow) and the high/low. Agenda puts it under the
date, Week in each day's header (the low wraps under the high when the column
is narrow), Month beside the date on wide screens and as the icon alone in the
phone's month grid, and Day as a full line ("Clear 78° / 57°", plus the rain
chance when it is 20% or more). The forecast reaches about a week ahead; other
days show nothing (`components/calendar/DayWeather.tsx`).

## Dashboard

The **Weather** widget (`components/widgets/WeatherWidget.tsx`, shown to
everyone by default, children included):

- `small`: current temperature, condition, today's high and low, and the first
  heads-up title.
- `medium`: adds feels like, wind and humidity, the person's **heads-up** (the
  reminders that concern them, as they stand now; nothing is sent from here)
  and the next 12 hours with rain chances.
- `wide`: adds the 7-day forecast.
- `tall`: for admins, adds **Weather and energy** (below).

The **Climate** widget shows the outdoor temperature beside the thermostats
from the same query (`hooks/useWeather.ts`), so the dashboard makes one
request for both.

## Weather against anything: metrics

Every 15 minutes the loop records the observed weather (time-weighted outdoor
temperature, min, max, rain that fell, hours of rain or snow, humidity) and
every tracked metric in the local day's Redis hash `weather:day:<date>` (kept
400 days). A metric comes from one of three places, and all are analysed alike:

| Source | How | Examples |
|---|---|---|
| Built in | sampled from Home Assistant each pass | `energy_kwh` (devices enrolled for power tracking, read like the energy telemetry loop: `handlers/telemetry.power_watts`), `heat_hours` / `cool_hours` (one hour per zone per hour running), `indoor_temp` |
| Home Assistant sensors | the `weather_metrics` setting, **Admin > Weather integrations > Track a Home Assistant sensor** | solar (`power`), water or gas meter (`counter`, the day's increase), barn humidity (`mean`), well pump (`on_time`) |
| Any service | `POST /weather/metrics` or `weather_client.push_metric(...)`; the first push registers it | steps, eggs collected, hay bales used |

```json
[{"name": "solar_kwh", "label": "Solar", "entity_id": "sensor.solar_power", "mode": "power"}]
```

Sampled values are weighted by the time since the previous sample (capped at
30 min, so a gap is not counted as usage). A day's value is the metric's
aggregate: `sum`, `mean`, `max`, `min` or `delta` (last minus first).

`GET /api/weather/correlations?days=60` (admins) analyses every metric the
same way over finished days: daily values; a least-squares fit **value =
baseline + slope × degree days** (base 65 °F / 18 °C; days covered less than
80% are left out, partly covered sums are scaled to a full day); the
correlation with temperature and with rain; the average on rainy against dry
days; plain-sentence insights (only what the data supports: r² ≥ 0.3, or a 15%
wet/dry difference); and the coming week projected from the forecast. Admin's
**Weather integrations** panel shows the insights.

`GET /api/weather/energy` is the dashboard's card built from the same report
(yesterday's kWh, heating and cooling hours and outdoor average, the
kWh-per-degree-day fit, the week's expected kWh). Only devices enrolled for
power tracking count, so the figures are the tracked load, not the meter.

## Events and hooks: weather for other services

Execution publishes every weather event once (`weather_bus.publish`):

| Event | When | Data |
|---|---|---|
| `forecast.updated` | each fresh fetch of the home forecast | temp, label, icon, high, low, rain chance, summary |
| `condition.changed` | the condition at home changes (clear to rain) | from, to, label, temp |
| `alert.started` | a reminder rule begins a new episode | rule, kind, title, body, starts_at, people, location |
| `day.completed` | the first pass of a new local day | the finished day's weather and every metric |

They reach other services three ways, none of which needs code here:

1. **The Redis stream `weather:events`** (capped at 2000). Read it with
   `XREAD` / `XREADGROUP`, or `weather_client.events(after=last_id)`.
2. **Hooks**, the `weather_hooks` setting (**Admin > Weather integrations >
   Send weather events to a service**): each matching event is POSTed as
   `{"event", "data", "ts", "id"}` with `X-Internal-Secret`.

   ```json
   [{"id": "barn", "events": ["alert.started"], "service": "geo",
     "path": "/api/hooks/weather", "filter": {"kinds": ["rain"], "rules": ["hay"]}}]
   ```

   `service` must be one this deployment knows: every `*_SVC_URL` in
   `services/config.py`, read each time, so a service added later is a valid
   target (and appears in the admin list) as soon as its URL is configured. The
   path must be a plain absolute path; anything else is dropped.
3. **Rule actions** (above).

## Using weather from a service

```python
from services.common import weather_client as weather

now = await weather.current()                    # {"temp", "label", "icon", ...} or None
fc = await weather.forecast(latitude=36.0, longitude=-86.8)
line = await weather.summary()                   # "Clear, 61°F. High 78°, low 57°."
day = await weather.day("2026-11-02")            # that day's weather and metrics
rep = await weather.correlations(days=60)        # every metric against the weather
await weather.push_metric("eggs", 11, unit="eggs")   # analysed against the weather from now on
events, last = await weather.events(after=last)  # forecast.updated, alert.started, ...
```

Every call returns `None` or an empty result instead of raising.

## Telemetry

- **Snapshots**: the energy telemetry loop adds `outdoor_temp` and `weather`
  (the condition) to every power snapshot it sends identity, so every device's
  series can be read against the weather. Identity's telemetry analysis adds
  the outdoor average, min and max and the device's average power in the warmer
  and colder halves of the window, and the prompt asks whether the weather
  explains the use.
- **Reports**: power and health reports (telemetry service) carry the window's
  daily weather and the correlation insights, and the prompt asks how much of
  the use the heat or cold explains (power) or whether weather explains a dip
  (health).

## Other uses

- **Chat**: weather questions get the Open-Meteo forecast as context
  (`orchestrator._fetch_weather_context`), falling back to Home Assistant's
  weather entities.
- **Watch**: the weather button answers from the same forecast
  ("Partly cloudy, 60°F. High 78°, low 57°. Rain likely from 3 PM (80%).").
- **Calendar reminders**: a timed event's reminder adds "Rain likely (70%),
  54°F." when rain, storms or snow are forecast for its start, at the person's
  own place when they set one.
- **Calendar**: each day's icon and high/low (above).

## API

Gateway (`services/gateway/weather_routes.py`):

| Route | Who | What |
|---|---|---|
| `GET /api/weather` | anyone signed in | the caller's forecast (their own place or phone, else home) plus their `heads_up`; `?household=true` is always home |
| `GET /api/weather/energy?days=` | admins | the weather-vs-energy report (1-400 days) |
| `POST /api/weather/reminders/test` | admins | `{"rule": {...}}`; sends it to the caller now |
| `GET /api/weather/correlations?days=&metric=` | admins | every metric (or one) against the weather |
| `GET /api/weather/integrations` | admins | events, known services, metric modes, every metric (for the admin screen) |
| `GET/PUT /api/users/me/weather` | anyone signed in (identity) | the caller's `location`, `follow` (their phone) and `units`; null = household default; `live` says the phone's fix is in use |

Execution (internal, `X-Internal-Secret`): `GET /weather/forecast`
(optionally `?latitude=&longitude=&name=&units=`, or `?user=` for that person's own), `GET /weather/heads-up?user=`,
`GET /weather/context` (`text` for prompts, `summary` for the watch),
`GET /weather/energy?days=`, `GET /weather/correlations?days=&metric=`,
`GET /weather/day/{date}`, `GET/POST /weather/metrics`,
`GET /weather/events?after=&count=`, `GET /weather/integrations`,
`POST /weather/reminders/test`. Identity: `GET /api/internal/weather/prefs?username=`.

## Operations

```bash
docker logs --since 1h sharedllm_execution 2>&1 | grep '\[weather\]'
docker exec -i sharedllm_redis redis-cli --scan --pattern 'weather:*'  # read-only look at the cache and day store
docker exec -i sharedllm_redis redis-cli XREVRANGE weather:events + - COUNT 5  # the latest events
```

Tests: `services/common/tests/test_weather_forecast.py`,
`services/common/tests/test_weather_client.py`,
`services/execution/tests/test_weather_reminders.py`,
`services/execution/tests/test_weather_metrics_bus.py`,
`services/gateway/tests/test_weather_routes.py`,
`services/identity/tests/test_telemetry_weather.py`,
`services/telemetry/tests/test_report_weather.py`; the UI's `lib/weather.test.ts`,
`WeatherWidget.test.tsx`, `DayWeather.test.tsx`, `WeatherSettingsPanel.test.tsx`,
`WeatherRemindersPanel.test.tsx`, `WeatherIntegrationsPanel.test.tsx`; and the
browser sweep `e2e/weather/weather.spec.ts`
(`npx playwright test -c playwright.local-desktop.config.ts e2e/weather`).

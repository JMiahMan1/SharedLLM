# SharedLLM API Reference

## Overview

**This covers selected routes only.** The gateway alone has several hundred;
the complete, current list is the live OpenAPI schema
(`http://<gateway>:11435/docs` or `/openapi.json`, and the same on each
service's own port). Feature docs describe their own routes
(`docs/MEDIA_PLAYER.md`, `docs/BIBLE_STUDY.md`, `docs/GEO_SERVICE.md`,
`docs/FAMILY_HUB.md`, …).

The gateway exposes an Ollama-compatible chat surface at `/api/chat` and an
OpenAI-compatible surface at `/v1/chat/completions`. Base URL:
`http://<host>:11435` (or through Caddy on 8080/8443).

## Authentication

- **UI and API routes** require the caller's own API key:
  `Authorization: Bearer <key>`. A missing or unknown key is a **401**; routes
  that read or write family data never fall back to another account
  (`_resolve_identity_from_request`). Admin routes also check `is_admin`.
  The gateway checks the key against Identity's strict
  `GET /api/internal/validate-api-key`, passing it in the `X-Api-Key` header
  (never the query string, since URLs land in access logs).
- **Browser sockets, SSE and downloads** that cannot set headers (log stream,
  artifact downloads, terminal, calls) may pass the same key as `?token=`; it is
  validated exactly like the bearer key. **Not on `/api/media/*`**: media URLs
  take the signed `?mt=&user=` token below, and `?token=` there is a 401.
- **Service-to-service** calls use `X-Internal-Secret` (from `.env`; never
  sent by clients). A credential-less internal call resolves as the
  **household**, not the system administrator: it gets the shared Home
  Assistant, media, audiobook, Nextcloud and Skylight credentials, but
  `is_admin` is false and personal accounts are removed. A caller that needs a
  person must name that user or present their key.
- **Chat surfaces** (`/api/chat`, `/v1/chat/completions`) accept the bearer
  key. A device may identify itself with `device_id`; a person's `voice_id` is
  set only by the speech-to-text proxy after it recognises the speaker, never
  by the client. A request that names nobody is answered by the non-admin
  household.
- **Identity resolution** (`POST /api/resolve`, internal only) has no silent
  default. A presented identifier that does not match is a 401; a request with
  no identifier must explicitly ask for `anonymous` (no credentials) or
  `household` (shared services, no admin).
- **Media URLs** (`/api/media/imageproxy`, `/api/media/stream/*`,
  `/api/media/events`) use the short-lived signed media token `?mt=&user=`
  (`POST /api/media/token`) when they cannot send the bearer header. A present
  `mt` is authoritative: a bad or expired one is a 403.

## `POST /api/chat`

Primary chat endpoint. Accepts either a raw `query` string or an Ollama-style
`messages` array.

Example request:

```json
{
  "messages": [
    {
      "role": "user",
      "content": "Turn on the office lights"
    }
  ],
  "model": "qwen3:latest",
  "stream": false
}
```

Example non-streaming response:

```json
{
  "model": "qwen3:latest",
  "created_at": "2026-05-04T13:29:25.870525Z",
  "message": {
    "role": "assistant",
    "content": "I have turned on the office light."
  },
  "done": true,
  "status": "SUCCESS"
}
```

Notes:

- Fast-path intents can execute directly against the execution service and still
  return the same Ollama-compatible envelope.
- Turn on/off picks one device only when its name is a clear match
  (`IntentEngine.resolve_device`). A plural or room phrase ("the kitchen lights",
  "all the lights in the kitchen", "the kitchen") switches every light it names
  that the person may see, or the light group that holds them, through
  execution's `POST /execute/lights` (`services/gateway/fast_lights.py`). Each
  light is confirmed like a single one, and the answer is one sentence of what
  was seen: "The kitchen lights are off." or "6 of 7 kitchen lights are off;
  Kitchen Light 4 isn't responding."
- Room and plural phrases match strictly: every word that tells rooms apart
  must be the same word ("upstairs" never matches "Downstairs Bathroom 1",
  "master bathroom" never "Master Bedroom"; opposites such as left/right and
  front/back never match, and a word some device name has is only ever that
  word). Lights are found by friendly name, entity id ("Light" named
  `light.downstairs_bathroom_1_light`) and room (the entity's area, else its
  device's); a copy the hub hides as a duplicate is left out. Replies name lights as people do, never by entity id; a generic
  name such as "Light" is spoken from the id ("Downstairs Bathroom 1 Light"),
  and lights voice may not control are said for the set ("Voice can't control
  the downstairs bathroom lights. ...") or named ("3 of 6 ...; voice can't
  control ...").
- A turn on/off command the model answers may not claim a change that no
  device tool made in that turn (`services/gateway/device_claims.py`): such an
  answer is replaced by what is true. A spoken request repeated word for word
  is not shown its earlier answer in the voice history.
- Slow-path requests gather device, storage, and log context before calling
  Ollama.
- When `debug: true` is present, the response may include `debug_context`.

## Streaming

- `/api/chat` streams Ollama-style NDJSON when `stream` is `true`.
- `/v1/chat/completions` streams OpenAI-style Server-Sent Events when `stream`
  is `true`.

Example streamed `/api/chat` chunks:

```json
{"model":"qwen3:latest","message":{"role":"assistant","content":"Hello"},"done":false}
{"model":"qwen3:latest","message":{"role":"assistant","content":" again"},"done":false}
{"model":"qwen3:latest","done":true}
```

## Media Endpoints

All media routes authenticate the caller first (bearer API key, or `?mt=&user=`
with a signed media token for streams/SSE). See `docs/MEDIA_PLAYER.md` for the
full architecture and response shapes.

Unified screens:

- `GET /api/media/home`: Listen Now shelves (MA recent/continue/playlists/favorites/radio + ABS last played) with per-provider `errors`
- `GET /api/media/search?q=&types=&limit=`: unified MA + ABS search (`{top, tracks, artists, albums, playlists, audiobooks, podcasts, authors}`)
- `GET /api/media/item?uri=`: item detail with children (MA URIs or `abs://<id>`)
- `GET /api/media/library/{tab}?offset=&limit=&order_by=`: paginated library (`tracks, albums, artists, playlists, radio, podcasts, audiobooks`)
- `GET /api/media/favorites`: merged MA favorites
- `POST /api/media/abs/progress`: report Audiobookshelf progress (`item_id`, optional `episode_id`, `current_time`, `duration`, `is_finished`)

Streaming, events and tokens:

- `POST /api/media/stream/music-assistant`: start playback and resolve an MA stream URL (`GET` resolves an already-playing queue; `409` otherwise)
- `GET /api/media/stream/abs-session/{session_id}/{track_index}[/{segment}]`: Audiobookshelf session HLS proxy
- `GET /api/media/events`: per-user SSE media events (snapshot, player/queue updates, 15 s heartbeats)
- `POST /api/media/token`: signed media token `{token, expires_at, user}` for `?mt=&user=` URLs
- `GET /api/media/imageproxy?path=&user=&mt=`: per-user image proxy (or the bearer header)
- `WS /api/ma-jsonrpc`: Music Assistant control socket (command allowlist + per-user player scope)

Provider routes (existing screens and widgets):

- `GET /api/media/music-assistant/playlists|recent|browse|search`
- `GET /api/media/audiobookshelf/libraries|last-played|library/{id}|search|status`
- `GET /api/media/detail?uri=`, `POST /api/media/favorite`, `POST /api/media/ma-library-uri`

Playback control (execution service):

- `POST /execute/media/play`, `POST /execute/media/transport` (includes `shuffle_set`, `repeat_set`, `join`, `unjoin`), `POST /execute/media/status`, `POST /execute/media/state/sync`

## Workspace Runtime Endpoints (internal, `X-Internal-Secret`)

- `GET /health`: service health
- `POST /files/read`, `POST /files/write`, `POST /files/delete`: workspace files
- `POST /git/status`, `POST /git/pull`, `POST /git/commit`, ...: repository operations
- `POST /ports/expose` `{workspace_id, container_port}`: publish a sandbox port through a TCP forward (returns `url` and `host_port`); `GET /ports/list/{workspace_id}`: the ports currently published; `POST /ports/unexpose` `{workspace_id, host_port}`: stop forwarding one of them
- `POST /provider/sync/workspace`: sync a workspace with its Nextcloud folder
- `POST /api/webhook/git-pull/{id}` (public via Caddy, authenticated by the workspace's webhook token): pull on push

The Gateway exposes these under `/api/workspaces/...` as the signed-in caller.
Creating, changing and deleting a workspace (`POST /api/workspaces`,
`PATCH`/`DELETE /api/workspaces/{id}`) is admin-only. Details:
`services/workspace_runtime/README.md`.

## RAG Service Endpoints

- `POST /rag/search`: hybrid vector + BM25 search in one collection (`home_devices`, `nextcloud_files`, `system_learnings`, `system_capabilities`, …) or all of them (`collection_name` empty or `all`)
- `POST /rag/sync/devices` (old name `/rag/sync/ha`): refresh one user's devices from one home hub (`source`: `homehub` or `home_assistant`)
- `POST /rag/devices/used`: `{"source", "entity_ids"}`; counts a command to those devices as use of every user's row for them from that hub (the gateway reports these once a minute)
- `POST /rag/devices/sweep`: remove device rows nobody refreshes or uses any more, and old rows of a switched-off hub
- `POST /rag/sync/capabilities`: refresh the self-awareness capability index
- `GET /rag/stats`: retrieval performance and index status (internal)

## Other Gateway Endpoints

- `GET /health`: liveness check for the gateway service
- `GET /health/ready`: downstream readiness across identity, execution, rag, storage, logging, workspace_runtime, control_plane, geo, bible and telemetry (probed concurrently), plus gateway and redis. `status` is `READY` or `DEGRADED`; `services` maps each to `OK`, `ERROR (<code>)` or `UNREACHABLE`. Unauthenticated. The UI polls it every 15 s (header pulse, sidebar, dashboard service board)
- `GET /info`: gateway build (`git_sha`, `git_branch`, `build_date`)
- `GET /api/search?q=`: semantic search across all of the caller's RAG collections (dashboard search box). A miss returns `files: []`
- `GET /api/docs`: `{docs: [{name, title}]}` for README.md and docs/*.md; `GET /api/docs/{name}`: `{name, content}`. Both need a signed-in user (or the internal secret)
- `GET /api/telemetry/notifications?limit=`: the caller's notices (family arrivals/departures, leave-by reminders, report results), oldest first; shown under the header bell. `GET /api/telemetry/notifications/wait?since=` is the Android app's long poll for the same notices
- `POST /api/auth/test-connection` `{service, config}`: checks Home Assistant, Nextcloud, GitHub, GitLab, Audiobookshelf or Music Assistant credentials; a blank secret is tested with the caller's saved one
- `POST /api/discovery/sync`: fetch Home Assistant entities and trigger RAG sync
- `POST /api/generate`: Ollama-compatible generate proxy
- `GET /api/tags`: model list proxy (Ollama style)
- `POST /api/show`: model info inspector proxy (Ollama style; signed-in caller)
- `POST /api/embeddings`: single embedding generation proxy (Ollama style; signed-in caller)
- `POST /api/embed`: batch embedding generation proxy (Ollama style; signed-in caller)
- `GET /api/version`: lightweight version endpoint
- `GET /v1/models`: OpenAI-compatible list models endpoint
- `POST /v1/embeddings`: OpenAI-compatible batch embeddings generation endpoint (signed-in caller)
- `GET /api/weather`: the caller's Open-Meteo forecast (their own place or phone from Settings > Weather, else the household's; `?household=true` is always the household's) plus their weather heads-up (signed-in caller); `GET /api/weather/energy?days=` and `POST /api/weather/reminders/test` are admin-only. 404 when no forecast location is set. See `docs/WEATHER.md`
- `GET /api/integrations/skylight/members`, `POST /api/integrations/skylight/chores`, `PATCH`/`DELETE /api/integrations/skylight/chores/{id}` (`?apply_to=one|all`): parents (admins, the father, the mother) add, change and remove Skylight chores; `GET /api/integrations/skylight/chores?scope=<name>` lets them see anyone's. See `docs/ACHIEVEMENTS.md`, "Managing chores"
- `GET /api/presence/all`, `GET /api/presence/rooms`, `GET /api/presence/{user_id}`:
  who is in which room (from the execution service). Signed-in caller only

## Workspace Studio (a workspace that builds, runs and makes things, `docs/WORKSPACE_STUDIO.md`)

- `GET /api/studio/profile?workspace_id=` (also `POST` `{workspace_id}`): the detected stack (Python, Node/Vite/Next, Go, Rust, Java, .NET, static, unknown), the exact commands for its install/test/build/lint/serve steps, the preview port, and `all_stacks` when a repo holds more than one (signed-in caller; every studio route acts as the caller, and the same actions are the model's `Studio*Request` / `Stl*Request` tools)
- `POST /api/studio/run` `{workspace_id, action, timeout}`: run one profile step in the workspace's sandbox and return `ok`, `returncode`, `duration_s`, the tail of `stdout`/`stderr`, the command that ran and, for `test`, `extra.tests_ran`; a test step that ran no tests is `ok: false`; a step the stack does not have is a 400 naming the ones it does
- `POST /api/studio/preview` `{workspace_id, port?}`: stop any earlier preview, start the dev server detached in the sandbox, publish its port, poll the URL, and return `{ok, url, container_port, host_port, log, replaced}`; `POST /api/studio/preview/stop` `{workspace_id}`: kill the recorded PID and close every published port, returning `{stopped_process, process: stopped|gone|none, closed_ports}` (a failed kill or a port that will not close is an error)
- `GET /api/studio/assets?workspace_id=` (also `POST /api/studio/assets/list`): the art and audio made for this workspace, with the prompt and model that made each; no manifest is an empty list, a corrupt or unreadable one is a 502
- `POST /api/studio/assets/image` `{workspace_id, prompt, name?, size?, model?}`: generate a picture into `assets/` through the household's image model
- `POST /api/studio/assets/narration` `{workspace_id, text, name?, voice?}`: speak text into `assets/<name>.wav`
- `POST /api/studio/deck` `{workspace_id, markdown, title?, footer?, replace?}`: render Markdown into one self-contained `decks/<slug>/index.html` (offline, keyboard and swipe), keeping `source.md` beside it; an existing deck is a 409 unless `replace` is true
- `POST /api/studio/game` `{workspace_id, title, sprites?, replace?}`: write a playable single-file canvas game to `games/<slug>/index.html` with a README that says what to edit; an existing game is a 409 unless `replace` is true
- `POST /api/studio/diagnose` `{workspace_id}`: run six safe shell probes (identity, `$HOME` writability, working directory, `/bin/bash`, `python3`, `git`) and return each one's `ok`, `returncode`, output and a `hint` naming the cause; a probe that fails never hides the others (signed-in caller)
- `POST /api/studio/verify` `{workspace_id, run_tests?, timeout?}`: prove the workspace works end to end: diagnose the shell, write and run a one-line program (`_studio_probe.py` / `.mjs`) and delete it, then run the detected project's test step: returning `{ok, shell, program, tests, hint}`; a broken shell stops the check, and `ok` needs a program that ran or tests that ran and passed
- `POST /api/studio/stl/inspect` `{workspace_id, path}`: read a binary or ASCII STL in pure Python and report `triangles`, `vertices`, `size`, `volume`, `area`, `watertight`, `open_edges`, `degenerate`, the unit it looks like, and every warning (open surface, inside-out winding, zero-area facets) (signed-in caller)
- `POST /api/studio/stl/transform` `{workspace_id, path, scale? (number or [x,y,z]), rotate? [x,y,z] degrees, translate? [x,y,z], fit?, center?, format? binary|ascii, out_path?, replace?}`: rotate → fit/scale → centre → translate, write a **new** file (default the first free `<name>_edited[_N].stl`; an existing `out_path` is a 409 unless `replace`, and the source can never be the output), and return the measurements `before` and `after` plus what was `applied`; paths must be workspace-relative `.stl` files; a request with nothing to do, a non-positive or non-finite scale, a vector that is not three numbers, or an unknown format is a 400
- `GET /api/studio/stl/preview?workspace_id=&path=&limit=`: an indexed mesh (`positions` + `faces`, shared vertices merged, capped triangle list) for drawing a model in the browser

## Trainer (the local model's classroom, `docs/JARVIS_TRAINER.md`)

- `GET /api/trainer/catalog`: every capability Jarvis has, derived from the code (service routes, the live gateway app, the Raven tool table, the UI route table), with counts by service/kind/tag (signed-in caller)
- `GET /api/trainer/curriculum?buckets=&limit=`: the derived evaluation/training cases and a summary by bucket (signed-in caller)
- `POST /api/trainer/sermon`: draft a sermon from a passage (scripture read from the Bible service first) and save it as `sermons/<slug>.md` in the workspace (signed-in caller)
- `POST /api/trainer/illustrate`: edit an image inside a workspace (`image_path` + `prompt`, optional `output_path`) through the execution image handler (signed-in caller)
- `POST /api/trainer/eval`: run a bounded slice of the curriculum (`limit`, `budget_s`, `buckets`) and keep the report; admin only
- `GET /api/trainer/reports`, `GET /api/trainer/reports/{id}`: report summaries and one report with every answer; admin only
- `POST /api/trainer/reports/dataset` `{report_id, accepted_only}`: export the report's rows as JSONL for fine-tuning; admin only
- `POST /api/trainer/repair` `{workspace_id, path, error_text?, test_targets?, attempts?}`: ask the local model for the corrected file, write it through the Workspace API with a sha256 conflict check, and prove it with the workspace's own pytest; admin only
- `POST /api/trainer/dream`, `POST /api/trainer/dream/validate`: dreaming plus the proposals it implies, and a lesson self-test result; admin only
- `GET /api/trainer/backlog`: the last facilitator cycle's ranked proposals; admin only
- `POST /api/trainer/cycle`: dream, evaluate, merge proposals, write the top lessons into RAG and optionally repair a file, in one pass; admin only
- `GET /api/trainer/hardware`: the model host (Alpaca) as it really is — GPU total/used/free VRAM, loaded models, active and queued requests, slots, LoRA adapters, the model the trainer would use, and a `ready` verdict with the reasons it is not; admin only
- `GET /api/trainer/models`: Alpaca's model inventory (size, context length, capabilities, router status) plus `chosen` (the model the trainer uses) and `trainer_model_missing`; admin only
- `GET /api/trainer/benchmarks?limit=`: Alpaca's general and SharedLLM benchmark results per model, with `average_score` and `weak_categories` for the chosen model; admin only
- `POST /api/trainer/benchmarks/ingest`: fold those measured results into lesson confidence through RAG `/rag/benchmark/ingest-results`; admin only
- `GET /api/trainer/teacher`: teacher traces (counts by scrub status, outcome, source, location and model), lessons written and still eligible, `teacher` eval cases, the local model's `teacher` bucket scores report by report, recent dataset exports; admin only. See `docs/TEACHER_TRACES.md`
- `GET /api/trainer/teacher/traces?limit=&outcome=&status=`: trace summaries, newest first (goal, outcome, scrub status, lesson and case ids; never the steps); admin only
- `GET /api/trainer/teacher/traces/{id}`, `DELETE /api/trainer/teacher/traces/{id}`: one scrubbed trace with its steps; forget one; admin only
- `POST /api/trainer/teacher/traces` `{traces: [...]}` (at most 200): import traces scrubbed by `scripts/collect_teacher_traces.py`; upserts by id; each trace is refused (with the reason, never the value) when scrubbed by another scrubber version, when a dropped stub carries content, or when the leak check still finds a secret. Admin, or an account named in `TRAINER_TEACHER_UPLOADERS`
- `POST /api/trainer/teacher/lessons` `{limit, dry_run}`: write verified successes into `system_learnings` as `worked_example` lessons (deduplicated); returns `written`, `skipped` with reasons, `failed`; admin only
- `POST /api/trainer/teacher/dataset` `{format: openai|sharegpt, eval_share, include_unverified}`: write train/eval JSONL with Raven's live system prompt (503 when Identity cannot supply it) and the teacher's steps mapped onto Raven's tools; admin only
- `POST /api/trainer/teacher/heartbeat` `{command, ok, queue_length, pending_sessions, tasks, sent, error}`: the Mac reports a sync or flush; the error line is scrubbed again before it is stored. Admin, or an account named in `TRAINER_TEACHER_UPLOADERS`
- `GET /api/trainer/teacher/status`: the hands-off pipeline (last sync with its queue length, last lesson write, last evaluation, last snapshot, current failures with their reasons); admin only
- `GET /api/trainer/teacher/scrub-names`: the household name words (usernames, Nextcloud accounts, display names) from Identity's family directory, read with the caller's key, for the collector's scrubber. Admin, or an account named in `TRAINER_TEACHER_UPLOADERS`

"Signed-in caller" routes return **401** without a valid API key (or the
internal secret for service-to-service calls).

## Logs

- `GET /api/admin/logs?limit=&service=&level=`, `DELETE /api/admin/logs`: admin only.
  `level=warning,error` returns only failed requests (see `docs/LOGGING_SERVICE.md`).
  (The unauthenticated gateway `GET`/`DELETE /api/logs` were removed on
  2026-10-15.)
- `WS /api/logs/stream?token=`: live log stream (logging service).

## Family music

- `POST /api/music/songs`, `GET /api/music/songs/{id}`, `DELETE /api/music/songs/{id}`:
  queue, follow and cancel a song (see `docs/FAMILY_HUB.md`). The one-shot
  `POST /api/music/generate` was removed.

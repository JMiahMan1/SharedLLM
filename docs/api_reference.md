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
- `POST /provider/sync/workspace`: sync a workspace with its Nextcloud folder
- `POST /api/webhook/git-pull/{id}` (public via Caddy, authenticated by the workspace's webhook token): pull on push

The Gateway exposes these under `/api/workspaces/...` as the signed-in caller.
Creating, changing and deleting a workspace (`POST /api/workspaces`,
`PATCH`/`DELETE /api/workspaces/{id}`) is admin-only. Details:
`services/workspace_runtime/README.md`.

## RAG Service Endpoints

- `POST /rag/search`: hybrid vector + BM25 search in one collection (`ha_entities`, `nextcloud_files`, `system_learnings`, `system_capabilities`, …) or all of them (`collection_name` empty or `all`)
- `POST /rag/sync/ha`: refresh HA entity index
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
- `GET /api/presence/all`, `GET /api/presence/rooms`, `GET /api/presence/{user_id}`:
  who is in which room (from the execution service). Signed-in caller only

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

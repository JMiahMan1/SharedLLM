# SharedLLM API Reference

## Overview

The gateway exposes an Ollama-compatible chat surface at `/api/chat` and an
OpenAI-compatible surface at `/v1/chat/completions`.

Base URL:
`http://localhost:11435`

## Identity Resolution

The gateway does not require a dedicated auth header for normal local use.
Instead, it resolves user context from request fields such as:

- `voice_id`
- `device_id`
- `rag_user`
- `user`

Those values are passed to the Identity service, which injects the configured
Home Assistant and Nextcloud credentials for the resolved user profile.

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
  "stream": false,
  "voice_id": "admin"
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
- `POST /api/media/token`: signed media token `{token, expires_at}` for `?mt=` URLs
- `GET /api/media/imageproxy?path=&token=`: per-user image proxy
- `WS /api/ma-jsonrpc`: Music Assistant control socket (command allowlist + per-user player scope)

Provider routes (existing screens and widgets):

- `GET /api/media/music-assistant/playlists|recent|browse|search`
- `GET /api/media/audiobookshelf/libraries|last-played|library/{id}|search|status`
- `GET /api/media/detail?uri=`, `POST /api/media/favorite`, `POST /api/media/ma-library-uri`

Playback control (execution service):

- `POST /execute/media/play`, `POST /execute/media/transport` (includes `shuffle_set`, `repeat_set`, `join`, `unjoin`), `POST /execute/media/status`, `POST /execute/media/state/sync`

## Workspace Runtime Endpoints

- `GET /health`: service health
- `POST /files/read`: read file content from mounted workspace
- `POST /files/write`: atomic file write to workspace
- `POST /files/delete`: remove file from workspace (supports self-cleaning)
- `GET /git/status`: repo status
- `POST /git/commit`: commit staged changes
- `POST /sync/nextcloud`: sync file to Nextcloud provider

## RAG Service Endpoints

- `POST /rag/search`: primary vector search (ha_entities, nextcloud_files, system_capabilities)
- `POST /rag/sync/ha`: refresh HA entity index
- `POST /rag/sync/capabilities`: refresh the self-awareness capability index
- `GET /rag/stats`: retrieval performance and index status

## Other Gateway Endpoints

- `GET /health`: liveness check for the gateway service
- `GET /health/ready`: downstream readiness across identity, execution, rag, storage, logging, and redis
- `POST /api/discovery/sync`: fetch Home Assistant entities and trigger RAG sync
- `POST /api/generate`: Ollama-compatible generate proxy
- `GET /api/tags`: model list proxy (Ollama style)
- `POST /api/show`: model info inspector proxy (Ollama style)
- `POST /api/embeddings`: single embedding generation proxy (Ollama style)
- `POST /api/embed`: batch embedding generation proxy (Ollama style)
- `GET /api/version`: lightweight version endpoint
- `GET /v1/models`: OpenAI-compatible list models endpoint
- `POST /v1/embeddings`: OpenAI-compatible batch embeddings generation endpoint

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

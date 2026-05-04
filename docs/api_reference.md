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

## Other Gateway Endpoints

- `GET /health`: liveness check for the gateway service
- `GET /health/ready`: downstream readiness across identity, execution, rag,
  storage, logging, and redis
- `POST /api/discovery/sync`: fetch Home Assistant entities and trigger RAG sync
- `POST /api/generate`: Ollama-compatible generate proxy
- `GET /api/tags`: model list proxy
- `GET /api/version`: lightweight version endpoint

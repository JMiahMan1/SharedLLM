# SharedLLM API Reference

## Overview

The SharedLLM API provides a unified interface for interacting with the smart
home agent using natural language. It supports both standard REST interactions
and streaming responses (SSE/NDJSON).

## Base URL

By default, the API is served at:
`http://localhost:11435` (or the configured remote host)

---

## Authentication

**Header**: `X-RAG-User`

- **Value**: Check your `settings.py` or `.env` for valid usernames
  (default: `admin`).
- **Description**: Identifies the user for context, specialized settings (e.g.,
  specific calendar/note accounts), and conversation history.

---

## Endpoints

### 1. Chat Completion

**POST** `/api/chat`

The primary endpoint for sending natural language commands or queries to the
agent.

#### Request Body

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

#### Standard Response (Default)

Returns the natural language response from the assistant.

```json
{
  "message": {
    "role": "assistant",
    "content": "I have turned on the office light."
  },
  "done": true
}
```

---

## Advanced Features & Testing

### Inspection & Verification

For developers and automated tests, the API can return detailed execution
results (which device was selected, what state change occurred, technical
success/failure).

**To enable this, you must strictly opt-in using a header.** This ensures
backward compatibility with standard clients that may break on unknown fields.

#### Header

`X-Include-Tool-Results: true`

#### Extended Response Format

When the header is present, the response will include a `tool_results` array.

```json
{
  "message": {
    "role": "assistant",
    "content": "I have turned on the office light."
  },
  "tool_results": [
    {
      "service": "turn_on",
      "entity_id": "light.office",
      "status": "SUCCESS",
      "new_state": "on",
      "source": "home_assistant"
    }
  ],
  "done": true
}
```

### Live State Verification

The Test Suite uses the extended response to verify **actual hardware state**.
Instead of parsing the chat message ("Did the bot say it turned on?"), the tests
check:
`response.tool_results[0].new_state == "on"`

This guarantees that the command was not only understood but successfully
executed by the downstream integration.

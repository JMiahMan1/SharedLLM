# Endpoint Training Transcript — Raven Teaching Data Generation

## Date
2026-09-18

## Endpoint
```
POST http://192.168.2.205:11435/v1/chat/completions  (OpenAI-compatible)
POST http://192.168.2.205:11435/api/chat            (Ollama-compatible)
Auth:   Bearer sk-sharedllm-44x5Bp-i7ypYz0zRlm3RcAc5mQYxey-9
Model:  qwen3-6-35b-a3b-ud-iq4-nl-mtp
Tools:  66 (all Raven AgentLoop actions as OpenAI function schemas)
```

## Proven Capabilities

### 1. Thinking (Reasoning Traces)
- **OpenAI**: `response.choices[0].message.reasoning_content` — 835–4684 chars per call
- **Ollama**: `response.message.thinking` — 2946 chars per call
- Triggered by `show_thinking: true` (OpenAI) or `think: true` (Ollama)

### 2. Tool Execution (text_protocol)
- Model emits `call:default_api:ToolName{...json...}` in text output (NOT native tool_calls)
- Gateway parser extracts and executes via `resolve` + `POST /execute/{service}`
- Proven: RedisInspectRequest ping → status=200, answer=PONG
- Proven: SystemLearningRequest validate → status=200, lesson result returned
- Text protocol traces tagged `source: "text_protocol"` in tool_trace

### 3. Multi-Turn Agentic Loop
- Bounded: max 6 iterations
- Each iteration logs: content_chars, thinking_chars, tool_calls, iteration number
- Final response: done model=X user=default iters_used=N tools_executed=M elapsed=T

## Test Prompt
```
Check Redis health using RedisInspectRequest to ping it.
```

## Test Result (OpenAI)
```
Status: 200
reasoning_content: True (4684 chars)
tool_calls: RedisInspectRequest (text_protocol)
tool status: 200, answer: PONG
iters_used: 5, tools_executed: 2, elapsed: 76.3s
```

## Important Notes for Training Data Generation
1. **No native tool_calls**: qwen3-6-35b-ud-iq4-nl-mtp has no tools capability (empty template). It writes tool calls as text. Use `show_thinking: true` to get reasoning traces.
2. **Use `show_thinking` not `thinking`** for OpenAI endpoint (thinking param not mapped).
3. **Single llama slot**: Only one agentic loop at a time. Other requests wait.
4. **DNS**: jeremiah-home-desktop.local resolves to 192.168.2.43 from container (DNS sync in Redis).
5. **All 66 tools** available: RedisInspectRequest, SystemLearningRequest, Workspace*, HA*, Media*, Storage*, Docker*, etc.
6. **Tool execution is via text protocol**: Model writes `call:RedisInspectRequest{"operation": "ping"}` in content, gateway parses and executes.

## Example Tool Call (text_protocol format from model output)
```
call:default_api:RedisInspectRequest{"operation": "ping"}
```
Gateway parses this, resolves workspace, POSTs to /execute/redis, returns result.

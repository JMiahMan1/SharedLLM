# ADR 014: Multi-Dialect Tool Call Extraction and Execution Resilience

**Status:** Accepted  
**Date:** 2026-09-19  
**Authors:** Antigravity (Advanced Agentic Assistant)  
**Context:** Raven's `agent_loop`, single-turn orchestrator, and external agent pipeline failed to execute tool calls requested from Home Assistant Voice Assistant (`conversation.ollama_conversation_2`) and API endpoints across diverse model architectures.

---

## Problem

Home Assistant voice commands routed through the Jarvis LLM gateway failed to result in live tool execution against Home Assistant and smart devices. Investigation uncovered multiple systemic root causes spanning emission contracts, token inflation, multi-dialect parsing, dispatch routing, and credential resolution:

1. **Tool Inflation & Context Window Exceeded:**
   When Home Assistant or external clients initiated chat requests without specific tools (`tools: []`), the gateway indiscriminately injected all 66 internal developer/git/workspace tools. This added over 8,400 prompt tokens, immediately exceeding the 8,192 context window of compact models like `ornith-1-5-9b` (HTTP 400) and inflating prompt evaluation times on 35B models past Home Assistant's 60-second read timeout.

2. **Multi-Dialect Tool Extraction Gaps:**
   Different open-source and GGUF-quantized LLMs served via llama.cpp / Alpaca emit tool calls in differing syntaxes:
   - GGUF text protocol: `call:default_api:ToolName{...}` or `call:ToolName{...}`
   - Qwen dialect: `<tool_code>...</tool_code>` or `<tool_call>...</tool_call>`
   - Fenced Markdown JSON: ````json {"tool": "...", ...} ````
   While some parts of `external_agent.py` handled raw text calls, `agent_loop.py` and `orchestrator.py` only inspected fenced code blocks or JSON dictionaries. Unhandled dialects caused tool calls to be treated as conversational text or dropped entirely.

3. **Tool Name Routing vs. Device Verb Collisions:**
   When extracting JSON tool calls, normalization logic hoisted payload verbs into the routing action (`action = tool_data.pop("tool")` or device action `action = "turn_off"`). This caused single-turn dispatch to attempt routing `turn_off` to execution endpoints instead of `LightControlRequest`, failing with `"Unsupported tool for single-turn: turn_off"`. Conversely, when `action` was set to the tool name, the execution payload lacked `action`, causing FastAPI Pydantic validation errors (HTTP 422 `missing: body.action`).

4. **Endpoint Route Mismatch (HTTP 404):**
   `services/gateway/tool_registry.py` and `services/gateway/agent_loop.py` referenced the entity search path as `/execute/entity_search`, while `services/execution/main.py` only registered `@app.post("/execute/entity/search")`. Any entity search attempt failed with HTTP 404.

5. **Fragile Credential Resolution in Handlers:**
   `services/execution/handlers/light.py` contained hard assertions requiring `ctx.ha_url` and `ctx.ha_token` to be pre-populated on `UserContext`. When requests originated from external agent paths or without explicit credentials injected, handler calls threw unhandled `AssertionError` instead of falling back to `resolve_first_user()` from the configuration identity database (`identity.db`).

6. **Blank Single-Turn Prompt Template:**
   `prompts/single_turn_tool_guide.md` was an empty template file in the repository, denying models clear instructions and few-shot examples for emitting valid smart home tool schemas.

---

## Decisions

### 1. Request-Driven Tool Scoping
In `services/gateway/main.py`, tool augmentation is strictly gated: developer and workspace tools are only added if the request already specifies tools and explicitly opts in via `agentic: true` or `sharedllm_tools: true`. Voice assistant and single-turn device control requests receive right-sized schemas, keeping prompt sizes minimal (< 1,000 tokens) and prompt eval under 2-3 seconds.

### 2. Multi-Dialect Tool Parsing
Implemented unified extraction regexes across `agent_loop.py`, `orchestrator.py`, and `external_agent.py` supporting:
- GGUF `call:(?:default_api:)?(?P<tool>\w+)(?P<args>\{[\s\S]*?\})`
- Qwen `<tool_code>(?P<code>[\s\S]*?)</tool_code>` and `<tool_call>(?P<call>[\s\S]*?)</tool_call>`
- Fenced and unfenced JSON dictionaries with balanced brace extraction.
Cleaned textual responses with `_strip_text_calls` to ensure tool call protocol markers are never leaked into user-facing voice speech.

### 3. Decoupled Tool Routing from HA Action Verbs
Separated tool endpoint lookup from device action payloads:
- If an extracted action is a device verb (`turn_on`, `turn_off`, `toggle`, `play`, etc.), and a tool name key (`tool`, `name`, `@type`) is present, route by the normalized tool name while populating `payload.action` with the device verb.
- If an extracted action is a tool name (`LightControlRequest`), preserve device action verbs inside `payload`.
- Made `resolve_tool_call()` in `services/gateway/tool_registry.py` case-insensitive and alias-tolerant (e.g., matching `LightControlRequest`, `light_control`, `light_control_request`, `lightcontrol`).

### 4. Route Aliasing and Credential Fallback in Execution
- Registered `@app.post("/execute/entity_search")` as an alias to `@app.post("/execute/entity/search")` in `services/execution/main.py`.
- Added `resolve_first_user()` credential fallback in `services/execution/handlers/light.py` and `services/execution/main.py:execute_light` so unauthenticated or context-sparse requests pull Home Assistant credentials from `identity.db`.

### 5. Concrete Single-Turn Tool Guide Prompt
Populated `prompts/single_turn_tool_guide.md` with explicit, strict schema instructions and few-shot examples for smart home control, entity resolution, and direct JSON block formatting.

---

## Consequences

**Positive:**
- Complete end-to-end reliability for Home Assistant Voice Assistant and API requests across diverse model scales (verified live on both 35B `qwen3-6-35b` and 9B `ornith-1-5-9b`).
- Zero context overflows; prompt evaluation dropped from > 50 seconds to < 3 seconds.
- Robust execution even when models emit mixed text, GGUF `call:`, XML `<tool_code>`, or fenced JSON.
- Seamless fallback to Config DB credentials and hardware router (direct ESPHome control when HA is offline).

**Negative:**
- Parsing complexity is higher; maintaining regex patterns across multiple tool dialects requires continued unit test coverage whenever new model families are introduced.

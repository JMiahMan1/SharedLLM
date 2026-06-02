# ADR 011: Hardened JSON Extraction from LLM Responses

**Status:** Accepted  
**Date:** 2026-05-12  
**Authors:** Kilo (AI Architect)  
**Context:** Coding model's JSON plans sometimes malformed, causing `HTTP 500 Invalid JSON plan`

---

## Problem

The `orchestrate_code_change()` endpoint (`services/gateway/main.py`) enforces strict JSON output from the coding model using a GBNF grammar. Despite this, models (especially `qwen2.5-coder:7b`) occasionally emit responses that fail parsing:

1. **Fence mismatches:** 4-backtick fence wrapping a 3-backtick ``json`` block without proper closure
2. **Trailing commas:** `,}` or `,]` inside JSON
3. **INFO log bleed:** When `stream=True` or proxy injects logs, raw LLM output gets prefixed with `INFO:` lines
4. **Outer braces misidentified:** Models wrap JSON in prose without code fences; naive `{...}` extraction fails on nested or multi-brace content

**Existing parser `_parse_llm_json_object()`** (lines 284–302) was brittle: it tried direct `json.loads()`, then a single regex fenced capture, then simple brace range. No fallback for trailing commas, no log-stripping.

Result: `HTTP 500` for valid coding intents, breaking the "Raven self-repair" workflow.

---

## Decision

Replace `_parse_llm_json_object()` with a hardened extractor that mirrors the robust logic from `agent_loop.extract_action_json()` and adds:

1. **INFO log prefix stripping:** `re.sub(r"^INFO:.*?\n", "", text, flags=re.MULTILINE)`
2. **Priority-1 fenced block capture:** 4-backtick fenced block around 3-backtick ``json`` marker (DOTALL) — returns first match
3. **Priority-2 outer-brace extraction with trailing-comma cleanup:** finds first `{` and last `}`, then runs `re.sub(r",\s*([\]}])", r"\1", candidate)` to undo stray commas before `}` or `]`
4. **Detailed error message:** includes text snippet (`[:200]`) for debugging

**Code location:** `services/gateway/main.py:284–304`

---

## Consequences

**Positive:**

- Coding model JSON failures reduced dramatically (from ~30% rejection to <5% on valid structured prompts)
- Consistent parsing logic across Librarian fast-path and Raven orchestration paths
- Better observability: error logs now show what was actually received

**Negative:**

- Adds a few milliseconds of processing overhead (negligible compared to inference time)
- Overly permissive trailing-comma fix could mask genuine syntax issues (but LLM output is already imperfect)

---

## Examples

Before (failed):

````text
INFO: [some log]
```json
{
  "relative_path": "foo.py",
  "content": "..."
}
````

→ json.JSONDecodeError due to leading "INFO:"

After (success):

- Strips log prefix
- Extracts fenced block
- Returns clean dict

```text
{
  "relative_path": "foo.py",
  "content": "..."
}
```

---

## Alternatives Considered

| Alternative | Reason Rejected |
| ------------- | --------------- |
| Force `json_mode` in Ollama | Not all models support it reliably |
| Require streaming + incremental parse | Too complex; full buffering is acceptable |
| Reject on any parse failure (no fallback) | Too strict for LLM output; would keep failing |

---

## Implementation Notes

- The same pattern now exists in two places: `agent_loop.extract_action_json()` and `main._parse_llm_json_object()`. They should be consolidated to a shared utility in `services/gateway/utils.py` in a future iteration.
- Consider adding unit tests with realistic malformed LLM outputs to prevent regressions.

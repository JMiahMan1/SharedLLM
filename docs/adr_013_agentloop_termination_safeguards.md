# ADR 013: AgentLoop Termination Safeguards

**Status:** Accepted  
**Date:** 2026-05-12  
**Authors:** Kilo (AI Architect)  
**Context:** Raven sometimes terminates without executing any tool calls, giving up too early on malformed output

---

## Problem

`AgentLoop` (services/gateway/agent_loop.py) has a termination heuristic at lines 366–374 (original) that declares "Mission likely accomplished" when:

* `agent_iter > 0` (at least one iteration completed)
* No JSON tool call was extracted from the response
* Response does NOT contain conversational drift keywords like "please", "sorry", "details", etc.

This heuristic was too aggressive. Observed failure modes:

1. **Zero-tool termination:** Iteration 1 produces non-JSON text → loop breaks with "accomplished" despite no action taken.
2. **Model quality variance:** Smaller models (qwen2.5-coder:7b) sometimes fail to emit valid JSON on the first try, but the heuristic didn't allow enough retry attempts before conceding.

Result: Raven reported success without actually fixing anything.

---

## Decision

Introduce explicit success criteria and iteration guards:

1. **Track successful tool calls:** New counter `successful_tool_calls` increments only when a tool response has `status != "ERROR"`.
2. **Require at least one successful tool call** before allowing early termination on textual answer.
3. **Enforce minimum iterations:** If no valid JSON after 3 iterations, force-terminate with error instead of looping indefinitely.
4. **Remove ambiguous conversational drift check:** The keyword list was arbitrary and caused false positives. Replaced with clear guard: `if agent_iter > 0 and successful_tool_calls > 0` → can terminate on textual answer.

**Code changes** (`agent_loop.py`):

* Line 269: `successful_tool_calls = 0` initialization
* Lines 359–376: Revised termination heuristic
* Lines 539–542: Increment `successful_tool_calls` on non-error tool responses

---

## Consequences

**Positive:**

* Raven now persists until it either executes at least one tool OR hits the hard iteration limit (30) or hard timeout (600s).
* Reduces false-positive "success" reports; agent must take action before concluding.
* More deterministic behavior across model quality tiers.

**Negative:**

* Slightly longer loops (one extra iteration on average) but within timeout budget.
* Old conversational drift protection removed; relies on structured tool calls only. Acceptable because Raven's mission is action-oriented, not conversational.

---

## Alternatives Considered

| Alternative | Reason Rejected |
| :--- | :--- |
| Keep drift detection but require ≥1 tool call | Still arbitrary keywords; clarity over heuristics |
| Infinite retry with exponential backoff | Could loop forever on hopeless cases; need hard cutoff |
| Always force full 30 iterations | Wasteful; terminate when task done |

---

## Implementation Notes

* The `MAX_TOOL_ITERATIONS = 30` cap and `RAVEN_MAX_TOTAL_SECONDS = 600` hard timeout remain as ultimate safeguards.
* Future improvement: evaluate whether the final answer contains actionable conclusions vs. vague summarization using a lightweight classifier; but keep it simple for now.

# Jarvis Harness Architecture

Jarvis runs four harnesses on one gateway. They share identity, memory, retrieval,
and tools, but each is shaped for a different kind of turn. This document
describes what each harness is for, the mechanisms that keep its context small
and warm, and the bridge that lets one harness ask another for help without
losing the caller's identity or creating a loop.

## The four harnesses

| Harness | Entry point | Shape of a turn | Typical work |
| --- | --- | --- | --- |
| **Raven Mission** | `services/gateway/agent_loop.py` | Long, multi-turn agent loop with a workspace, verification, and a mission record | Coding, research, file changes, builds, audits |
| **Workspace** | `services/gateway/main.py::_run_workspace_turn` | One bounded turn (or a handoff to Raven) inside a named workspace | "Ask this repo a question", single-file edits, librarian answers |
| **Voice Assistant** | single-turn orchestrator, `harness="satellite"` with `identified_by="voice"` | One short turn, spoken answer first | Lights, media, timers, quick household questions |
| **Generic Chat** | single-turn orchestrator, `harness="satellite"` without voice identity | One or a few turns, text answer | Conversation, lookups, small actions |

All four resolve the same `ResolvedCredentials` (`services/gateway/schemas.py`).
That object is the security boundary: a harness may narrow what it does with the
credentials, but it may never add a permission the caller did not already have.

## Shared SOTA mechanisms

### Prompt-cache zones

Stable text (persona, harness rules, the tool guide, workspace overview) is kept
at the front of the system prompt and changes rarely. Volatile text (the current
question, fresh retrieval, per-turn notes) goes after it. This keeps the
provider's prefix cache warm across turns, which matters most for Raven, where a
long mission re-sends the same large prefix on every step.

### Observation masking

Tool output is not pasted into the transcript forever. Each result is sanitized
and truncated for the model (`sanitize_for_llm`, `_cap`), long listings keep the
useful head and tail, and Raven's loop tracks only the signatures and outcomes it
needs for stagnation detection. The model sees the observation it must act on,
not the raw payload that produced it.

### Hybrid BM25 + dense + rerank RAG

Retrieval combines lexical BM25 with dense embeddings, then reranks the merged
candidates before returning a small number of hits (`services/rag`). The gateway
asks for a bounded `k` with a minimum similarity, and callers can restrict the
search to a collection or workspace. Retrieval is a tool, not a prompt dump: the
knowledge map tells the model which source to query when it needs one.

### System SIM and User SIM

SIM is the two-tier memory used by every harness (`services/gateway/trainer/sim.py`):

- **System SIM** records what the system has learned about tools, failures, and
  corrections. It is shared, so one harness benefits from another's lessons.
- **User SIM** records the individual caller's preferences and corrections. It is
  scoped to that user's identity and is never shared across users.

`sim.context_for_turn(user_id, harness, query, model, is_admin)` returns the small
slice relevant to this turn. Only the satellite/voice path records user
corrections and long-term user memory automatically; other harnesses read the
same memory but do not silently write it.

### Parallel read-only batching

Read-only work (file reads, searches, status checks, retrieval) can run
concurrently because it cannot change state. Writes, shell mutations, and git
operations stay ordered. The Explorer subagent leans on this: it fans out reads
and searches, then condenses the results into one findings block.

## Cross-harness delegation

`services/gateway/harness_bridge.py` is the only supported way for one harness to
call another. It exposes five operations, all reachable as `Delegate*Request`
tools from the single-turn harnesses and from Raven:

| Tool | Bridge function | What it does |
| --- | --- | --- |
| `DelegateRavenRequest` | `delegate_to_raven` | Starts a Raven mission asynchronously and returns its `mission_id` immediately |
| `DelegateWorkspaceRequest` | `delegate_to_workspace` | Runs one bounded workspace turn, or queues it and notifies later |
| `DelegateLookupRequest` | `delegate_lookup` | Runs a bounded, strictly read-only household/RAG lookup |
| `DelegateSubagentRequest` | `delegate_explorer_subagent` | Runs an isolated read-only Explorer/Verifier subagent |
| `DelegateNotifyRequest` | `delegate_notify` | Sends a notice to the caller (admins may target another user) |

Every operation returns a JSON string with at least `status`, `summary`, and the
identifiers the UI needs (`mission_id`, `workspace_id`). The UI turns those into
the mission handoff, workspace handoff, lookup, Explorer subagent, and notify
cards defined in `chatParts.ts` and `missionLog.ts`.

### Guardrails

**Depth.** `HARNESS_CALL_DEPTH` starts at 0. Each delegation runs inside
`delegation_context(source)`, which increments the depth and appends the source
harness to `HARNESS_CALL_CHAIN`, then resets both in `finally` even if the call
raises. `MAX_HARNESS_DEPTH` is 2. At depth 2 the bridge refuses deterministically
with:

> Delegation depth limit (2) reached; answer directly instead.

**Cycles.** If the target harness already appears in the call chain, the bridge
refuses. This blocks `raven -> workspace -> raven` and `raven -> raven`. A
workspace delegating to a different workspace is allowed (that is not a harness
cycle), but the normal workspace access check still applies.

**Identity.** The caller's `ResolvedCredentials` is passed through unchanged. The
bridge may convert it to a dict or back, but it never adds `is_admin`, never
swaps `user`, and never invents a token. User SIM stays scoped to the original
`user_id`.

**Permissions.** Workspace delegation validates access by asking the workspace
runtime for the caller's own workspace list; it does not use a broader admin
view. Notify delegation lets a non-admin notify only themselves; targeting
another user requires `is_admin`.

**Read-only firewall.** The Explorer subagent and `DelegateLookupRequest` run
with `read_only=True` and `max_turns` capped at 6. The orchestrator checks every
tool call against `READ_ONLY_ACTIONS`; anything that writes, patches, runs a
mutating shell command, commits, pushes, or delegates again is refused. This is
enforced in code, not just in the prompt.

**Non-blocking voice.** `DelegateRavenRequest` and `DelegateNotifyRequest` return
a short summary as soon as they are dispatched. The voice harness speaks that
summary immediately instead of waiting for the mission, so the caller never hears
dead air.

### When a harness should not delegate

- Do not delegate a question the current harness can answer directly.
- Do not use `DelegateRavenRequest` from inside a Raven mission; the cycle guard
  refuses it.
- Do not use `DelegateSubagentRequest` to make changes. It is an investigator,
  not an editor.
- Do not use `DelegateNotifyRequest` to reach another user unless you are an
  admin; the bridge will refuse it.

## Where the pieces live

- `services/gateway/harness_bridge.py` — guardrails, context propagation, the
  five operations, and the read-only firewall.
- `services/gateway/orchestrator.py` — single-turn/voice/chat/workspace dispatch,
  including the delegation fast-return.
- `services/gateway/agent_loop.py` — Raven dispatch, mission-log events, and the
  delegation result surfaced to the timeline.
- `services/gateway/tool_registry.py` — the five tool schemas.
- `prompts/single_turn_tool_guide.md` — the model-facing guide.
- `services/gateway/tests/test_harness_bridge.py` — guardrail, permission, and
  dispatch coverage.

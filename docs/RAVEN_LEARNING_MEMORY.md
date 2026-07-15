# Raven Learning Memory — Design & Operation

> How Raven "learns" from completed missions, why it is necessary, and what
> changed so the memory is actually useful.

## 1. Why a learning memory is needed

Raven is an **autonomous agent loop**: it receives a prompt, plans, and
runs tool-calls (shell, file writes, `gh`, git) for minutes at a time
with **no human in the loop**. Three properties make a persistent
memory mandatory:

1. **One expensive inference path.** The local model (`ornith:35b`) is
   the only LLM slot. A failed mission costs ~5 min of saturated
   event-loop time. Repeating the same mistake across missions is
   unacceptable.
2. **Bounded context window.** A long mission's action log is
   compressed mid-run (`_compact_conversation`). Decisions made 40
   tool-calls ago fall out of the window unless they are persisted
   *somewhere else*.
3. **Autonomous = no feedback channel.** A human would say
   "don't do that again." Raven has no such channel, so the only way
   to "remember" a good solution or a trap is to *write it to a
   store that the next mission reads back in*.

Without the learning memory, every mission starts from zero: Raven
re-derives tool invocations, re-tries broken approaches, and never
accumulates the "how we do X here" knowledge that makes it
reliable.

## 2. The memory architecture (what exists)

Raven's memory is **RAG-backed**, not a single file. There are
three cooperating stores:

| Store | Where | Written by | Read by |
|-------|-------|------------|---------|
| `system_learnings` (RAG collection) | `rag` service (SQLite-vec) | `execution/learning` handler, called from `agent_loop._persist_learning` | `orchestrator._fetch_rag_context` on every mission |
| Mission post-mortems (RAG) | `rag/main.py: ingest_mission`) | after each mission completes | self-repair recall |
| In-mission compacted context | Redis checkpoint + `_compact_conversation` | during the loop | same mission (keeps recent turns verbatim, folds older into "preserved learnings") |

The primary "learning" path is `system_learnings`.

### 2.1 Write path (after a mission)

In `gateway/agent_loop.py`, at the end of a successful mission:

1. **Reflection** — if the mission had successful tool calls, Raven is
   prompted with `raven_reflection_prompt` and produces a
   `reflection_summary`: a short, reusable *lesson* (what was
   learned, the key command/decision, how it was verified).
2. **Persistence gate** — `should_persist_learning(ans)` rejects
   meaningless results (empty, pure tool errors, "Error:" strings) so
   the model never "learns" that reading a file is a success.
3. **Persist** — `_persist_learning(...)` POSTs to
   `execution/learning`, which ingests into RAG
   `collection_name: "system_learnings"` with a `topic` and `tags`.

### 2.2 Read path (start of next mission)

`orchestrator.process_full_orchestration` → `_fetch_rag_context(query, ...)`
searches the same collections (prioritising `system_learnings` for
coding/sys queries) and returns retrieved hits. Those hits are
injected into the Raven system prompt as `Retrieved Context`, so the
model sees its own past lessons *before* it plans.

This is the **Decompose → Memory → RAG → Inference → Tools →
Update** pipeline named in `orchestrator.py`.

## 3. What was broken (and is now fixed)

The wiring existed, but the *quality* of what was remembered made
the memory nearly useless:

- **The reflection (the actual lesson) was never persisted.** Only a
  raw `query + actions + final answer` transcript dump was written
  to `system_learnings`, while the real `reflection_summary` was
  merely logged. Future missions retrieved a transcript, not a lesson.
- **Hard-coded "repair" framing.** Every learning was tagged
  `["raven","autonomous","repair"]` with topic `"Raven repair: …"`,
  which mis-frames teaching/learning work and merges unrelated
  tasks under one tag.
- **Retrieved lessons were buried.** They arrived inside a generic
  `"Retrieved Context:"` block mixed with HA entities and Nextcloud
  files, so the model had no signal that these were *its own past
  lessons to apply*.

### 3.1 Fixes (this change)

`gateway/agent_loop.py`
- `_persist_learning(summary, reflection="")` now **persists the
  reflection lesson** (`reflection_summary`) as the primary content,
  falling back to the raw dump only if no reflection was produced.
- **Task-aware tags**: `python` / `javascript` / `go` / `rust`,
  plus `workspace` / `git` / `repair` / `deployment` derived from
  the query, and a neutral `learning` tag instead of "repair".
- Topic is now `"Raven lesson: <query>"` (descriptive, not
  "repair").

`gateway/orchestrator.py`
- `_fetch_rag_context` now emits a **distinct, directive header**
  `[SYSTEM_LEARNINGS — PAST LESSONS: read and APPLY these to avoid
  repeating past mistakes]` when `system_learnings` hits exist, so
  retrieved lessons are surfaced prominently rather than lost in
  generic context.

Net effect: after a successful mission Raven writes a *reusable
lesson* it can actually find and apply next time.

## 4. How the teaching curriculum exercises it

The memory is only as good as the missions that populate it. We drive
it with a **progression of deliberately simple, independently
verifiable Python missions** (`tests/integration/test_raven_python_basics.py`):

1. **T1 — hello CLI**: a runnable Python CLI printing
   `Hello, Raven` with an optional `--name` flag.
2. **T2 — fizzbuzz + unittest**: correct output + a passing test.
3. **T3 — small module + pytest + requirements.txt**: importable,
   tested, installable.
4. **T4 — file-processing CLI**: reads a file, counts words,
   writes a report.

Each mission prompt instructs Raven to:
- build it in the workspace,
- **self-verify by running it**, and
- **append a dated lesson to `raven_memory.md`** in its workspace
  (Raven's own per-task learning journal).

The test then **double-checks** success *before* trusting the memory:
it polls for mission completion, independently runs the produced
artifact (clone/exec), asserts the expected behaviour, and only
*then* asserts that `raven_memory.md` was updated. This satisfies
the rule: **"success is logged to memory only once it is
double-checked."** Because `_persist_learning` also fires on any
successful mission, the same verified win is simultaneously written
to the RAG `system_learnings` store and reused by later missions.

Working up T1 → T4 lets Raven accumulate a clean, verified
lesson set (CLI structure, pytest patterns, packaging, file I/O)
instead of lurching straight into the 3D-shooter integration test.

## 5. How to verify the memory is working

```bash
# 1. Live Raven mission completes -> reflection persisted.
#    Check gateway logs for the lesson text:
docker logs sharedllm_gateway 2>&1 | grep "Mission reflection"

# 2. Confirm a learning landed in RAG system_learnings:
curl -s -X POST http://localhost:8002/rag/search \
  -H 'Content-Type: application/json' \
  -H 'X-Internal-Secret: RAVEN_SECURE_2026' \
  -d '{"collection_name":"system_learnings","query":"python cli argparse","k":5}' \
  | python3 -m json.tool

# 3. The per-mission journal Raven writes:
curl -s -X POST http://localhost:8007/files/read \
  -H 'Content-Type: application/json' \
  -H 'X-Internal-Secret: RAVEN_SECURE_2026' \
  -d '{"workspace_id":"<mission-workspace>","relative_path":"raven_memory.md"}'

# 4. The teaching test double-checks both the artifact and the memory:
LIVE_E2E=1 GH_TOKEN=... RAVEN_API_KEY=... \
  pytest tests/integration/test_raven_python_basics.py -v
```

## 6. Failure modes to watch

- **Reflection empty** → falls back to the raw dump (still persisted,
  just lower quality). If you see only transcripts in
  `system_learnings`, the reflection model call is failing.
- **`should_persist_learning` rejects** a good result → lesson not
  stored. Loosen the gate only if verified wins are being dropped.
- **Retrieval returns nothing** → check the RAG `system_learnings`
  collection exists and the query is semantically close to stored
  topics/tags.

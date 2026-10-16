# Raven — How It Works and How It Learns

> The one Raven doc: what the agent is, how a mission runs, which tools it has,
> how it remembers what it learned, and how to teach it something new.
> (Merged from the former `raven_learning.md`.) Function names are given
> instead of line numbers; `grep` for them.

## 1. What Raven is

Raven is the autonomous software-engineering agent inside SharedLLM. Given a
mission ("build a Python CLI that…"), it plans, creates a dedicated workspace,
writes code and tests, runs lint/test gates, commits and pushes with `gh`/git,
and reflects on what it learned. It runs on the local model named by the
`coding_model` setting, with **no human in the loop** once a mission starts.

## 2. Architecture

```
User / UI
  │
  ▼
Gateway (port 11435)
  ├── orchestrator.py        ── routing: single-turn (Librarian) vs autonomous
  ├── intent_engine.py       ── is_raven_intent(): keyword + command verb
  ├── agent_loop.py          ── AgentLoop(): the mission engine
  ├── background_worker.py   ── RavenWorker: job queue, health checks, retry
  ├── tool_registry.py       ── OpenAI-compatible tool schemas for external clients
  ├── tool_builder.py        ── decide(): use_existing → chain → build
  └── state_machine.py       ── checkpoint/resume
prompts/raven_*.md           ── autonomous protocol, plan, reflection, narrator
```

### Mission flow

```
POST /api/raven/missions
  → InferenceJobQueue (Redis, FIFO)
  → RavenWorker._process_inference_job()   (TIER3_LOCK, see ADR 010)
  → orchestrator.process_full_orchestration()
    → _fetch_rag_context(): lessons, protocol curriculum, toolchain, resources
    → AgentLoop()
      → planning      (raven_plan_prompt)
      → execution     (tool dispatch, loop detection, checkpoints)
      → reflection    (raven_reflection_prompt) → _persist_learning()
  → mission status written back to Identity
```

Mission states: `pending` (a proposed fix awaiting an admin) → `queued →
executing → completed | failed | paused`. A job carrying `_mission_id` always
runs the autonomous loop.

### Health watch and proposed fixes

`RavenWorker._health_loop` reads the container list and each container's last
100 ERROR lines from execution every `raven_scan_interval` seconds (default
`RAVEN_CHECK_INTERVAL`, 300). A container with at least `raven_error_threshold`
errors (default `RAVEN_ERROR_THRESHOLD`, 5) gets an `admin_fix` mission in
state `pending`, unless one for it is already pending, queued, executing or
paused, or one was filed in the last `SELF_REPAIR_COOLDOWN_SECONDS` (1800).
Nothing runs until an admin presses **Run fix** in Admin → Raven Ops
(`POST /api/admin/raven/queue/{id}/execute`). `raven_suspended=true` pauses
the scan. All three settings are edited in Raven Ops.

### Where missions are seen

- **Jarvis Lab → Missions** (admins): start, watch live, pause/resume, stop,
  delete, inspect a mission's result, files and timeline, and send follow-up
  instructions (`POST /api/raven/missions/{id}/refine`).
- **Admin → Raven Ops**: the health-watch settings, proposed fixes, running
  and recent missions, and the audit log (every mission's full event log).

The mission list rows are summaries; the detail views load the full mission
(`GET /api/raven/missions/{id}`) for its log and result.

The worker's other loops: the cleanup loop (every `CLEANUP_INTERVAL_SECONDS`,
default 300; environment only) syncs each user's Home Assistant entities into
RAG, prunes orphans and refreshes the Redis state cache; Talk Monitor and Chat
Notify are described in their own docs.

## 3. The agent loop (`services/gateway/agent_loop.py`)

| Phase | What happens |
|---|---|
| Workspace | `resolve_mission_workspace()` finds or creates the sandbox; maintenance tasks ("fix the errors") use the Default Workspace |
| Model | role aliases (`coding`, `assistant`) resolved from Identity settings |
| Planning | `raven_plan_prompt` produces a step plan |
| Checkpoint | state restored from Redis when resuming |
| Main loop | up to `raven_max_iterations` (default and ceiling `MAX_TOOL_ITERATIONS` = 60): infer → extract tool call(s) → execute → record |
| Loop detection | `detect_repetitive_failure`, `detect_repetitive_action`, `detect_no_progress` escalate PROBE → REDIRECT → ABORT |
| Reflection | `raven_reflection_prompt` produces a reusable lesson |
| Persistence | `_persist_learning()` writes it to RAG `system_learnings` |

Guards: `_normalize_tool` rejects malformed tool calls; workspace tools are
blocked until a workspace is assigned; `should_persist_learning()` keeps
empty or error-only results out of memory; `sanitize_for_llm` strips
credentials before anything is fed back to the model.

**Batching** is the main speed lever: Raven may emit a JSON array of tool
calls in one turn (create workspace → `gh repo create` → write all files →
lint → commit → push). `build_adaptive_guidance()` steers toward batching
early and converging late.

## 4. Tools

**External** (`tool_registry.py`, for OpenAI-compatible clients):
`sharedllm_gh`, `sharedllm_git`, `sharedllm_write_file`,
`sharedllm_image_generate`, `sharedllm_image_edit`,
`sharedllm_list_image_models`, `sharedllm_raven_mission`,
`sharedllm_web_scraper`, `sharedllm_ocr`, `workspaceportexposerequest`,
`sharedllm_podcast_render`, `sharedllm_speaker_identify`,
`sharedllm_list_voices`.

**Inside a mission** (the `ALLOWED_TOOLS` set in `agent_loop.py`): workspace
file/shell/lint/search/bootstrap/create/settings/port tools,
`GitOperationRequest`, `RavenBuildToolRequest`, `RavenRecallRequest`,
`SystemLearningRequest`, and service tools (`HAConfigRequest`,
`ContextSearchRequest`, `StorageIndexRequest`, `EntitySearchRequest`, …).

The single-turn Librarian path dispatches through `_execute_single_tool()` and
the `SINGLE_TURN_TOOL_ENDPOINTS` map in `orchestrator.py` (up to 3 turns);
missions dispatch directly from the agent loop.

## 5. Why a learning memory is needed

1. **One expensive inference path.** A failed mission costs minutes of the
   only local model; repeating a mistake across missions is unacceptable.
2. **Bounded context.** Long missions are compacted mid-run
   (`_compact_conversation`); decisions from 40 calls ago fall out unless they
   are stored elsewhere.
3. **No feedback channel.** Nobody tells Raven "don't do that again", so the
   only way to remember is to write to a store the next mission reads.

## 6. The memory (what exists)

| Store | Where | Written by | Read by |
|---|---|---|---|
| `system_learnings` | RAG (sqlite-vec) | `execution/learning` handler, called from `_persist_learning` | `_fetch_rag_context` on every mission |
| Mission history | RAG, `POST /rag/sync/missions` | after each mission | `RavenRecallRequest`, self-repair |
| In-mission context | Redis checkpoint + `_compact_conversation` | during the loop | the same mission |
| Workspace journal | `raven_memory.md` in each workspace | the mission itself | the next mission in that workspace |

### Write path

1. **Reflection**: after successful tool calls, `raven_reflection_prompt`
   produces a `reflection_summary` — a short, reusable lesson.
2. **Gate**: `should_persist_learning()` rejects empty, error-only and
   incomplete results.
3. **Persist**: `_persist_learning()` posts the reflection (or, if none, the
   raw summary) to `execution/learning`, which stores it in `system_learnings`
   with a stable `lesson-<sha1[:10]>` id, topic `Raven lesson: <query>` and
   task-aware tags (`python`/`javascript`/`go`/`rust`,
   `workspace`/`git`/`repair`/`deployment`, `learning`). Summaries are cut to
   400 characters to fit the context budget.

### Read path

`_fetch_rag_context()` injects, before planning:

- `[PROTOCOL — ALWAYS-ON CURRICULUM]` — the seeded protocol lessons, pinned.
- `[SYSTEM_LEARNINGS — PAST LESSONS: read and APPLY these …]` — the most
  relevant earned lessons.
- `[WORKSPACE TOOLCHAIN]` — binaries probed from the workspace shell image
  (`services/execution/toolchain.py`, run by **workspace_runtime** at startup,
  synced to RAG; read back from `GET /rag/toolchain`).
- `[NEXTCLOUD RESOURCES]` and `[HOME ASSISTANT SNAPSHOT]` — from
  `/rag/resources/nextcloud` and `/rag/resources/ha`.

Raven cites lessons it applies as `Apply: [id]`; applied lessons are marked so
reuse is counted. `POST /rag/dream` consolidates the store (COMPACT oversized
summaries, MERGE duplicates sharing a rule, PRUNE superseded lessons).

## 7. Teaching it

1. **Always-on protocol curriculum (seeded).** At RAG startup
   `_seed_protocol_lessons()` idempotently writes the 19 `lesson-proto-*`
   lessons (tag `protocol`): dedicated workspace, cheapest sufficient tool,
   verification before reporting, web-search fact checks, `Apply: [id]`
   citations, and scenario lessons (writing, programming, typesetting,
   publishing, image editing, resource pulling).
2. **Verified mission lessons (earned).** Simple, independently checkable
   missions in `tests/integration/test_raven_python_basics.py` (hello CLI,
   FizzBuzz + tests, packaged module, file-processing CLI). Each mission must
   run its own artifact and append a dated lesson to `raven_memory.md`; the
   test re-runs the artifact itself before it accepts the memory update, so
   success is logged only once it is double-checked. Terse-prompt and
   resource-pulling missions live in `test_raven_terse_prompts.py` and
   `test_raven_web_tools_curriculum.py`.

Where to extend:

- `tool_builder.py` `_TOOLS` / `decide()` — teach a new capability or chain.
- `ALLOWED_TOOLS` in `agent_loop.py` — make a tool available inside missions.
- `SINGLE_TURN_TOOL_ENDPOINTS` in `orchestrator.py` — the Librarian path too.
- `RAVEN_KEYWORDS` / `RAVEN_COMMAND_VERBS` in `intent_engine.py` — what
  triggers a mission.
- `prompts/raven_autonomous_protocol.md` — changes every future mission.

Proven chains: greenfield build (create → `gh repo create` → settings → write
× N → lint/test → add → commit → push); tool discovery via
`RavenBuildToolRequest` (use_existing / chain / build a `tools/<slug>.py`);
loop recovery (`RavenRecallRequest(only="failed")` → read source → web search
→ a distinct fix → re-run).

## 8. Checking that the memory works

```bash
# Seeded protocol curriculum (19 lesson-proto-* ids). RAG listens on 8004.
curl -s 'http://192.168.2.205:8004/rag/learning?tag=protocol&user_id=default' \
  -H "X-Internal-Secret: $INTERNAL_SECRET" | python3 -m json.tool

# Toolchain inventory (populated when workspace_runtime starts):
curl -s 'http://192.168.2.205:8004/rag/toolchain' \
  -H "X-Internal-Secret: $INTERNAL_SECRET" | python3 -m json.tool

# A lesson landed after a mission:
docker logs sharedllm_gateway 2>&1 | grep "Mission reflection"
curl -s -X POST http://192.168.2.205:8004/rag/search \
  -H 'Content-Type: application/json' -H "X-Internal-Secret: $INTERNAL_SECRET" \
  -d '{"collection_name":"system_learnings","query":"python cli argparse","k":5}'

# The teaching test checks both the artifact and the memory:
LIVE_E2E=1 GH_TOKEN=... RAVEN_API_KEY=... \
  pytest tests/integration/test_raven_python_basics.py -v
```

`$INTERNAL_SECRET` comes from the server's `.env`; never paste it into docs.

In the app, Knowledge Hub > Raven Lessons lists lessons grouped by topic
(`GET /api/storage/learning`, any signed-in user). Edit and Delete are shown
to admins only and the gateway enforces it; RAG refuses ids outside
`system_learnings`. A mission marks a lesson applied by calling RAG's
`PATCH /rag/learning/{id}/applied` directly (there is no gateway route).

## 9. Failure modes to watch

- **Reflection empty** → the raw summary is stored instead (lower quality). If
  `system_learnings` holds only transcripts, the reflection call is failing.
- **`should_persist_learning` rejects** a good result → loosen the gate only if
  verified wins are being dropped.
- **Retrieval returns nothing** → check the collection exists and the query is
  semantically close to stored topics/tags.
- **Protocol block missing** → the curriculum fetch in `_fetch_rag_context`
  failed (gateway warning); check RAG is up and `tag=protocol` returns rows.
- **Toolchain block empty** → workspace_runtime never synced; check its logs
  and `/rag/toolchain`, and restart it after shell-image changes.
- **Wrong workspace / stale artifacts** → a workspace bootstrap failed
  (`create_if_missing` must be true for new `raven-*` workspaces).

## 10. Open gaps

See "Raven capability gaps" in `docs/PENDING_TASKS.md`.

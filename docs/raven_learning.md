# Raven Agent — Learning Document

> A comprehensive onboarding guide for developers learning how Raven works,
> how to extend it, and how to write missions that teach it new capabilities.

---

## Table of Contents

1. [What Is Raven?](#1-what-is-raven)
2. [Architecture Overview](#2-architecture-overview)
3. [The Agent Loop](#3-the-agent-loop)
4. [Missions](#4-missions)
5. [Tools](#5-tools)
6. [How They Fit Together](#6-how-they-fit-together)
7. [Expansion Points & Gaps](#7-expansion-points--gaps)
8. [Chaining Opportunities](#8-chaining-opportunities)
9. [Proposed Teaching Missions](#9-proposed-teaching-missions)
10. [Key Files Reference](#10-key-files-reference)

---

## 1. What Is Raven?

Raven is an **autonomous software-engineering agent** that operates inside the SharedLLM platform. Given a high-level mission (e.g. "build a space shooter game"), Raven:

1. Plans the work
2. Creates a dedicated workspace
3. Writes source code, tests, and config files
4. Runs lint/test/selftest gates
5. Commits and pushes to GitHub
6. Reflects on what it learned

Raven runs on a local 35B LLM (ornith:35b via Ollama) and is **fully unmanned** — no human in the loop once a mission starts.

---

## 2. Architecture Overview

```
User / UI
  │
  ▼
Gateway (port 11435)
  ├── orchestrator.py        ── Routing: single-turn vs autonomous
  ├── intent_engine.py       ── Detects "Raven" keyword + command verb
  ├── agent_loop.py          ── Main execution engine (AgentLoop function)
  ├── background_worker.py   ── Mission queue, health checks, retry
  ├── tool_registry.py       ── OpenAI-compatible tool schemas (8 tools)
  ├── tool_builder.py        ── RavenBuildToolRequest decision logic
  ├── state_machine.py       ── Checkpoint/resume for missions
  └── prompts/               ── Raven system prompts
        ├── raven_autonomous_protocol.md
        ├── raven_plan_prompt.md
        ├── raven_reflection_prompt.md
        └── raven_narrator_protocol.md
```

### Data Flow (Autonomous Mission)

```
POST /api/raven/missions
  → background_worker queues the job
  → worker calls orchestrator.process_full_orchestration()
    → intent_engine.is_raven_intent() confirms Raven keyword
    → orchestrator loads RAG context (system_learnings + workspace memory)
    → orchestrator dispatches to AgentLoop()
      → Phase 1: Planning (PROMPT_RAVEN_PLAN)
      → Phase 2: Execution loop (tool dispatch, loop detection, learning)
      → Phase 3: Reflection (PROMPT_RAVEN_REFLECTION) → persist to RAG
  → worker updates mission status in Identity service
```

---

## 3. The Agent Loop

**File:** `services/gateway/agent_loop.py` (4674 lines)

The `AgentLoop` async function (line 2423) is the heart of Raven's execution engine. It drives the iterative plan→act→verify cycle.

### Key Phases

| Phase | What Happens |
|-------|-------------|
| **Workspace resolution** | `resolve_mission_workspace()` — finds or creates the sandbox |
| **Model resolution** | Resolves role aliases (`coding`, `assistant`) to actual model names from Identity |
| **VRAM-safe params** | Pre-computes `num_predict`, `num_ctx`, temperature once per mission |
| **Planning** | Calls `PROMPT_RAVEN_PLAN` to generate a step-by-step plan |
| **Checkpoint load** | Restores state from Redis if resuming a paused/failed mission |
| **Main loop** | Iterates up to `max_iterations` (default 60): infer → extract tool call → execute → record |
| **Loop detection** | Detects stagnation (same action repeated with identical output) and escalates |
| **Reflection** | On completion, calls `PROMPT_RAVEN_REFLECTION` to produce a reusable lesson |
| **Persistence** | Writes the lesson to RAG `system_learnings` collection via `_persist_learning()` |

### Critical Guards

- **Tool dispatch guard** (`_normalize_tool`): Validates and normalizes model-emitted tool calls. Rejects malformed writes (e.g. `file_path: ":"`).
- **Workspace guard**: Blocks workspace tools (write, shell, git) when no workspace is assigned — forces Raven to create one first for project missions.
- **System maintenance guard**: `is_system_maintenance_task()` detects "fix the errors" queries so they run in the Default Workspace instead of a new sandbox.
- **Loop detection** (`detect_repetitive_failure`, `detect_repetitive_action`, `detect_no_progress`): Catches infinite re-runs of the same command and injects escalating directives (PROBE → REDIRECT → ABORT).
- **Learning persistence gate** (`should_persist_learning`): Rejects empty, error-only, or incomplete results so the memory store only contains verified lessons.
- **Credential sanitizer** (`sanitize_for_llm`): Strips API keys, tokens, passwords from any data before feeding it back to the LLM.

### The Batch Mechanism

Raven can emit a **JSON array** of tool calls in a single inference turn. This is the primary speed lever — a known sequence (create workspace → gh repo create → wire settings → write ALL files → lint → commit → push) executes from one reasoning cycle instead of N. The `build_adaptive_guidance()` function steers the model toward batching in early phases and converging in late phases.

---

## 4. Missions

A **mission** is a unit of work submitted to Raven. It flows through:

1. **Submission** → `POST /api/raven/missions` (or via `RavenMissionRequest` tool)
2. **Queuing** → `InferenceJobQueue` in Redis (FIFO)
3. **Processing** → `RavenWorker._process_inference_job()` in `background_worker.py`
4. **Orchestration** → `process_full_orchestration()` in `orchestrator.py`
5. **Execution** → `AgentLoop()` in `agent_loop.py`

### Mission Lifecycle States

```
queued → executing → completed | failed | paused
```

### Key Mission Concepts

- **`_mission_id`**: Any job carrying this field was submitted through the Raven mission pipeline and runs the autonomous AgentLoop. This is how the orchestrator distinguishes mission jobs from single-turn queries.
- **Workspace**: Every project mission gets its own workspace sandbox. System maintenance missions use the user's Default Workspace.
- **RAG context**: Before each mission, `_fetch_rag_context()` searches `system_learnings`, `system_capabilities`, `nextcloud_files`, and `ha_entities` collections, injecting the most relevant past lessons into the system prompt.
- **Workspace memory**: Each workspace has its own `raven_memory.md` journal — read at mission start for same-workspace reinforcement.

---

## 5. Tools

### Tool Registry (`tool_registry.py`)

The OpenAI-compatible tool schemas exposed to external clients:

| Tool Name | Service | Purpose |
|-----------|---------|---------|
| `sharedllm_gh` | execution | Run `gh` CLI commands (repo create, PRs, issues) |
| `sharedllm_git` | execution | Git operations (status, add, commit, push, log, branch, checkout) |
| `sharedllm_write_file` | workspace_runtime | Write/modify files in a workspace |
| `sharedllm_image_generate` | alpaca_sd | Generate images via Stable Diffusion |
| `sharedllm_image_edit` | alpaca_sd | Edit existing images |
| `sharedllm_list_image_models` | alpaca_sd | List available SD models |
| `sharedllm_raven_mission` | gateway | Dispatch a background Raven mission |
| `workspaceportexposerequest` | workspace_runtime | Expose a container port to the host |

### Internal Tool Actions (agent_loop.py)

Inside the AgentLoop, Raven uses a broader set of **internal tool actions** (4674-line file, the `ALLOWED_TOOLS` set at line 475). These include:

- **Workspace tools**: `WorkspaceFileWriteRequest`, `WorkspaceFileReadRequest`, `WorkspaceFilePatchRequest`, `WorkspaceShellRequest`, `WorkspaceLintRequest`, `WorkspaceSearchRequest`, `WorkspaceBootstrapRequest`, `WorkspaceCreateRequest`, `WorkspaceSettingsUpdateRequest`, `WorkspacePortExposeRequest`
- **Git tools**: `GitOperationRequest` (status, diff, add, commit, push, pull, log, branch, checkout, init, remote_add, repo_create)
- **Raven-internal tools**: `RavenBuildToolRequest` (tool discovery/chaining), `RavenRecallRequest` (mission history introspection), `SystemLearningRequest` (persist lessons to RAG)
- **Service tools**: `HAConfigRequest`, `ContextSearchRequest`, `DiscoverySyncRequest`, `StorageIndexRequest`, `EntitySearchRequest`, etc.

### Tool Routing

Tool calls are dispatched via the `_execute_single_tool()` function in `orchestrator.py` (line 591) which maps action names to service endpoints using the `SINGLE_TURN_TOOL_ENDPOINTS` dictionary. For Raven missions, the AgentLoop handles tool dispatch directly via HTTP calls to the execution service.

---

## 6. How They Fit Together

### Single-Turn Path (Librarian)

```
User query → orchestrator → is_raven_intent() = False
  → _single_turn_inference()
    → call_ollama() with system prompt + RAG context
    → extract_action_json() from response
    → _execute_single_tool() → execute-service HTTP call
    → append result to conversation → repeat (up to 3 turns)
    → strip_json_from_response() → return answer
```

### Autonomous Path (Raven Mission)

```
User query → orchestrator → is_raven_intent() = True (or _mission_id present)
  → AgentLoop()
    → resolve_mission_workspace()
    → load dynamic settings + model
    → inject RAG context + workspace info into system prompt
    → PLANNING PHASE: call PROMPT_RAVEN_PLAN → generate plan
    → EXECUTION LOOP (up to 60 iterations):
      → call OllamaProvider.generate() with plan + context + guidance
      → extract_action_json() or extract_action_batch() from response
      → _normalize_tool() → validate → dispatch to execution service
      → record result → detect loops/stagnation → inject guidance
      → _save_checkpoint() to Redis (for resume)
    → REFLECTION PHASE: call PROMPT_RAVEN_REFLECTION → produce reflection_summary
    → _persist_learning() → POST to execution/learning → RAG system_learnings
    → return final answer
```

### The Learning Pipeline

Raven's memory system ensures it gets better over time:

1. **Write**: After a successful mission, `_persist_learning()` extracts the `reflection_summary` (the lesson) and writes it to RAG `system_learnings` collection with task-aware tags (python/javascript/go/rust, workspace/git/repair/deployment).
2. **Read**: Before each new mission, `_fetch_rag_context()` searches `system_learnings` and injects the most relevant past lessons into the system prompt under a `[SYSTEM_LEARNINGS — PAST LESSONS]` header.
3. **Apply**: Raven is instructed to cite applied lessons as `Apply: [id]` in its plan and to use `RavenRecallRequest` to review its own history before repeating failed approaches.

The per-workspace `raven_memory.md` journal provides same-workspace reinforcement on top of the global system_learnings store.

---

## 7. Expansion Points & Gaps

### Current Gaps

| Area | Gap | Impact |
|------|-----|--------|
| **Tool set** | No native browser automation tool (only shell-based web scraping) | Raven can't test web UIs interactively |
| **Observation** | No screenshot/visual feedback loop | Can't verify UI state or visual rendering |
| **Multi-agent** | No delegation to specialized sub-agents | All work is done by a single LLM instance |
| **CI integration** | CI workflow generation is conditional on GitHub credentials | Projects without CI get no automated validation |
| **Dependency management** | No explicit dependency-install awareness | Raven must discover and install deps manually |
| **Error recovery** | Loop detection escalates but doesn't auto-diagnose | Raven still needs to figure out fixes itself |
| **Cross-workspace** | No shared library/template reuse across workspaces | Each mission starts from scratch for tooling |

### Expansion Points

1. **`tool_builder.py`** (`decide()` function): The "use_existing → chain → build" router can be extended with new capability triggers to teach Raven about tools it doesn't yet know exist.
2. **`ALLOWED_TOOLS` set** (agent_loop.py line 475): Add new tool action names here to make them available inside missions.
3. **`SINGLE_TURN_TOOL_ENDPOINTS`** (orchestrator.py line 174): Add new endpoint mappings for tools that should work in the single-turn Librarian path too.
4. **`_TOOLS` catalog** (tool_builder.py line 41): Add new `_Tool` entries to teach the tool-discovery router about capabilities Raven can chain.
5. **`RAVEN_KEYWORDS` / `RAVEN_COMMAND_VERBS`** (intent_engine.py line 27-34): Adjust which prompts trigger Raven autonomous missions.
6. **`_MAINTENANCE_PATTERNS`** (agent_loop.py line 243): Add patterns to control which queries get the Default Workspace vs. a dedicated workspace.
7. **Prompt files** (`prompts/raven_*.md`): The system prompt is the primary way to teach Raven new behaviors. Modifying `raven_autonomous_protocol.md` changes all future missions.
8. **`should_persist_learning()`** (agent_loop.py line 1926): Extend the quality gate to accept new types of successful mission results.

---

## 8. Chaining Opportunities

### Proven Chains (from the autonomous protocol)

The most efficient chains Raven uses are **batched arrays** emitted in a single inference turn:

**Greenfield build chain** (the canonical example):
```
WorkspaceCreateRequest → WorkspaceShellRequest(gh repo create) → WorkspaceSettingsUpdateRequest → [WorkspaceFileWriteRequest × N] → WorkspaceShellRequest(lint+test) → GitOperationRequest(add) → GitOperationRequest(commit) → GitOperationRequest(push)
```

**Tool discovery chain** (using RavenBuildToolRequest):
```
RavenBuildToolRequest(capability) → use_existing: call the named tool directly
                                     → chain: execute 2-3 tools in sequence
                                     → build: scaffold tool/ run it
```

**Loop recovery chain** (when stagnation is detected):
```
RavenRecallRequest(only="failed", limit=10) → read source file → websearchrequest → make distinct fix → re-run
```

### How to Chain New Capabilities

When teaching Raven a new capability, think in terms of which existing tools it can **chain** with:

1. **Simple chain**: New capability = one known tool → teach the `_TOOLS` catalog in `tool_builder.py`
2. **Multi-step chain**: New capability = sequence of known tools → document the sequence in the system prompt
3. **Build mode**: New capability = no existing tool fits → scaffold a `tools/<slug>.py` and teach Raven to implement `run()` then execute it

---

## 9. Proposed Teaching Missions

These missions are designed to teach Raven specific capabilities in a progression from simple to complex. Each mission should be **independently verifiable** before it populates the learning memory.

### Tier 1 — Foundation (verify basic CLI + file I/O)

| # | Mission | Teaches | Verification |
|---|---------|---------|-------------|
| T1 | Build a Python CLI that prints "Hello, Raven" with a `--name` flag | WorkspaceCreateRequest, WorkspaceShellRequest, WorkspaceFileWriteRequest, git commit/push | Run the CLI: `python hello.py --name Alice` → `Hello, Alice` |
| T2 | Implement FizzBuzz with a passing `pytest` test suite | Test writing, pytest execution, passing gates | `pytest` passes with 100% coverage of fizzbuzz logic |
| T3 | Create a small Python module + `requirements.txt` + `pyproject.toml` | Packaging, importability, installable distribution | `pip install .` succeeds and `import module` works |
| T4 | Build a file-processing CLI (reads file, counts words, writes report) | File I/O, argument parsing, output verification | Run with a test file, assert correct word count in output |

### Tier 2 — Intermediate (verify code quality + CI)

| # | Mission | Teaches | Verification |
|---|---------|---------|-------------|
| T5 | Build a Python class with type hints + `mypy` passing | Type checking, code quality | `mypy .` passes with zero errors |
| T6 | Create a FastAPI endpoint that returns JSON | API development, dependency management | `curl http://localhost:<port>/endpoint` returns expected JSON |
| T7 | Build a web scraper that extracts titles from URLs | External API calls, HTML parsing | Scrape a known page, assert title matches |
| T8 | Implement a git PR automation script (gh cli) | GitHub API, automation | Script creates a PR and verifies it exists |

### Tier 3 — Advanced (verify cross-cutting concerns)

| # | Mission | Teaches | Verification |
|---|---------|---------|-------------|
| T9 | Build a Discord bot with command routing | Event-driven architecture, async Python | Bot responds to `/ping` with `Pong` |
| T10 | Create a SQLite-backed CRUD app with migration | Database patterns, schema management | Migration runs, CRUD operations all succeed |
| T11 | Implement a config-driven pipeline (YAML config → stages) | Configuration parsing, pipeline patterns | Pipeline executes all stages in order |
| T12 | Build a plugin system where Raven discovers and loads tools | Meta-programming, dynamic imports | Plugin loaded and called successfully |

### Tier 4 — Cross-Workspace & Memory

| # | Mission | Teaches | Verification |
|---|---------|---------|-------------|
| T13 | Reuse a lesson from `system_learnings` to solve a variant of a previous task | RAG memory recall, lesson application | Mission explicitly cites `Apply: [id]` in its plan |
| T14 | Build a tool that reads `raven_memory.md` from a previous workspace | Workspace memory recall | Tool reads and applies the journal correctly |

---

## 10. Key Files Reference

| File | Lines | Purpose |
|------|-------|---------|
| `services/gateway/agent_loop.py` | 4674 | Main execution engine — AgentLoop, tool dispatch, loop detection, learning persistence |
| `services/gateway/tool_registry.py` | 352 | OpenAI-compatible tool schemas for 8 external-facing tools |
| `services/gateway/tool_builder.py` | 223 | `RavenBuildToolRequest` decision logic (use_existing → chain → build) |
| `services/gateway/orchestrator.py` | 823 | Routing, RAG context injection, single-turn vs autonomous dispatch |
| `services/gateway/background_worker.py` | 1009 | Mission queue, health checks, retry, orphan recovery |
| `services/gateway/state_machine.py` | 222 | Checkpoint/resume for missions |
| `services/gateway/intent_engine.py` | 243 | Raven intent detection (keyword + verb heuristics) |
| `services/gateway/prompts.py` | — | Prompt loading (PROMPT_RAVEN_PLAN, PROMPT_RAVEN_REFLECTION) |
| `prompts/raven_autonomous_protocol.md` | 171 | Raven's system prompt — protocol, tool format, guardrails |
| `prompts/raven_plan_prompt.md` | — | Template for the planning-phase system prompt |
| `prompts/raven_reflection_prompt.md` | — | Template for the reflection-phase system prompt |
| `prompts/raven_narrator_protocol.md` | — | Narration/UI protocol prompt |
| `services/gateway/agent_loop.py:1926` | — | `should_persist_learning()` — quality gate for memory persistence |
| `services/gateway/agent_loop.py:137` | — | `build_adaptive_guidance()` — per-iteration steering |
| `services/gateway/agent_loop.py:2019` | — | `resolve_mission_workspace()` — workspace allocation logic |
| `services/gateway/background_worker.py:53` | — | `RavenWorker` — the singleton inference worker |
| `services/gateway/orchestrator.py:270` | — | `process_full_orchestration()` — top-level pipeline entry |
| `services/gateway/orchestrator.py:327` | — | `_fetch_rag_context()` — RAG read path with workspace memory bridge |
| `services/gateway/tool_builder.py:142` | — | `decide()` — the tool discovery router |
| `services/gateway/intent_engine.py:37` | — | `is_raven_intent()` — the intent classifier |
| `docs/RAVEN_LEARNING_MEMORY.md` | — | Design doc for the learning memory system |
| `docs/RAVEN_CAPABILITY_GAP_ANALYSIS.md` | — | Analysis of Raven's current capability gaps |
| `docs/RAVEN_AUDIT_BLUEPRINT.md` | — | Audit blueprint for Raven hardening |
| `services/gateway/tests/test_mission_routing.py` | — | Tests for mission routing logic |
| `services/gateway/tests/test_mission_outcome_assessment.py` | — | Tests for mission outcome assessment |
| `services/tests/test_raven_user_cases.py` | — | End-to-end user case tests |
| `services/tests/test_raven_command_seeding.py` | — | Tests for command seeding |
| `services/tests/raven_git_coding_path.py` | — | Git coding path integration tests |

---

## Quick Start Commands

```bash
# Run the gateway tests
pytest services/gateway/tests/ -v

# Run the Raven integration tests
LIVE_E2E=1 GH_TOKEN=... RAVEN_API_KEY=... \
  pytest tests/integration/test_raven_python_basics.py -v

# Check RAG system_learnings contents
curl -s -X POST http://localhost:8002/rag/search \
  -H 'Content-Type: application/json' \
  -H 'X-Internal-Secret: RAVEN_SECURE_2026' \
  -d '{"collection_name":"system_learnings","query":"python cli","k":5}' \
  | python3 -m json.tool

# Check gateway logs for lesson persistence
docker logs sharedllm_gateway 2>&1 | grep "Mission reflection"
```

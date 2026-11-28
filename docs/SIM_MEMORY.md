# Self-Improvement Memory (SIM)

SIM is the shared, problem-grouped learning ledger described in
*Self-Improvement Memory Enables Continual Recursive Self-Improvement*
(arXiv:2611.18856). It records **why** a rule exists, **what to do**, and
**what actually happened**, then injects only the rules that are safe for the
current user and model family.

It is deliberately small at runtime: a bounded hint block, never a second
knowledge base. The full ledger is for proposer agents and humans.

## Two tiers

| | System SIM | User SIM |
|---|---|---|
| Scope | `scope='system'`, `user_id='default'` | `scope='user'`, `user_id=<person>` |
| Audience | every user and every harness | exactly one person |
| Privacy | personal data, secrets, emails, tokens, paths, IPs and phone numbers are scrubbed before storage | may keep personal phrasing, disambiguations, corrections and workspace habits |
| Admin rules | may set `requires_admin=True`; never shown to non-admins | n/a |
| Retrieval | merged for every caller, filtered by `is_admin` | merged only for the exact lower-cased `user_id` |

There is no third tier and no fallback that treats an unknown scope as shared:
`normalize_scope()` raises instead. A cross-user read is a bug, not a policy
choice, so retrieval always uses `scope='user' AND user_id = ?`.

## Problem grouping

One lesson is one `problem_id`, derived from
`scope + user_id + harness + category + signature`. Recording the same problem
again updates the rule and appends an `attempt`, so the ledger keeps history
instead of accumulating competing copies.

Each problem stores:

- `rationale` — why the rule/fix exists (root cause),
- `edit_or_rule` — the actionable rule/fix,
- `predicted_outcome`, `observed_outcome`,
- `outcome_status` — `pending | improved | neutral | regressed`,
- evaluation context — `harness`, `domain`, `model`, `model_family`, `round`,
- `verified_models` and `regressed_models` (JSON arrays of families),
- lifecycle `status`, `requires_admin`, `tags`.

## Cross-model portability

`normalize_model_family()` maps concrete ids to stable families:
`kwaipilot-kat-coder-v2-5-dev-iq4-nl → kat-coder`,
`qwen3-6-35b… → qwen`, `inkling-2-mini → inkling`,
`gemini-… → gemini`, `claude-… → claude`, `deepseek-… → deepseek`, and so on.
Unknown models group by their leading alphabetic token.

Lifecycle:

| Status | Meaning | Runtime injection |
|---|---|---|
| `candidate` | recorded, awaiting verification | yes (lowest priority) |
| `verified` | improved on one family, no regression on the current one | yes |
| `portable` | improved on **≥2** families with a low regression rate | yes, **highest priority** across model swaps |
| `model_specific` | improved on family A, regressed on family B | only for family A; **never** for B or an untried family |
| `retired` | consistently regressed or superseded | **never** |

`retired` rows stay in the ledger on purpose: proposer agents must see which
fixes already failed and why, so they never propose the same fix again.

Retrieval ranks `portable` → verified on the current family → verified on
another family → candidate, and filters out retired rows, rows that regressed
on the current family, and admin-only rows for non-admins.

## Where it is stored

RAG owns two SQLite tables (`services/rag/db.py`, created with
`CREATE TABLE IF NOT EXISTS`):

- `sim_problems` — the current problem-grouped row and lifecycle state,
- `sim_attempts` — append-only attempt/evaluation history.

Indexes cover `(scope, user_id)`, `harness`, `status`, `problem_id` and the
attempt `model_family`/`problem_id`. `services/rag/sim.py` implements record,
hybrid query, outcome, list, history and consolidation.

Retrieval is hybrid: lexical tokens over rule/rationale/signature, tag and
harness affinity, and optional embedding cosine similarity. If the embedder is
missing or raises, retrieval silently uses the lexical/tag score.

## RAG API (internal secret required)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/rag/sim/record` | record/regroup a problem and append an attempt; scrubs System SIM |
| `POST` | `/rag/sim/query` | System SIM (admin-filtered) + exact caller's User SIM |
| `POST` | `/rag/sim/outcome` | record `observed_outcome`, update `verified_models`/`regressed_models`/status |
| `GET` | `/rag/sim/problems` | filter by scope, user, harness, status, retired |
| `GET` | `/rag/sim/history` | full history, including retired/regressed attempts (proposers) |
| `POST` | `/rag/sim/consolidate` | promote portable winners, mark model-specific, retire regressions |

Every row-scoped query take `is_admin`; System SIM rows with
`requires_admin=1` are omitted for non-admins.

## Authenticated gateway API

Mounted under `/api/trainer/sim` (signed-in users; admin checks on System SIM):

| Method | Path | Who |
|---|---|---|
| `GET` | `/api/trainer/sim/problems` | any signed-in user: own User SIM + non-admin System SIM; admins also see admin-only System rows |
| `GET` | `/api/trainer/sim/history` | same policy, retired rows included |
| `POST` | `/api/trainer/sim/record` | User SIM for the caller; System SIM only for admins |
| `POST` | `/api/trainer/sim/outcome` | only a User SIM problem the caller owns |
| `POST` | `/api/trainer/sim/consolidate` | admins only |

The gateway never accepts a `user_id` for User SIM from the request; it always
uses the authenticated caller.

## The three harnesses

### Raven missions (`services/gateway/agent_loop.py`)

Before workspace resolution/planning, `AgentLoop` queries `harness='raven'` and
appends the block to its system context (Raven has no prompt-cache contract).
The injected rows are remembered; mission completion/failure records
`improved`/`regressed` for them. Detected no-progress loops record a negative
`loop_stall` lesson through `_record_loop_probe`.

### Workspaces (`orchestrator.py`, `trainer/selffix.py`)

Workspace chat turns call `_single_turn_inference(..., harness='workspace')`.
`selffix.repair_workspace` also queries `harness='workspace'` and puts the hints
in the repair prompt, then records the exact model after the pytest gate:
passing repairs are positive lessons, failing ones are negative lessons.

### Satellite assistants / single-turn voice and chat (`orchestrator.py`)

`_single_turn_inference(..., harness='satellite')` injects at most **600
characters**.

**Prompt-cache rule:** the SIM block goes only into the per-request question
(`context_parts`). It must never be added to the static system message or the
`single_turn_tool_guide` prefix. That fixed prefix (~3,500 tokens) is what
alpaca-proxy keeps warm; mutating it would re-read the whole guide on every
voice turn. The tests assert the system message is byte-for-byte unchanged and
the hint appears in the user question.

Tool failures are tracked across turns. A 4xx/5xx/validation error followed by
a successful retry records a `tool_recovery` lesson: schema/alias mistakes go
to scrubbed System SIM; an ambiguous or user-specific disambiguation goes to
that user's User SIM.

## Proposer–selector loop

`services/gateway/trainer/dream.py`:

- `sim_history()` is read by the facilitator and Raven-failure pass **before**
  proposing anything, including retired/regressed attempts.
- `proposals_from_sim()` emits `sim_retired` (“never re-propose this”),
  `sim_validate` (recurring unverified rule) and `sim_portability` advice.
  `sim_retired` is shown but is not written as a behavioural lesson.
- `record_eval_outcomes()` records every evaluated case against the model that
  ran it, then calls `consolidate_sim()`.
- `teacher.write_lessons()` records verified teacher traces as positive rules;
  `raven_failures.run_pass()` records each classified failure as a negative
  rule, keyed by tool/kind/evidence.

## Privacy and security notes

- `scrub_system_sim_text()` is applied to every System SIM text field before
  storage (emails, provider tokens, key/value secrets, Bearer tokens, POSIX and
  Windows paths, IPv4 addresses, phone numbers, long opaque tokens).
- User SIM is never scrubbed of the person's own phrasing (that is the point),
  but it is never readable under a different `user_id`.
- `requires_admin` is enforced in both SQL retrieval and the prompt formatter.
- SIM is optional at runtime: a RAG outage yields an empty block and never
  fails a chat turn, mission or repair.

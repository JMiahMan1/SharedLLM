# Jarvis Trainer: teaching the household's own model

> The trainer is the facilitator around the local model. It derives everything
> Jarvis can do from the code, turns the household's real work into checkable
> cases, runs them inside a hardware budget, and turns the failures back into
> lessons, repairs and UI work. Code: `services/gateway/trainer/`. UI: Admin →
> Lab → **Training**. Endpoints: `docs/api_reference.md`.

## 1. Why it exists

Raven learns one mission at a time. Nobody was asking the harder question: *does
the local model actually know what Jarvis can do, and is it getting better?*
The trainer answers it with three habits:

- **derive, never list** — features come from the source, so a new route or tool
  is in the curriculum the moment it is merged;
- **score, never judge** — every case is checked with strings and regexes, so a
  run is reproducible and comparable week to week;
- **close the loop** — a failure becomes a ranked proposal, a proposal becomes a
  low-confidence lesson in RAG, and the next mission carries it. Dreaming then
  compacts, merges or prunes it like any other lesson.

## 2. The catalogue (`trainer/catalog.py`)

`build_catalog()` reads four sources:

| Source | How | What it proves |
|---|---|---|
| Service routes | `ast` parse of each service's `main.py` route decorators | every endpoint that exists, with its docstring, tags and a derived use case |
| The gateway app | the live app passed in from the request (`_IncludedRouter` includes are flattened) | the Jarvis API exactly as FastAPI serves it |
| Raven's tools | `tool_registry._RAVEN_TOOL_TABLE` | the tools the model is actually offered |
| The UI | `services/ui/src/App.tsx` route table | the screens a person can open |

Each feature becomes a sentence (`POST /api/workspaces/{id}/files/write` →
"Ask Jarvis to workspace files write") which is what the curriculum asks a
person to type. Tags (`workspaces`, `memory`, `media`, `bible`, …) are derived
from the path, so a new area of the product is grouped without configuration.

The catalogue is cached per process: a source file cannot gain a route at
runtime, and `main.py` alone is over twenty thousand lines.
`build_catalog(refresh=True)` re-reads.

## 3. The curriculum (`trainer/curriculum.py`)

Roughly 95 cases, in this order:

1. **Seeds** — the household's own work, hand-written with objective checks:
   fix the off-by-one bug from the code alone; name the root cause of a real
   `ClientTimeout` traceback; outline a sermon from Romans 8:28; quote John 3:16;
   turn a photo request into a valid `ImageEditRequest` (no `image` key, no
   donor for a style change); name `WorkspaceFileWriteRequest` for saving a
   sermon; write `Apply: [lesson-abc123]` exactly; name dreaming's three passes;
   start a mission with `WorkspaceCreateRequest`; explain a service in under 90
   words.
2. **Workspace recipes** — read, write, test, pull-on-push, terminal. Each is
   only emitted while the endpoint exists on `workspace_runtime`; a renamed
   route drops its case instead of teaching a dead call.
3. **Tool cases** — one per row of the Raven tool table: the question is the
   tool's own summary, the answer is its name. Adding a tool adds a case.
4. **Service cases** — "which service owns `/rag/dream`?", derived from the
   catalogue, because a wrong service is the most common silent failure.

Buckets: `repair`, `writing`, `vision`, `workspace`, `memory`, `protocol`,
`tools`, `knowledge`. `GET /api/trainer/curriculum?buckets=writing&limit=20`.

## 4. A run (`trainer/runner.py`)

- **Sequential, bounded.** One short answer per case, a wall-clock budget
  (`budget_s`), a token ceiling per case, and a stop after three consecutive
  model failures. A small model shared with a household must not be hammered.
- **Scored by checks only.** `must_include`, `must_not_include`, `must_match`,
  `must_not_match`, `max_chars`; the score is the fraction of checks that held,
  and the case passes only on a clean sheet.
- **Evidence kept.** Each report goes to `data/trainer/reports/<id>.json`
  (untracked) with every answer, the failed checks, the model and the timings.
  `list_reports` / `load_report` serve the Lab; report ids are validated so they
  cannot walk the tree.
- **Training data.** `export_dataset` writes JSONL rows shaped as
  `{"messages": [system, user, assistant], "meta": {...}}` — accepted rows only
  by default, or every row (with `accepted: false`) for a later
  preference-tuning pass. `POST /api/trainer/reports/dataset`.

## 5. Self-repair (`trainer/selffix.py`)

The answer to "Jarvis should fix its own errors":

1. read the file through the **Workspace API** (`/files/read`);
2. if no traceback was given, run the workspace's own gate (`/tests/pytest`)
   first — a workspace that already passes is left alone;
3. ask the local model for the **whole corrected file** in one fenced block;
4. **guard it**: fenced block required, must differ from the failing file, must
   compile, and must keep every top-level function and class;
5. write it back with `expected_sha256` (a concurrent edit is a conflict, not an
   overwrite);
6. run the gate again. On failure, retry with the real test output as context;
7. if every attempt fails, **restore the original file** — a failed repair
   leaves the workspace exactly as it was found.

The guards are the point. Without them "self-repair" is a model deleting the
function under test and calling it a fix.

## 6. Written works (`trainer/sermon.py`)

A small model cannot write a whole sermon in one prompt, so the pipeline works
the way a preacher does:

1. read the passage from the family's **Bible service** (`/execute/bible`,
   `action=read`) — the text is quoted, not remembered;
2. one call for a labelled outline (title, big idea, points, application,
   prayer);
3. one call per point, each 90–140 words, grounded on the passage;
4. render markdown and save it through the Workspace API as
   `sermons/<slug>.md`.

The file states plainly when scripture could not be fetched and that quotations
must be checked before preaching. An unlabelled outline is kept as one section
rather than thrown away.

`illustrate()` edits an image in the same workspace through
`/execute/image_edit` (`image_path` + `prompt`), which is the same handler the
Workspace drawing canvas uses — a face *style* change is an instruction, a face
*swap* needs a donor (`face_image_path`).

## 7. Dreaming and the backlog (`trainer/dream.py`, `trainer/facilitator.py`)

Dreaming already compacts, merges, prunes and validates lessons. The trainer
adds the question dreaming does not ask: **what next?**

`proposals_from_dream` turns a report into work:

| Report says | Proposal |
|---|---|
| a lesson is `degraded` (no rule/root cause/outcome) | rewrite it (priority 9) |
| a lesson was applied but never validated | run a mission that proves it (6) |
| `reviewed` is tiny | seed memory from the capability groups (4) |
| lessons were compacted | keep new lessons short (3) |

`proposals_from_failures` adds:

| Evaluation says | Proposal |
|---|---|
| the model stopped answering | pull/load `coding_model` (10) |
| a tool was not chosen from its own summary | document its trigger in `prompts/single_turn_tool_guide.md` (8) |
| a bucket failed | add one protocol lesson for that mistake (3 + failures) |

`ui_proposals` compares route tags with page tags: a tag with ≥10 capabilities
and no screen is a door the app does not have. Internal tags (`tools`, `admin`,
`telemetry`, `memory`, `identity`) are excluded — they are not screens.

`cycle()` runs dream → evaluate → merge proposals → write the top three into
RAG (`type: improvement_proposal`, confidence 0.35) → optionally a proven
repair → save `data/trainer/backlog.json`. Every step is isolated: a RAG outage
is recorded as an error and the rest of the pass still runs.

## 8. The endpoints

| Endpoint | Who | What |
|---|---|---|
| `GET /api/trainer/catalog` | signed in | features, counts, fingerprint |
| `GET /api/trainer/curriculum?buckets=&limit=` | signed in | derived cases and their summary |
| `POST /api/trainer/sermon` | signed in | draft a sermon into a workspace |
| `POST /api/trainer/illustrate` | signed in | edit an image in a workspace |
| `POST /api/trainer/eval` | admin | run a bounded evaluation |
| `GET /api/trainer/reports`, `/reports/{id}` | admin | reports and their detail |
| `POST /api/trainer/reports/dataset` | admin | export a JSONL fine-tuning set |
| `POST /api/trainer/repair` | admin | self-repair a workspace file, proven by its tests |
| `POST /api/trainer/dream` | admin | dream, plus what it implies |
| `POST /api/trainer/dream/validate` | admin | report a lesson's self-test |
| `GET /api/trainer/backlog` | admin | the last cycle's proposals |
| `POST /api/trainer/cycle` | admin | the whole pass in one call |

## 9. Working within the hardware

- **One call at a time.** No parallel prompting: a small model queues anyway and
  concurrency only spreads the latency across more failed timeouts.
- **Small, purposeful prompts.** Tool cases ask one question and allow 60 output
  tokens; the sermon splits into an outline plus one call per point; `llm.py`
  keeps the head and tail of an over-long prompt (the middle is dropped with a
  visible marker) instead of failing on context length.
- **A budget, not hope.** `budget_s` stops a run; three consecutive model
  failures stop it sooner, with the reason recorded.
- **The gate is free.** Compiling a candidate and checking its definitions costs
  nothing and catches most bad repairs before any test runs.

## 10. How to use it

```bash
# What does it know?
curl -s "$JARVIS/api/trainer/curriculum?limit=5" -H "Authorization: Bearer $KEY" | jq '.cases[].title'

# A bounded run on the repair and writing cases
curl -s -X POST "$JARVIS/api/trainer/eval" -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' -d '{"buckets":["repair","writing"],"limit":8}' | jq '.totals'

# Fix a failing workspace file, proven by that workspace's tests
curl -s -X POST "$JARVIS/api/trainer/repair" -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  -d '{"workspace_id":"sermons","path":"tools/parse.py","test_targets":["tools/test_parse.py"]}' | jq

# Draft a sermon from a passage into the workspace
curl -s -X POST "$JARVIS/api/trainer/sermon" -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  -d '{"workspace_id":"sermons","passage":"Romans 8:28","points":3}' | jq '{title,path,words}'
```

In the app: **Admin → Lab → Training**. The tab shows the catalogue summary, the
case counts per bucket, the last report's bucket bars with the five worst
failures, the improvement backlog, and the written-works composer.

### Continuous improvement without a cron entry

The gateway's worker runs the same bounded cycle on a timer when
`trainer_cycle_interval_minutes` is set (Admin → LLM & Settings; `0` keeps it
off, and `raven_suspended` pauses it too). Six cases, a 240 s budget, then it
sleeps: see `docs/BACKGROUND_JOBS.md`, "Trainer cycle".

From a shell, `scripts/train_jarvis.py` drives the same endpoints:

```bash
python scripts/train_jarvis.py cycle --limit 6            # dream + evaluate + propose
python scripts/train_jarvis.py eval --buckets repair       # just the repair cases
python scripts/train_jarvis.py dataset --report eval-...   # JSONL fine-tuning rows
```

## 11. Where the notes are

- `docs/RAVEN_LEARNING_MEMORY.md` — what Raven is, the mission loop, and the
  lessons this trainer writes and scores.
- `docs/api_reference.md` — the trainer's endpoints alongside the rest.
- `services/gateway/trainer/` — the modules; each has a docstring saying what it
  refuses to do (overwrite without a conflict check, write unproven code, call
  the model without a budget).

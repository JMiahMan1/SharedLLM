# Teacher traces: teaching the local model from strong cloud models

> Strong cloud models (the *teachers*: opencode on OpenRouter models, Cline on
> DeepSeek) do real agentic work on this repository. The household's local model
> (on Alpaca) should learn from that work. This pipeline collects those sessions
> on the Mac, scrubs them before anything leaves it, and feeds them into the
> Jarvis Trainer three ways: retrieved lessons, evaluation cases, and a
> fine-tuning dataset. It extends the trainer (`docs/JARVIS_TRAINER.md`); it is
> not a parallel system.

## 1. The pieces

| Piece | Where | Runs on |
|---|---|---|
| Collector CLI | `scripts/collect_teacher_traces.py` | the Mac |
| Normaliser (opencode + Cline to one trace shape) | `services/gateway/trainer/teacher_traces.py` | the Mac (and tests) |
| Scrubber | `services/gateway/trainer/teacher_scrub.py` | the Mac; the gateway re-checks |
| Store, lessons, eval cases, fine-tune rows | `services/gateway/trainer/teacher.py` | the gateway |
| Routes | `/api/trainer/teacher*` in `services/gateway/trainer/routes.py` | the gateway |
| Routing helper | `scripts/teacher_route.py`, `services/gateway/trainer/teacher_routing.py` | anywhere |
| opencode plugin | `tools/opencode-trainer-sync/` | the Mac, inside opencode |
| Lab card | Admin, Lab, Training, "Teacher traces" | the app |

## 2. A trace

Each user turn in a session is one *task*. The collector turns it into:

- `goal`: what the person asked;
- `steps`: each tool call as `{tool, kind, args, output, status, exit, thought}`,
  inputs and outputs truncated (head and tail, with a marker); paths made
  relative to the project; at most 80 steps;
- `final_answer`: the teacher's closing text;
- `outcome`, judged from evidence and never from the teacher's own claim:
  `failure` when the next user message corrects the task ("that didn't work",
  "still failing", "revert"), otherwise the *last* test-like command decides
  (`pytest`, `vitest`, `tsc`, `ruff check`, `npm test`, ...; exit code first,
  then its output); `unverified` when nothing was checked;
- `model`, `model_location` (`cloud` or `local`), `files`, `tools`, `areas`;
- `scrub`: the scrubber's version and what it did (counts only).

opencode is read from `~/.local/share/opencode/opencode.db` (`session_v2`,
`session_message`): the database is cloned first (an APFS clone, `cp -c`, so a
many-gigabyte database costs nothing), opened read-only, and the clone deleted.
Cline is read from `~/.cline/data/sessions/<id>/<id>.json` and
`<id>.messages.json`.

## 3. The scrubber

Nothing is written, printed or sent before it runs, and only counts are ever
printed. Three layers, strictest first:

1. **Drop the step.** A step whose input touched `.env` files (templates such as
   `.env.example` are fine), Fernet keys or decryption, encrypted identity
   columns, an identity database, `printenv` or a container's environment,
   ssh keys, keychains or credential files; or whose output carried a
   recognisable credential or env dump, is removed whole.
2. **Drop the task.** A goal about secrets (rotate or decrypt keys, `.env`), a
   task that lost 3 or more steps (or over a quarter of them) to rule 1, or one
   that still fails the leak check after masking, leaves only a stub: ids,
   model, dates, counts and the reason. No goal, no steps, no answer.
3. **Drop the session.** When any task of a *cloud* session asked about secrets
   or saw a live credential, every task of that session becomes a stub: a
   teacher that saw a decrypted key may echo it later in a form no pattern
   catches. `--per-task-policy` turns this off, explicitly.

Everything that remains is masked: provider keys (OpenAI/OpenRouter, Anthropic,
GitHub, Hugging Face, Slack, AWS, Google, ...), JWTs, Fernet keys and tokens,
private keys, bearer tokens and auth headers (`X-Internal-Secret` included),
`NAME=value` with a secret-looking name, quoted password/token assignments, URL
passwords, long hex runs (not a 40-character git commit), high-entropy strings,
emails, phone numbers, IP addresses (`--keep-ips` keeps them), home-directory
user names, every secret-looking value in the local `.env` files, and the
household names from a local file. A final leak check runs on the result; any
hit drops the task instead.

**Names are never in code.** Before every upload the collector fetches them from
Identity's family directory through the gateway (`GET /api/trainer/teacher/scrub-names`,
with the uploader key): usernames, Nextcloud accounts, display names and each
word of them. They are cached in the state directory (`names.json`, mode 600).
**Fail closed:** when they cannot be fetched and the cache is older than 24
hours, nothing is scrubbed or sent, the sync state is not advanced, the
heartbeat reports the failure, and the next run retries. A local file
(`--names-file`, default `~/.config/sharedllm/teacher-names.txt` when it exists)
adds more names; it is optional. A run with no names at all stops unless
`--no-names` says so (only sensible for a counts-only dry run).

The gateway runs the leak check again on every trace it receives and refuses a
trace scrubbed by another scrubber version, a dropped trace that still carries
content, or any trace with a secret left in it (it names the kind, never the
value). The shared fixture `tools/opencode-trainer-sync/test/fixtures/secret_cases.json`
holds synthetic secrets of every kind plus a session shaped like one that
decrypted keys; the Python tests and the plugin's node tests both prove none of
it survives.

A dry run over the Mac's sessions on 2026-11-21 (counts only): 23 sessions,
1388 tasks (142 verified successes, 131 failures, 1115 unverified); 889 tasks
dropped (795 by the session rule, 89 for too many secret steps, 5 for their
goal); 499 kept, masked or clean. The session that printed decrypted keys is
dropped whole by the session rule.

## 4. How the trainer uses them

**a. Worked-example lessons.** `POST /api/trainer/teacher/lessons` writes each
verified success (outcome `success`, at least two useful steps, not dropped)
into `system_learnings` as one lesson:

> For a task like "Fix the off-by-one in the pager" (services/gateway), the steps
> that worked: read pager.py; searched for 'def pages'; edited pager.py; checked
> with `pytest services/gateway/tests/test_pager.py -q`.

Same document shape as every other lesson (`id`, `rule`, `root_cause`,
`outcome`, `confidence` 0.6, `tags` = `teacher`, `worked_example`, step kinds,
areas; metadata `type: worked_example`, `source_trace`), so the orchestrator
already retrieves it into missions (`Apply: [id]`), and dreaming compacts,
merges and prunes it like any lesson. Rules are capped at 480 characters; a
lesson is written once per trace, and a task whose goal overlaps an existing
lesson's by 80% or more is skipped. A refused RAG write is reported, not counted.

**b. Evaluation cases.** Every verified trace that edited a file becomes a case
in the `teacher` bucket: the teacher's task as the prompt, "plan it in 8 lines",
checked by the files the teacher changed (`must_include` their names) and the
check it ran (`must_match` the runner, e.g. `pytest`). Run them with
`POST /api/trainer/eval {"buckets":["teacher"]}` (or the Lab's Evaluate with the
`teacher` chip). Reports keep `by_bucket.teacher`, which is the score history
the Lab card draws. A failed teacher case files a `teacher_gap` proposal in the
backlog (write the worked examples, then re-run), alongside the usual bucket
proposal.

**c. Fine-tuning rows.** `POST /api/trainer/teacher/dataset
{"format": "openai"|"sharegpt", "eval_share": 0.1, "include_unverified": false}`
writes `data/trainer/datasets/teacher-<format>-<stamp>-{train,eval}.jsonl`:

- system: Raven's live `raven_autonomous_protocol` from Identity (the export
  stops with a 503 if it cannot be read: rows must match the real prompt);
- user: the goal; then one assistant turn per step with a tool call, and its
  tool result; then the final answer;
- tool calls are mapped onto **Raven's own tools**: shell to
  `WorkspaceShellRequest` (simple `git status/diff/add/commit/...` to
  `GitOperationRequest`), read to `WorkspaceFileReadRequest`, edit to
  `WorkspaceFilePatchRequest`, write to `WorkspaceFileWriteRequest`, grep to
  `WorkspaceSearchRequest`; steps with no Raven equivalent (web, questions,
  sub-agents) are left out and counted in `meta.unmapped_tools`;
- `openai`: `{"messages": [...], "tools": [...], "meta": {...}}` with
  `tool_calls` and `role: tool` messages (axolotl `chat_template`, Unsloth,
  TRL `SFTTrainer`); `sharegpt`: `{"conversations": [...]}` with Hermes-style
  `<tool_call>` / `<tool_response>` tags and the tool list in the system turn;
- split train/eval by a hash of the trace id, so a trace never moves between
  splits from one export to the next.

## 5. Fine-tuning: realistic options for this hardware

The decision is the owner's. The facts:

- **Alpaca's card cannot fine-tune the production model.** KAT-Coder is a
  35B-parameter MoE (3B active). QLoRA needs the whole base in 4-bit (about
  18-20 GB) plus activations; the RTX 4060 has 8 GB, and 30 GB of system RAM
  does not change that.
- **The system prompt is long.** Raven's protocol is about 27,000 characters
  (roughly 7,000 tokens) before the task starts, so useful rows run 8,000 to
  16,000 tokens.
- **The data is small.** Around a hundred verified successes today. LoRA on that
  overfits quickly: rank 16, 1 to 3 epochs, watch the eval split.

Options, cheapest first:

1. **Retrieval only (no training).** Write the worked-example lessons and
   measure the `teacher` bucket. Zero risk, works with any model, and the
   score history shows whether it helps. Start here.
2. **QLoRA a small dense model on the 4060** (Qwen3 8B, ornith 9B, or a 4B for
   headroom) with Unsloth: 4-bit base, `max_seq_length` 8192, batch 1,
   gradient accumulation 8, gradient checkpointing. An 8B at 8K tokens is near
   the limit of 8 GB; a 4B is comfortable. Then merge, convert with llama.cpp's
   `convert_hf_to_gguf.py`, quantize (`llama-quantize ... Q4_K_M`), and import
   into Alpaca's router (`alpaca-puller.py` import, then the indexer). This
   gives a small specialist, not a better KAT-Coder.
3. **Rent a GPU for the 35B MoE** (one A100 80 GB or H100 for a few hours).
   Train a LoRA on the *same* Hugging Face weights the production GGUF was
   made from, convert only the adapter with llama.cpp's `convert_lora_to_gguf.py`,
   and load it on top of the existing GGUF (`--lora`; Alpaca lists adapters at
   `/admin/lora`, which `GET /api/trainer/hardware` reports). No re-quantizing
   the base, and the adapter can be removed in one restart. The dataset must
   not leave the Mac or the gateway unscrubbed (it never is), but renting puts
   the scrubbed rows on someone else's machine: decide whether that is
   acceptable.

Whichever is chosen, compare the `teacher` bucket before and after on the eval
split's tasks, and keep the adapter only if it wins.

A minimal Unsloth run for option 2 or 3 (illustrative):

```python
from unsloth import FastLanguageModel
from datasets import load_dataset
from trl import SFTTrainer, SFTConfig

model, tok = FastLanguageModel.from_pretrained("Qwen/Qwen3-8B", max_seq_length=8192, load_in_4bit=True)
model = FastLanguageModel.get_peft_model(model, r=16, lora_alpha=16, use_gradient_checkpointing="unsloth")
data = load_dataset("json", data_files={"train": "teacher-openai-...-train.jsonl", "eval": "teacher-openai-...-eval.jsonl"})
fmt = lambda row: {"text": tok.apply_chat_template(row["messages"], tools=row["tools"], tokenize=False)}
trainer = SFTTrainer(model=model, tokenizer=tok, train_dataset=data["train"].map(fmt), eval_dataset=data["eval"].map(fmt),
                     args=SFTConfig(per_device_train_batch_size=1, gradient_accumulation_steps=8, num_train_epochs=2,
                                    learning_rate=1e-4, output_dir="out", dataset_text_field="text"))
trainer.train()
model.save_pretrained("lora")
```

## 6. Routing: which work may go to a cloud teacher

A trace can only be collected from work a cloud model was allowed to see, so
choose the model before the work starts:

```bash
python scripts/teacher_route.py "Refactor the pager tests" --path services/gateway/pager.py
# cloud: public code reasoning ...                                      (exit 0)
python scripts/teacher_route.py "Why does the family calendar sync fail on the live server"
# local: it involves household or personal data; it touches production ...  (exit 1)
```

Local-only (use the alpaca model) when the task:

- involves secrets, keys, tokens, passwords, decryption, `.env`, identity
  databases, ssh or keychains;
- involves household or personal data (family members, chores, locations,
  calendars, contacts, private chats, guardian or health data, photos,
  recordings), or names a household member (from the names file);
- touches production: the live servers, `docker exec`, database dumps,
  backups, user accounts, credentials, deploys;
- touches `.env*` (not templates), `data/`, backups, `services/identity/`,
  `services/guardian/`, `private_chat*`.

Everything else (public code reasoning: refactors, tests, type errors, UI
work, docs) may go to a teacher. When unsure, local.

In opencode, keep two agents and pick per task: one on the alpaca model for
local-only work and one on the cloud teacher. Keep the existing
`permission.read` denial of `.env` files for the cloud agent; it is the reason
most sessions never touch a secret at all. The plugin below never syncs a
cloud session that touched secrets (rule 3).

## 7. The opencode plugin

`tools/opencode-trainer-sync/` syncs finished sessions automatically: on
`session.idle` (debounced) it runs `collect_teacher_traces.py sync --session <id>`,
which sends only the tasks not sent before (state in
`~/.local/state/sharedllm-teacher/`), plus the previous last task again so a
later correction updates its outcome. A failed upload is queued on disk
(scrubbed, mode 600) and retried; a collector failure keeps the session id and
retries it; every failure is written to opencode's log (`service:
trainer-sync`). Opt-in per project; see its README for installation. It is not
installed by this change.

## 8. Hands-off: what runs on its own

**On the Mac.** A launchd agent (`tools/teacher-sync-launchd/`) runs every 30
minutes, and once at login:

1. `collect_teacher_traces.py sync --source all`: every opencode and Cline
   session touched since the last run (with an hour of overlap), only the tasks
   not sent before; names fetched from Identity first (fail closed); failed
   uploads queued, scrubbed, mode 600;
2. `collect_teacher_traces.py flush`: the queue;
3. after each, a heartbeat to `POST /api/trainer/teacher/heartbeat` (counts and
   an error line only).

Output goes to `~/Library/Logs/sharedllm-teacher/sync.log`, rotated at 1 MiB
with 5 kept. The opencode plugin is the per-session trigger; launchd is the
catch-up that also covers Cline.

**On the gateway.** The background worker runs a teacher pass every 30 minutes
(`services/gateway/trainer/teacher_auto.py`) while `trainer_teacher_auto` is
true (the default; `raven_suspended` pauses it):

1. worked-example lessons for newly imported verified traces;
2. the `teacher` bucket, every `trainer_teacher_eval_interval_hours` (24), and
   only when Alpaca's readiness gate says the card is idle (a busy card is
   "waiting", not a failure); each run is a report, so the trend draws itself;
3. a fine-tuning snapshot once `trainer_teacher_snapshot_every` (100) new
   verified traces have arrived since the last one, with an admin notice that
   carries counts only;
4. the Mac's heartbeat: a reported error, or no heartbeat for
   `trainer_teacher_sync_stale_minutes` (180), is a failure.

`GET /api/trainer/teacher/status` and the Lab card show the last sync (with its
queue length), the last lesson write, the last evaluation, the last snapshot
and any failure with its reason. A failure notifies the admins once when it
starts (a notice telemetry did not accept is retried) and clears when the part
recovers. The four settings are seeded by Identity on start (Admin, LLM &
Settings). The Lab buttons remain for manual runs.

## 9. Learning from Raven's own failed missions

The same pass learns from what the local model got wrong
(`services/gateway/trainer/raven_failures.py`), server side, with no Mac step.
It reads finished missions from Identity (`/api/raven/missions`, each new failed
one once, with its `output_log`) and:

1. **Classifies the cause first.** Infrastructure (the model unavailable or
   busy, timeouts, 5xx, connection errors, restarts, orphaned or pre-empted
   jobs) is counted and never becomes a lesson, the trainer's existing outage
   rule; a repeated timeout is an outage, not a loop. Model mistakes:
   `loop`, `iteration_cap`, `wrong_tool`, `bad_arguments`, `hallucinated_path`,
   `ignored_errors`, `unverified_success`, `gave_up`. A user abort with no
   visible cause is recorded but not learnt from.
2. **Writes an "avoid" lesson** in `system_learnings` (`type: avoid`, tagged
   `raven_failure`, the kind and the tool), keyed by kind, tool and the error's
   shape (numbers, quoted values and paths folded), so one recurring mistake is
   one lesson whose confidence rises from 0.4 by 0.1 each time it recurs, up to
   0.8. At most 10 lessons per pass.
3. **Keeps a `raven_failures` case**: the mission's goal, "plan it again in 8
   lines", checked so that the answer names a way to verify and does not repeat
   the specific failing call (a hallucinated path, a wrong tool). The teacher
   pass evaluates this bucket with the `teacher` bucket on an idle card; the Lab
   card shows the trend. The newest 60 cases are kept.
4. **Pairs** a failed attempt with the verified teacher trace whose goal is
   closest (at least 40% word overlap) in the dataset export, as
   `teacher-<fmt>-<stamp>-contrastive.jsonl`: `{prompt, chosen, rejected}` rows
   in the conversational form TRL's DPO trainer reads.

Mission text can hold household data, so goals, results and steps are scrubbed
with the same scrubber before anything is kept, written or exported; the names
come from Identity's internal name roster (`GET /api/internal/name-roster`),
and the pass refuses to run without them (fail closed, reported as a failure).

**Loops stop early now.** The agent loop keeps `(call, ok, error)` for every
tool call and `detect_mission_loop` flags the same read-only or failing call
5 times, or the same error 4 times, within the last 16 calls, consecutive or
not (writes may recur: edit, test, edit is normal). The model is nudged once;
when the same call or error comes back twice more, the mission stops with
`ERROR: looping: <call or error> repeated N times`, which the classifier files
as a loop. A trivial mission that used to wander to iteration 38 of 60 now stops
after about a dozen calls with the cause named.

## 10. Runbook

Once:

1. **An uploader key.** Use an admin's API key, or a dedicated account named in
   the gateway's `TRAINER_TEACHER_UPLOADERS` (comma-separated usernames, server
   `.env`, read at startup). Save it to a file and `chmod 600` it, e.g.
   `~/.config/sharedllm/trainer-key`.
2. **Install the agent.**
   ```bash
   tools/teacher-sync-launchd/install.sh --gateway https://<jarvis> \
     --key-file ~/.config/sharedllm/trainer-key
   ```
   It is idempotent, prints what it installed, and refuses a key file others
   can read. `uninstall.sh` removes it.

That is all. Optional:

- sessions never to collect, one id per line, in
  `~/.config/sharedllm/teacher-exclude.txt`;
- extra names in `~/.config/sharedllm/teacher-names.txt`;
- look first, counts only:
  `python scripts/collect_teacher_traces.py collect --since 2026-11-01 --dry-run --no-names`;
- the opencode plugin for immediate per-session syncs (its README).

Manual runs from a shell use the same endpoints: `GET /api/trainer/teacher`,
`GET /api/trainer/teacher/status`, `GET /api/trainer/teacher/traces?outcome=&status=`,
`POST /api/trainer/teacher/lessons`, `POST /api/trainer/teacher/dataset`.

# Workspace Runtime

## What It Is

The Workspace Runtime is a dedicated microservice for operating on mounted local
workspaces inside the SharedLLM Docker stack.

It exists to make agentic workflows concrete instead of purely prompt-driven.
Its purpose is to give the system a safe, explicit runtime for repository and
workspace inspection rather than pretending that storage-backed snapshots are a
live coding environment.

A workspace is not limited to source code. A workspace can also contain notes,
documents, media assets, generated outputs, transcripts, and other user-owned
files that need the same safe local mutation model.

## What It Does Today

Current implemented capabilities:

- load a read-only workspace registry from `config/workspaces.json`
- persist workspace registry state in the workspace runtime database so edits
  survive container recreates
- bootstrap missing workspaces from Git into the local workspace root
- create per-user workspace records when a user-scoped repo is requested for
  the first time
- resolve a caller's user identity through the Identity service when user
  context is provided
- resolve a workspace ID to a real mounted local path
- enforce policy-based workspace visibility such as `authenticated` and
  `admin_only`
- enforce reduced capability sets for `system` workspaces unless the resolved
  caller is admin
- enforce that all workspace access stays under the configured workspace root
- read files from a workspace safely
- list workspace files safely for context gathering
- write files to authorized workspaces with optional optimistic conflict checks
- use the same file APIs for non-code assets and generated artifacts alongside
  source files
- report `git status`
- return `git diff`
- stage files with `git add`
- create commits with Git author metadata derived from Identity
- create branches for isolated changesets
- fetch, pull, and rebase Git branches through configured remotes
- push branches through configured Git remotes using resolved provider credentials
- scan a workspace's designated provider folder through the Storage service
- sync changed local files into the designated provider path
- execute a single-file write -> sync -> test -> commit -> push workflow
- run targeted `pytest` commands inside a workspace
- expose a health endpoint so the Gateway can include it in readiness checks

## What It Is Meant To Do

The intended longer-term role is broader than code-only execution. It should
become the runtime substrate for workspace-scoped agentic tasks, including:

- code edits against authoritative local Git checkouts
- git lifecycle operations such as branch creation, commit, and push
- targeted test and lint execution
- note and document editing within the same mounted workspace
- synthesis of master documents or rollups from multiple workspace files
- metadata sidecar creation for workspace assets
- orchestration of local enrichment tools such as transcription pipelines
- storage of STT transcripts, TTS outputs, and other generated media artifacts
  under the same workspace root
- eventual coordination with provider sync flows after local changes are
  finalized

## What It Deliberately Does Not Do

Today it does not:

- mutate the workspace registry
- perform broad folder mirroring back to providers
- sync non-text assets back to providers
- replace the Storage service
- replace the RAG service
- execute smart-home or media commands

## Why This Service Exists

SharedLLM has an architectural split:

- local Git workspaces are the authoritative source for active code state
- storage backends such as Nextcloud are authoritative for discovery and
  companion documents

That split requires a local runtime. Without it, the system can talk about code
but cannot safely act on a real workspace. The Workspace Runtime is the first
service created specifically to fill that gap.

## Relationship To Other Services

- **Gateway**
  Routes coding and librarian requests and will eventually orchestrate this
  service directly for agentic workspace operations.

- **Identity**
  Resolves user-scoped context that can later be tied to workspace registry
  entries.

- **Storage**
  Handles provider discovery, listing, search, and indexing of companion
  content. It is not the authoritative runtime for active Git work.

- **RAG**
  Supplies semantic context from repository-adjacent documents and other indexed
  material.

## Current API Surface

- `GET /health`
- `GET /workspaces`
- `POST /workspace/resolve`
- `POST /workspaces/bootstrap`
- `POST /files/read`
- `POST /files/list`
- `POST /files/write`
- `POST /git/status`
- `POST /git/diff`
- `POST /git/add`
- `POST /git/commit`
- `POST /git/branch/create`
- `POST /git/fetch`
- `POST /git/pull`
- `POST /git/rebase`
- `POST /git/push`
- `POST /provider/scan`
- `POST /provider/sync/file`
- `POST /workflow/write-sync-commit`
- `POST /tests/pytest`

## Current Safety Model

- every non-health endpoint requires `X-Internal-Secret`
- registry-backed workspaces can require authenticated or admin identities
  without embedding usernames in the registry
- admin overrides come from the Identity service's DB-backed `is_admin` flag
- system workspaces can expose a narrower capability set than normal user
  workspaces
- write-side Git actions require the `git_write` capability unless the caller
  is admin
- workspace paths must resolve under `WORKSPACE_RUNTIME_ROOT`
- file access is blocked if it escapes the workspace
- file writes can enforce `expected_sha256` before replacing existing content
- provider sync requires a configured provider binding such as `nextcloud_path`
  plus resolved provider credentials from Identity
- pytest targets reject absolute paths, parent traversal, and option-like
  arguments
- Git author metadata is derived from resolved GitHub or GitLab identity fields
  when explicit author information is not supplied
- chat-driven README generation is handled in the Gateway and uses
  `workspace_runtime` for workspace inspection, file writes, and provider sync

## Workspace Self-Edit Workflow

Raven is allowed to commit and push autonomously, but only to non-protected
review branches.

Required branch policy:

- direct autonomous pushes to protected branches such as `main`, `master`,
  `development`, `dev`, and `release/*` are blocked in `workspace_runtime`
- Raven should create or switch to a task branch such as `raven/<task-id>`
- when a workflow starts on a protected branch and `auto_create_review_branch`
  is enabled, the runtime creates a `raven/<user>/<file>-<timestamp>` branch
  from the workspace `default_branch`
- protected branches should remain protected in the Git provider with PR review
  and status checks

Required verification order for `POST /workflow/write-sync-commit`:

1. resolve workspace and branch policy
2. write the file
3. run lint on the touched file or explicit `lint_paths`
4. run targeted `pytest` when requested
5. create the commit
6. push only if the branch is non-protected and verification passed
7. sync to the provider only after verification and Git lifecycle checks

Autonomous push guardrail:

- `push=true` requires `pytest_targets`
- if the resolved push branch matches the protected-branch policy, the request
  fails before push

Review metadata:

- workflow responses now include a `review` object intended for PR creation or
  human code review handoff
- the review payload includes `title`, `head`, `base`, changed files, lint
  results, pytest results, and a reviewer checklist

## Expected Near-Term Expansion

The next useful features are:

1. richer file mutation support beyond direct text writes
2. richer multi-file workflow endpoints with strict remote-auth controls
3. workspace registry APIs instead of a static JSON file
4. gateway-level orchestration for coding tasks that need real file changes
5. provider sync expansion from single-file text writeback into broader folder
   mirroring and non-text asset handling
6. optional note/document/transcription endpoints for broader workspace
   operations

## Remaining Implementation Work

The current service is still an inspection/runtime substrate, not a full
agentic workspace engine. The main unfinished pieces are:

1. **DB-backed workspace registry**
   Move workspace definitions and access policy out of static JSON and into a
   service-owned or Identity-linked database model.
2. **Write path expansion**
   Extend mutation beyond direct text replacement into richer create/update/delete
   flows with better conflict handling and auditability.
3. **Git lifecycle completion**
   Add pull/rebase behavior with explicit scope controls and remote credential
   handling.
4. **Provider synchronization**
   Reflect authoritative local changes back to storage providers where that is
   appropriate. The first thin slice now supports explicit single-file text
   writeback to Nextcloud-backed workspace folders, but not full folder mirroring
   or non-text assets.
5. **Gateway orchestration**
   Let the gateway route coding and workspace tasks into this service rather
   than only using prompt-level disclaimers.
6. **Non-code workspace actions**
   Add note editing, document synthesis, metadata sidecars, and transcription
   orchestration under the same workspace safety model.
7. **Operational hardening**
   Add better audit logs, explicit command allowlists, per-workspace policy,
   and clearer separation between CI-safe tests and local/server-only tests.

## Current Progress

The next thin slice is underway and partially working:

- `workspace_runtime` now exposes safe workspace file listing in addition to
  read/write, git, and provider sync.
- The gateway now has a README-generation path that can resolve a workspace,
  gather local repo context, call the coding model, write `temp/README.md`, and
  sync it to the mapped Nextcloud folder.
- Live remote validation has already confirmed workspace writeback and
  Nextcloud sync for temp files on the hosted `SharedLLM` machine.
- The remaining blocker is test harness stability for the new gateway
  orchestration path; the runtime and provider pieces are already exercised.

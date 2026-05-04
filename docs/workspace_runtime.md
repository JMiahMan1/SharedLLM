# Workspace Runtime

## What It Is

The Workspace Runtime is a dedicated microservice for operating on mounted local
workspaces inside the SharedLLM Docker stack.

It exists to make agentic workflows concrete instead of purely prompt-driven.
Its purpose is to give the system a safe, explicit runtime for repository and
workspace inspection rather than pretending that storage-backed snapshots are a
live coding environment.

## What It Does Today

Current implemented capabilities:

- load a read-only workspace registry from `config/workspaces.json`
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
- report `git status`
- return `git diff`
- stage files with `git add`
- create commits with Git author metadata derived from Identity
- create branches for isolated changesets
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
- `POST /files/read`
- `POST /files/list`
- `POST /files/write`
- `POST /git/status`
- `POST /git/diff`
- `POST /git/add`
- `POST /git/commit`
- `POST /git/branch/create`
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

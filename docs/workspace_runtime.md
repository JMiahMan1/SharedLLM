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
- resolve a workspace ID to a real mounted local path
- enforce that all workspace access stays under the configured workspace root
- read files from a workspace safely
- report `git status`
- return `git diff`
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

- mutate files
- mutate the workspace registry
- create commits or push to remotes
- write back to Nextcloud or another provider
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
- `POST /git/status`
- `POST /git/diff`
- `POST /tests/pytest`

## Current Safety Model

- every non-health endpoint requires `X-Internal-Secret`
- workspace paths must resolve under `WORKSPACE_RUNTIME_ROOT`
- file access is blocked if it escapes the workspace
- pytest targets reject absolute paths, parent traversal, and option-like
  arguments

## Expected Near-Term Expansion

The next useful features are:

1. file write endpoints with path safety and optimistic conflict handling
2. git add/commit/push endpoints with strict scope controls
3. workspace registry APIs instead of a static JSON file
4. gateway-level orchestration for coding tasks that need real file changes
5. optional note/document/transcription endpoints for broader workspace
   operations

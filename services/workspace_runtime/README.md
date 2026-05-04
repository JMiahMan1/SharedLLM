# Workspace Runtime Service

The Workspace Runtime service is the first concrete agentic substrate for
SharedLLM's workspace-centric workflows.

It provides a safe, mounted-workspace interface for:

- listing registered workspaces
- resolving a workspace to a real local path
- reading files inside that workspace
- reporting `git status`
- returning `git diff`
- running targeted `pytest` commands

## What It Does Today

Implemented today:

- read-only workspace registry loading
- user-scoped workspace filtering through the Identity service
- policy-based workspace visibility through `access_policy` rather than
  hardcoded usernames
- limited `system` workspaces with capability restrictions unless the resolved
  caller is admin
- safe workspace resolution under the mounted root
- read-only file access
- `git status` and `git diff`
- targeted `pytest` execution

## Service Schematic

```mermaid
flowchart TD
    Gateway[Gateway / Internal Caller] -->|X-Internal-Secret| WorkspaceRuntime
    WorkspaceRuntime --> Registry[config/workspaces.json]
    WorkspaceRuntime --> Identity[Identity /api/resolve]
    WorkspaceRuntime --> MountedWorkspace[/workspace/...]
    MountedWorkspace --> Git[git status / git diff]
    MountedWorkspace --> Pytest[python -m pytest]
```

## Request Flow

```mermaid
flowchart TD
    Start[Request with workspace_id + user context] --> ResolveIdentity[Resolve caller via Identity]
    ResolveIdentity --> LoadRegistry[Load workspace registry entry]
    LoadRegistry --> Policy[Apply access_policy and scope rules]
    Policy --> PathCheck[Resolve path under WORKSPACE_RUNTIME_ROOT]
    PathCheck --> Capability[Check requested capability]
    Capability --> Execute[Read file / git / pytest]
```

## Registry Schema

Each workspace entry can currently define:

- `id`: stable workspace identifier
- `display_name`: human-friendly label
- `local_path`: mounted path relative to `WORKSPACE_RUNTIME_ROOT`
- `nextcloud_path`: discovery path used by storage-side tooling
- `git_remote`: expected Git remote name
- `default_branch`: default branch for future Git lifecycle actions
- `sync_mode`: current authority model
- `access_policy`: `authenticated` or `admin_only`
- `scope`: `user` or `system`
- `capabilities`: optional override list for allowed operations

## Current API Surface

- `GET /health`
- `GET /workspaces`
- `POST /workspace/resolve`
- `POST /files/read`
- `POST /git/status`
- `POST /git/diff`
- `POST /tests/pytest`

## Access Model

- `authenticated` workspaces require a resolved Identity user
- `admin_only` workspaces require `is_admin=True` from Identity
- `system` workspaces can expose a narrower capability set than normal
  workspaces
- admin users can bypass system capability limits when the request should be
  allowed at the identity-policy layer

## What It Is Meant To Do

This service is intentionally broader than code-only execution. Its target role
is to operate on mounted workspaces as a whole, including:

- code repositories
- notes and supporting documents
- synthesized master documents
- metadata sidecars
- local enrichment flows such as transcription or document generation

## Safety Model

- All workspace paths must resolve under `WORKSPACE_RUNTIME_ROOT`.
- Registry entries declare access policy such as `authenticated` or
  `admin_only`, while admin status comes from the Identity service.
- Registry entries can declare `scope: "system"` and a reduced `capabilities`
  list for more sensitive workspaces.
- Relative file reads and pytest targets are checked for path traversal.
- The service is intended for internal use and requires `X-Internal-Secret`
  for non-health endpoints.

## Current Scope

Implemented:

- read-only file access
- policy-based workspace resolution
- git inspection
- targeted test execution

Not yet implemented:

- file writes
- git commit/push APIs
- workspace registry mutation APIs
- direct Gateway orchestration against these endpoints
- DB-backed workspace registry records
- document or note mutation APIs

## Remaining Work

- move workspace definitions from static JSON into a DB-backed registry
- add file mutation endpoints with safety and conflict controls
- add Git branch/add/commit/push operations
- sync local authoritative changes back to provider-backed storage where needed
- let the Gateway orchestrate this service directly for agentic tasks
- add note, document, metadata, and transcription operations under the same
  workspace policy model

See also:

- [docs/workspace_runtime.md](/home/jeremiah/Summers Drive/Code/SharedLLM/docs/workspace_runtime.md)

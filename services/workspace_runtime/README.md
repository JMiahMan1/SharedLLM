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
- limited `system` workspaces with capability restrictions unless the resolved
  caller is admin
- safe workspace resolution under the mounted root
- read-only file access
- `git status` and `git diff`
- targeted `pytest` execution

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
- Registry entries can restrict access with `allowed_users`, resolved through
  the Identity service.
- Registry entries can declare `scope: "system"` and a reduced `capabilities`
  list for more sensitive workspaces.
- Relative file reads and pytest targets are checked for path traversal.
- The service is intended for internal use and requires `X-Internal-Secret`
  for non-health endpoints.

## Current Scope

Implemented:

- read-only file access
- git inspection
- targeted test execution

Not yet implemented:

- file writes
- git commit/push APIs
- workspace registry mutation APIs
- direct Gateway orchestration against these endpoints

See also:

- [docs/workspace_runtime.md](/home/jeremiah/Summers Drive/Code/SharedLLM/docs/workspace_runtime.md)

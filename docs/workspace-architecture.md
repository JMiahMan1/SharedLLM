# Workspace Architecture Review

## Overview

Workspaces are the foundational abstraction for code, content, and data management in SharedLLM. They provide isolated, version-controlled environments that Raven (the autonomous agent) and users can interact with for reading, writing, testing, and syncing files. Each workspace maps to a git repository and has its own path, permissions, sync configuration, and lifecycle.

---

## Directory Structure

### Target Layout

```
/workspaces/                          # WORKSPACE_RUNTIME_ROOT (container)
├── system/                           # System workspaces (admin-managed)
│   ├── sharedllm/                    # SharedLLM system workspace
│   │   ├── .git/
│   │   ├── services/
│   │   ├── docker-compose.yml
│   │   └── ...
│   └── <other-system-workspace>/
│
└── users/                            # User workspaces
    ├── jeremiah/                     # User-specific directory
    │   ├── my-project/
    │   └── notes/
    ├── alice/
    │   └── research/
    └── ...
```

### Host Mapping

```
/home/jeremiah/workspaces/            # WORKSPACE_HOST_PATH (host)
├── system/
│   └── sharedllm/
└── users/
    └── jeremiah/
```

Docker volume mount: `${WORKSPACE_HOST_PATH:-/home/jeremiah/workspaces}:/workspaces`

### Reserved Names (blocked)

- `users` -- reserved for user workspace directory
- `workspaces` -- reserved (root name)
- `system` -- reserved for system workspaces (admin only)

---

## Workspace Model

### Fields (SQLModel)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | `str` | - | Unique identifier (PK). Cannot be "users", "workspaces", or "system" |
| `display_name` | `str` | - | Human-readable name |
| `scope` | `str` | `"user"` | `"system"` or `"user"` -- determines directory placement |
| `owner_user` | `str \| None` | `None` | Identity service user ID. `"default"` = shared profile |
| `is_default` | `bool` | `False` | Marks as default workspace for git/tool operations |
| `access_policy` | `str` | `"authenticated"` | `"authenticated"` or `"admin_only"` |
| `container_mount_path` | `str \| None` | `None` | Path relative to scope root (e.g., `sharedllm` for system, `jeremiah/my-project` for user) |
| `host_mount_path` | `str \| None` | `None` | Absolute host path (reference only) |
| `repo_url` | `str \| None` | `None` | Git remote URL |
| `git_remote` | `str` | `"origin"` | Git remote name |
| `default_branch` | `str \| None` | `"main"` | Default branch for pull/push |
| `sync_mode` | `str` | `"local_git_authoritative"` | Sync strategy |
| `capabilities` | `List[str]` | `[]` | Allowed operations |
| `auto_pull_enabled` | `bool` | `False` | Webhook-triggered git pull |
| `auto_backup_enabled` | `bool` | `False` | Nextcloud backup after pull |
| `webhook_token` | `str \| None` | `None` | Plaintext (migrated to enc) |
| `webhook_token_enc` | `str \| None` | `None` | Fernet-encrypted webhook secret |
| `quarantined` | `bool` | `False` | Auto-flagged after repeated Raven failures |
| `last_raven_mission_id` | `int \| None` | `None` | Last mission that touched this workspace |
| `nextcloud_path` | `str \| None` | `None` | Remote path for Nextcloud sync |
| `excludes` | `List[str]` | `[]` | Directories to exclude from sync |

### Computed Properties

- `effective_container_path` → `container_mount_path or local_path`
- `resolved_path` → `get_workspace_root() / scope / container_mount_path`

---

## Path Resolution

### System Workspaces (`scope: "system"`)

```
resolved_path = WORKSPACE_RUNTIME_ROOT / "system" / container_mount_path
```

Example: `container_mount_path = "sharedllm"` → `/workspaces/system/sharedllm`

### User Workspaces (`scope: "user"`)

```
resolved_path = WORKSPACE_RUNTIME_ROOT / "users" / owner_user / container_mount_path
```

Example: `owner_user = "jeremiah"`, `container_mount_path = "my-project"` → `/workspaces/users/jeremiah/my-project`

### Derivation on Bootstrap

When `create_if_missing=true` and no `container_mount_path` is provided:

```python
# System workspace
container_mount_path = _derive_repo_name(repo_url)  # e.g., "sharedllm"

# User workspace
container_mount_path = _derive_repo_name(repo_url)  # e.g., "my-project"
# Parent directory created automatically: users/{owner_user}/
```

---

## Permissions & Access Control

### Layers

1. **Internal Secret** (`X-Internal-Secret`) -- service-to-service auth, required on all workspace_runtime endpoints

2. **Scope-based visibility**:
   - `scope: "system"` -- only visible to admins (`is_admin=True`)
   - `scope: "user"` -- visible to owner and admins
   - `owner_user: "default"` -- visible to all authenticated users (shared profile)

3. **Access Policy**:
   - `"authenticated"` -- any resolved user can access
   - `"admin_only"` -- only admins can access

4. **Capabilities** (per-workspace operation whitelist):
   - System workspaces: `["read", "git_status", "git_diff"]`
   - User workspaces: `["read", "write", "git_status", "git_diff", "git_write", "pytest"]`
   - Admins bypass capability checks

5. **Quarantine**:
   - Auto-triggered after `RAVEN_QUARANTINE_THRESHOLD` (3) failures within `RAVEN_QUARANTINE_WINDOW_SECONDS` (600)
   - Blocks write-sync-commit operations
   - Revert clears quarantine

6. **Git Push Protection**:
   - Protected branches: `main, master, development, dev, release/*`
   - Raven creates review branches instead: `raven/{user}/{branch}-{timestamp}`

### Reserved Name Enforcement

Workspace IDs cannot be: `users`, `workspaces`, `system` (case-insensitive). This prevents directory collision with the scope structure.

---

## Service Architecture

### workspace_runtime (:8007)

Primary workspace service. Handles:
- Workspace CRUD (create, read, update, delete)
- File operations (read, write, patch, delete, search, list)
- Full git lifecycle (status, diff, add, commit, push, pull, fetch, checkout, branch, stash, remote, show, rebase, revert)
- Bootstrap (clone repo, initialize workspace)
- Webhook endpoints (git-pull trigger)
- Workflow orchestration (write → sync → commit)
- Pytest execution
- Lint execution
- Nextcloud provider sync
- Auto-quarantine

### execution (:8003)

Legacy execution bridge. Handles:
- Workspace file operations (read, write, patch, search, shell, lint) -- operates directly on `WORKSPACE_ROOT`
- Git operations (simpler subset) -- resolves workspace via workspace_runtime API
- TTS, media, HA, Docker, announcement handlers

**Migration path**: Execution service workspace/git handlers should eventually delegate to workspace_runtime for all operations. Currently, the execution git handler calls workspace_runtime's `/workspaces/resolve` to get the resolved path.

### gateway (:11435)

Orchestration layer. Handles:
- Raven agent loop (dispatches workspace/git tool calls)
- Tool routing: `WorkspaceBootstrapRequest` → workspace_runtime, `GitOperationRequest` → execution, `WorkspaceFile*Request` → execution
- User context injection (credentials, tokens)
- Fast path intent mapping
- Context compression

### identity (:8001)

Identity and configuration. Handles:
- User resolution (`/api/resolve`)
- Git credentials (github_token, gitlab_token, git_token)
- Global settings (`workspace_runtime_root`)
- Device assignments
- Raven missions

---

## Current Functionality

### Quick Actions (Raven)

Raven Quick Actions are predefined prompts that trigger autonomous agent missions. The "Sync Workspace Now" action:

1. User clicks "Sync Workspace Now" in the UI
2. Gateway receives the request, creates a Raven mission
3. Agent loop processes the mission with system prompt instructing it to:
   - Check git status via `GitOperationRequest(action="status")`
   - Pull latest via `GitOperationRequest(action="pull")`
   - Report results
4. Agent dispatches tool calls to execution service
5. Execution service git handler resolves workspace path and runs git commands
6. Results flow back through agent loop to user

### Workspace Management UI

Admin can:
- List all workspaces
- Create/edit/delete workspaces
- Set default workspace (star icon)
- Sync all workspaces (pull all)
- Pull individual workspace
- Configure webhooks
- View quarantine status
- Revert quarantined workspaces

Users can:
- View their own workspaces
- View shared workspaces (`owner_user: "default"`)
- Cannot see system workspaces

---

## Where We're Going

### Immediate Goals

1. **Proper directory structure**: `/workspaces/system/<id>` and `/workspaces/users/<user>/<id>`
2. **Default workspace**: `is_default` flag, used when no `workspace_id` is specified in git/tool requests
3. **Reserved name blocking**: Prevent creation of workspaces named "users", "workspaces", "system"
4. **Git handler workspace resolution**: All git operations resolve workspace path per-request, defaulting to `is_default=True` workspace
5. **Quick Actions reliability**: Ensure "Sync Workspace Now" and other git-related Quick Actions work end-to-end

### Future Goals

1. **Multi-user workspace sharing**: Collaborative workspaces with per-user permissions
2. **Workspace templates**: Pre-configured workspace blueprints
3. **Workspace snapshots**: Point-in-time backups and restore
4. **Execution service migration**: Move all workspace/git operations to workspace_runtime
5. **Workspace health checks**: Automated monitoring of workspace state, git sync status, disk usage
6. **Workspace search**: Full-text search across workspace contents

---

## Known Issues

1. **Execution git handler runs in wrong directory**: Currently uses `WORKSPACE_ROOT` (`/workspaces`) instead of the resolved workspace path (`/workspaces/system/sharedllm`). This causes `git status` to fail with "No such file or directory".

2. **Workspace path derivation doesn't account for scope**: `_derive_workspace_container_path()` returns just the repo name, but doesn't prepend `system/` or `users/{user}/`.

3. **Bootstrap doesn't create scope directories**: When bootstrapping a system workspace, it doesn't ensure `/workspaces/system/` exists.

4. **No reserved name validation**: Workspace IDs can collide with scope directory names.

5. **Legacy workspace records**: Existing workspaces have `container_mount_path=None` and `local_path="."`, requiring auto-fix on bootstrap.

# Raven Capability Gap Analysis & Tool Augmentation Plan

**Status:** Draft — For Implementation  
**Owner:** Kilo (AI Architect)  
**Mission:** Equip Raven with all tools needed to autonomously fix, verify, and improve the SharedLLM codebase without human intervention.

---

## 1. Current Raven Toolset (As-Is)

### Core Workspace Tools

| Tool | Schema | Status | Gaps |
| ------ | -------- | -------- | ------ |
| `WorkspaceFileReadRequest` | `path`, `offset_lines`, `limit_lines`, `summary_only` | ✅ Works | None |
| `WorkspaceFileWriteRequest` | `path`, `content`, `expected_sha256`, `create_parents` | ✅ Works | No backup/restore |
| `WorkspaceFilePatchRequest` | `path`, `chunks` (old_text/new_text) | ✅ Works | No conflict detection |
| `WorkspaceLintRequest` | `path` | ✅ Works | Single-file only; no batch |
| `WorkspaceSearchRequest` (ripgrep) | `query`, `path`, `include`, `exclude` | ✅ Works | None |
| `WorkspaceShellRequest` | `command`, `cwd`, `timeout` | ⚠️ Hardened but limited | Blocklist prevents mutation; safe-read only |
| `WorkspaceBootstrapRequest` | `repo_url`, `branch`, `create_if_missing` | ✅ Works | None |

### Git Tools

| Tool | Schema | Status | Gaps |
| ------ | -------- | -------- | ------ |
| `GitOperationRequest` | `action` (status/diff/add/commit/push/pull/fetch/reset/branch/checkout/clean/show), `message`, `path`, `branch` | ✅ Works | No `--amend`, no `--no-verify`, no `stash` |
| **Missing:** `GitBlameRequest` | — | ❌ | Raven can't see who last touched a line (context for edits) |
| **Missing:** `GitStashRequest` | — | ❌ | Can't save work temporarily |

### Test & Verification

| Tool | Schema | Status | Gaps |
| ------ | -------- | -------- | ------ |
| `WorkspaceLintRequest` | per-file lint | ✅ | No bulk lint (all files in workspace) |
| **Missing:** `WorkspaceTestRequest` | `targets: list[str]`, `timeout_seconds`, `coverage` | ❌ | Raven uses shell `pytest` but parses output manually |
| **Missing:** `TestCoverageRequest` | `paths: list[str]` | ❌ | Can't check coverage gaps |
| **Missing:** `TypeCheckRequest` | `paths: list[str]` | ❌ | No mypy/pyright integration |

### Analysis & Understanding

| Tool | Schema | Status | Gaps |
| ------ | -------- | -------- | ------ |
| `CapabilityIndexRequest` | — | ✅ | Returns tool list |
| **Missing:** `CodeGraphRequest` | `path: str`, `depth: int` | ❌ | No AST/call graph for complex understanding |
| **Missing:** `SymbolSearchRequest` | `symbol: str`, `kind: str` (class/function) | ❌ | Can't find definition quickly |
| **Missing:** `FileHistoryRequest` | `path: str`, `limit: int` | ❌ | Can't see recent changes to a file |

### Health & Diagnostics

| Tool | Schema | Status | Gaps |
| ------ | -------- | -------- | ------ |
| `DockerLogsRequest` | `container_name`, `tail_lines`, `filter_level` | ✅ Works | None |
| `DockerComposeRequest` | `action`, `services` | ✅ Works | None |
| **Missing:** `SystemHealthCheckRequest` | — | ❌ | No RAM/VRAM/disk metrics |
| **Missing:** `DependencyHealthRequest` | `check_type: "imports"\|"importlib"\|"pip"` | ❌ | Can't verify pip packages installed |
| **Missing:** `PortCheckRequest` | `host`, `port` | ❌ | Can't verify service is listening |

### Collaboration & PR Workflow

| Tool | Schema | Status | Gaps |
| ------ | -------- | -------- | ------ |
| `GitOperationRequest` (push) | ✅ | | **Missing:** `CreatePullRequestRequest` — can't open PRs for human review |
| | | | **Missing:** `PRReviewRequest` — can't add review comments |
| | | | **Missing:** `CheckRunsRequest` — can't trigger/check CI status |

### Configuration & Secrets

| Tool | Schema | Status | Gaps |
| ------ | -------- | -------- | ------ |
| `StorageFileRead/Write` | Nextcloud access | ✅ | **Missing:** `ConfigValidateRequest` — can't check .env completeness |
| | | | **Missing:** `SecretScanRequest` — can't scan for leaked keys |

---

## 2. Priority 1: Must-Have Tools for Autonomous Repair

These are blockers for Raven to truly self-heal without human intervention.

### 2.1 `WorkspaceTestRequest` — Structured Test Execution

**Why Raven needs it:** Current approach: `WorkspaceShellRequest` with `pytest …`. Raven must parse human-readable output to determine pass/fail. Fragile, language-dependent, no structured data.

**Schema:**

```python
class WorkspaceTestRequest(WorkspaceRef):
    targets: list[str] = Field(default_factory=list, description="Test file paths or node IDs (e.g., ['tests/test_gateway.py', '-k test_health'])")
    timeout_seconds: int = Field(default=90, ge=1, le=300)
    coverage: bool = Field(default=False, description="Generate coverage report")
    junit_xml: bool = Field(default=False, description="Output JUnit XML for CI integration")
    verbose: bool = Field(default=False)
```

**Response:**

```python
{
  "status": "SUCCESS",
  "message": "All tests passed (12/12)",
  "detail": {
    "total": 12,
    "passed": 12,
    "failed": 0,
    "skipped": 0,
    "xfailed": 0,
    "xpassed": 0,
    "duration_seconds": 4.2,
    "failures": [
      {"test": "test_foo", "message": "AssertionError: …", "traceback": "…"}
    ],
    "coverage": {"total_percent": 87.3, "lines": 1234} if coverage=True
  }
}
```

**Handler:** `handle_workspace_test()` in `workspace.py`

- Runs `pytest -q --tb=short --maxfail=1` with JSON output (`--json-report` if plugin installed)
- Parses `pytest` exit code + JSON report
- Extracts failures, durations, coverage if requested

---

### 2.2 Enhanced `GitOperationRequest` — Staging & Amend

**Why needed:** Raven needs to see what it's about to commit and fix commit messages.

**New actions:**

- `diff_cached` — show staged diff
- `amend` — amend last commit (used when tests fail after push)
- `blame` — show line-by-line authorship (helps understand legacy code)
- `stash_push` / `stash_pop` — temporary shelving

**Example:** Raven workflow:

1. `GitOperationRequest(action="add", paths=["services/foo.py"])`
2. `GitOperationRequest(action="diff_cached")` → verify changes
3. `GitOperationRequest(action="commit", message="feat: …")`
4. Tests run → if fail → `GitOperationRequest(action="amend", ...)`

**Blame response:**

```json
{
  "status": "SUCCESS",
  "message": "Blame for services/gateway/agent_loop.py",
  "detail": {
    "lines": [
      {"line_no": 1, "commit": "abc123", "author": "jeremiah", "date": "2025-05-10", "text": "import os"},
      …
    ]
  }
}
```

---

### 2.3 `DependencyCheckRequest` — Verify Requirements

**Why needed:** Before Raven commits a `requirements.txt` change, it must check if dependencies install cleanly.

**Schema:**

```python
class DependencyCheckRequest(WorkspaceRef):
    check_type: Literal["imports", "pip", "all"] = "all"
    path: str = Field(default="requirements.txt", description="Path to requirements file")
    python_version: Optional[str] = None
```

**Response:**

```json
{
  "status": "SUCCESS",
  "message": "All 79 packages importable",
  "detail": {
    "missing": [],
    "conflicts": [],
    "installable": True
  }
}
```

**Implementation:** Spin up temporary venv, `pip install -r requirements.txt`, then `python -c "import pkg"` for each.

---

### 2.4 `StaticAnalysisRequest` — Type & Security Checks

**Why needed:** Lint is syntax-only; Raven needs deeper static analysis.

**Schema:**

```python
class StaticAnalysisRequest(WorkspaceRef):
    tools: list[Literal["mypy", "bandit", "pylint", "vulture"]] = Field(default_factory=list)
    paths: list[str] = Field(default_factory=list, description="Paths to analyze, defaults to entire workspace")
    ignore: list[str] = Field(default_factory=list, description="Error codes to ignore")
```

**Response:**

```json
{
  "status": "SUCCESS",
  "message": "mypy: 0 errors, bandit: 0 high-severity",
  "detail": {
    "mypy": {"errors": [], "notes": []},
    "bandit": {"high": 0, "medium": 0, "low": 0, "issues": []}
  }
}
```

**Note:** Tools must be installed in workspace_runtime container. Add to Dockerfile.

---

### 2.5 `CreatePullRequestRequest` — PR Creation

**Why needed:** Raven should never push directly to protected branches. It must create PRs for human review.

**Schema:**

```python
class CreatePullRequestRequest(WorkspaceRef):
    title: str
    body: str
    head_branch: str
    base_branch: str = "main"
    draft: bool = False
    reviewers: list[str] = Field(default_factory=list)
    assignees: list[str] = Field(default_factory=list)
```

**Response:**

```json
{
  "status": "SUCCESS",
  "message": "Pull request #123 created",
  "detail": {
    "pr_number": 123,
    "html_url": "http://github.com/…/pull/123",
    "reviewers_required": 1
  }
}
```

**Implementation:** Call GitHub/GitLab API using `user_context.github_token` / `gitlab_token`. Use `requests` or `httpx`.

---

## 3. Priority 2: Safety & Quality Tools

### 3.1 `SecretScanRequest` — Detect Leaked Credentials

**Purpose:** Before committing, Raven scans changed files for secret-like patterns to prevent accidental leakage.

**Schema:**

```python
class SecretScanRequest(WorkspaceRef):
    paths: list[str] = Field(default_factory=list, description="Files to scan; defaults to all staged files")
    scan_mode: Literal["staged", "workspace", "commit"] = "staged"
```

**Response:**

```json
{
  "status": "SUCCESS",
  "message": "No secrets detected in 15 files",
  "detail": {"findings": []}  // or [{"path": "...", "line": 42, "type": "github_pat"}]
}
```

**Uses:** regex patterns from ADR-004 sanitizer.

---

### 3.2 `WorkspaceTestRequest` (extended) — With Dry-Run

Add `dry_run: bool` to let Raven see what tests *would* run without executing.

---

### 3.3 `SystemHealthCheckRequest` — Resource Metrics

**Purpose:** Before starting a large job, Raven checks if system can handle it.

**Schema:**

```python
class SystemHealthCheckRequest(BaseRequest):
    user_context: UserContext
    check_vram: bool = True
    check_disk: bool = True
    check_ram: bool = True
```

**Response:**

```json
{
  "status": "SUCCESS",
  "message": "System healthy: VRAM 3.2/8 GB, Disk 127/500 GB, RAM 12/32 GB",
  "detail": {
    "vram_used_gb": 3.2,
    "vram_total_gb": 8.0,
    "disk_used_gb": 127,
    "disk_total_gb": 500,
    "ram_used_gb": 12,
    "ram_total_gb": 32
  }
}
```

**Impl:** `nvidia-smi` (if available), `df`, `free`.

---

## 4. Priority 3: Understanding & Context Tools

### 4.1 `GitBlameRequest` — Line-Author Attribution

```python
class GitBlameRequest(WorkspaceRef):
    path: str
    start_line: Optional[int] = None
    end_line: Optional[int] = None
```

---

### 4.2 `CodeGraphRequest` — AST & Dependencies

```python
class CodeGraphRequest(WorkspaceRef):
    path: str
    include_imports: bool = True
    include_calls: bool = True
    include_classes: bool = True
```

Returns:

```json
{
  "imports": ["os", "sys", "fastapi"],
  "functions": [{"name": "main", "line": 10, "args": ["query"]}],
  "classes": [{"name": "AgentLoop", "line": 200, "bases": []}],
  "calls": [{"from": "main", "to": "AgentLoop", "line": 225}]
}
```

---

## 5. Tool Registration & Permissions

All new tools must be:

1. **Added to schemas.py** with proper Pydantic validation
2. **Implemented in handlers/** (workspace.py for file/test tools, git.py for git tools, new `raven.py` for diagnostics)
3. **Added to ALLOWED_TOOLS** in `gateway/agent_loop.py`
4. **Documented in prompts.py** with clear usage examples
5. **Hardened with timeouts** (30s for file ops, 60s for tests, 10s for git)
6. **Tested** with unit + integration tests

---

## 6. Implementation Phasing

### Phase 1 (Next Commit — Raven Can Run Tests)

- Add `WorkspaceTestRequest` schema + handler
- Add to ALLOWED_TOOLS
- Update RAVEN_AUTONOMOUS_PROTOCOL prompt with test guidance
- Tests: unit + integration

### Phase 2 (Safer Commits)

- Enhanced GitOperation (diff_cached, amend)
- `SecretScanRequest`
- `DependencyCheckRequest`

### Phase 3 (Deep Understanding)

- `GitBlameRequest`
- `StaticAnalysisRequest` (mypy/bandit)

### Phase 4 (PR Workflow)

- `CreatePullRequestRequest`
- `SystemHealthCheckRequest`

---

## 7. Prompt Updates Required

RAVEN_AUTONOMOUS_PROTOCOL needs new sections:

```markdown
## Test-Driven Verification
1. After applying a fix, run `WorkspaceTestRequest` on the affected test files.
2. If tests fail, analyze output and iterate.
3. Only commit if ALL relevant tests pass.

## Pre-Commit Checklist
BEFORE any `GitOperationRequest` with action="commit":
1. `WorkspaceLintRequest` on all changed files → must pass
2. `WorkspaceTestRequest` on related tests → must pass
3. `SecretScanRequest` on staged changes → must be clean
4. `GitOperationRequest` with action="diff_cached" → review changes
5. THEN commit with descriptive message

## Safe Git Practices
- NEVER use `git push` directly on protected branches
- If on protected branch, create review branch first
- Use `GitOperationRequest(action="diff_cached")` before committing
- If tests fail post-commit, use `action="amend"` to fix
```

---

## 8. Success Criteria

Raven is considered **fully self-healing** when it can:

1. Detect a broken test (via health check or error report)
2. Locate the bug (search, blame, read)
3. Propose a fix (patch)
4. Verify fix locally (lint + test)
5. Commit with good message
6. Create PR for human review (if on protected branch)
7. Revert if PR fails CI

All without human intervention.

---

**Next Actions:**

1. Implement `WorkspaceTestRequest` schema + handler
2. Add to ALLOWED_TOOLS
3. Write unit tests
4. Update prompts
5. Deploy
6. Have Raven run its own tests (meta!)

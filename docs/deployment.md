# Deployment Guide

## Architecture Overview

SharedLLM runs on a **two-machine architecture**:

| Role | Machine | Purpose |
|------|---------|---------|
| **LLM Host** | Local machine (this machine) | Runs Ollama (`alpaca-proxy`), `llama-server`, `alpaca-indexer` |
| **SharedLLM Server** | `ai.local` (mDNS — falls back to static IP when mDNS is unavailable) | Runs all SharedLLM microservices (gateway, execution, identity, RAG, etc.) |

The LLM Host provides inference via `ollama-server` (port 11434) and `llama-server` (port 8080). The SharedLLM server connects to these via Docker `extra_hosts` mapping.

## Source vs Workspace

**Critical rule**: The workspace root (configured via `WORKSPACE_HOST_PATH` in your `.env`) is for **runtime artifacts only** — git operations, file edits, and agent execution. It is **never** the source for building or deploying services.

All builds and deployments come from the **source repository** — the directory where you cloned the project on the server (i.e., your local checkout, not the workspace runtime path).

```
Source repo (build/deploy):  <your cloned repo directory on the server>
Workspace (runtime only):    <WORKSPACE_HOST_PATH from .env>
```

## Deployment Flow

### 1. Push to Git
```bash
git add -A && git commit -m "your message" && git push origin microservices
```

### 2. Pull and Deploy on Server
```bash
ssh <server-user>@<server-host>       # values from your environment config
cd <source-repo-directory>            # ← SOURCE REPO, NOT WORKSPACE
git pull origin microservices
bash scripts/deploy.sh               # auto-detects changed services
```

### 3. Auto-Deploy (Post-Merge Hook)
The repo has a post-merge hook that detects changes and auto-rebuilds affected services:
```bash
cd <source-repo-directory> && git pull origin microservices
# Auto-deploy runs automatically after merge
```

## What NOT to Do

- **Never** run `docker compose up --build` from the workspace root (`WORKSPACE_HOST_PATH`)
- **Never** copy `.env` files into the workspace directory
- **Never** treat the workspace as a build context
- **Never** start Ollama on the server — it runs on the LLM Host only

The workspace is a git clone managed by Raven and the workspace runtime service. Its contents are ephemeral and may differ from the deployed source.

## Service Change Detection

The auto-deploy script detects which services changed:
- Changes to `services/gateway/*` → rebuild gateway
- Changes to `services/execution/*` → rebuild execution
- Changes to `services/identity/*` → rebuild identity
- Changes to `services/ui/*` → rebuild ui
- Changes to `docker-compose.yml` → rebuild all

## Linting

Python files are linted with **ruff** (replaces flake8/black/isort):
```bash
python -m ruff check services/gateway/
python -m ruff check --fix services/gateway/  # auto-fix
```

The gateway has a **post-write lint hook** that automatically lints Python files after `WorkspaceFileWriteRequest` or `WorkspaceFilePatchRequest`. Lint failures are fed back to the LLM for correction.

## Model Auto-Upgrade

When a Raven mission fails due to schema/tool format errors (422, "no valid tool call", etc.), the worker automatically retries with the **largest available model** from Ollama. The upgrade model is discovered dynamically via `GET /api/tags` — no hardcoded model names.

```python
# _get_upgrade_model() queries Ollama and picks the largest model by size
upgrade_model = await self._get_upgrade_model(current_model)
```

## Verifying Deployment

```bash
# Check running services
docker compose ps

# Check gateway logs
docker compose logs --tail=20 gateway

# Verify API health
curl http://localhost:11435/health/ready

# Verify Ollama connectivity from server
docker exec sharedllm_gateway curl -s http://ollama-server:11434/api/tags
```

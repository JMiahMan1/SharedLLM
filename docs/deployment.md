# Deployment Guide

## Source vs Workspace

**Critical rule**: The workspace (`/workspaces/system/sharedllm`) is for **runtime artifacts only** — git operations, file edits, and agent execution. It is **never** the source for building or deploying services.

All builds and deployments come from the **source repository** at `/home/jeremiah/SharedLLM` on the server.

```
Source repo (build/deploy):  /home/jeremiah/SharedLLM
Workspace (runtime only):    /home/jeremiah/workspaces/system/sharedllm
```

## Deployment Flow

### 1. Push to Git
```bash
git add -A && git commit -m "your message" && git push origin microservices
```

### 2. Pull and Deploy on Server
```bash
ssh jeremiah@192.168.2.205
cd /home/jeremiah/SharedLLM          # ← SOURCE REPO, NOT WORKSPACE
git pull origin microservices
docker compose up -d --build gateway  # builds from source, not workspace
```

### 3. Auto-Deploy (Post-Merge Hook)
The repo has a post-merge hook that detects changes and auto-rebuilds affected services:
```bash
cd /home/jeremiah/SharedLLM && git pull origin microservices
# Auto-deploy runs automatically after merge
```

## What NOT to Do

- **Never** run `docker compose up --build` from `/home/jeremiah/workspaces/system/sharedllm`
- **Never** copy `.env` files into the workspace directory
- **Never** treat the workspace as a build context

The workspace is a git clone managed by Raven and the workspace runtime service. Its contents are ephemeral and may differ from the deployed source.

## Service Change Detection

The auto-deploy script detects which services changed:
- Changes to `services/gateway/*` → rebuild gateway
- Changes to `services/execution/*` → rebuild execution
- Changes to `services/identity/*` → rebuild identity
- Changes to `services/ui/*` → rebuild ui
- Changes to `docker-compose.yml` → rebuild all

## Verifying Deployment

```bash
# Check running services
docker compose ps

# Check gateway logs
docker compose logs --tail=20 gateway

# Verify API health
curl http://localhost:11435/health/ready
```

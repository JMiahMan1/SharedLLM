# SharedLLM - Modular SOA AI Middleware

A high-performance, modular Service-Oriented Architecture (SOA) that unifies smart home control, personal cloud services, and semantic memory into a single conversational AI interface.

## 🏗 Architecture Overview

SharedLLM has been migrated from a monolithic application to a robust microservices stack for improved stability, scalability, and observability.

### Core Services

| Service           | Port  | Description                                                                                                                                                                                  |
|-------------------|-------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Gateway**           | 11435 | Orchestrator & Intent Classifier. Routes requests to specialized services.                                                                                                                   |
| **Identity**          | 8001  | Secure credential management and device-to-user resolution.                                                                                                                                  |
| **Execution**         | 8003  | Home Assistant bridge for lights, media, climate, and security.                                                                                                                              |
| **RAG**               | 8004  | Semantic memory layer using ChromaDB for knowledge retrieval.                                                                                                                                |
| **Storage**           | 8005  | Shared persistence for documents and configuration.                                                                                                                                          |
| **Logging**           | 8006  | Centralized observability hub for all microservices.                                                                                                                                         |
| **Workspace Runtime** | 8007  | Sandboxed workspace inspection with Identity-aware workspace filtering, limited system workspaces, file reads, git status/diff, targeted pytest execution, and broader workspace operations. |
| **Automation**        | \-     | Background task processor for polling and scheduled events.                                                                                                                                  |
| **Redis**             | 6379  | High-speed cache for session state and history.                                                                                                                                              |

## 🚀 Deployment

The system is designed to run in Docker with host networking for seamless device discovery (Google Cast, DLNA).

```bash
# Pull latest changes and auto-deploy
bash scripts/deploy.sh
```

Or manually:

```bash
docker compose up -d --build
```

Runtime model roles are environment-driven. In particular, the gateway can
route obvious coding requests to a dedicated coding model when
`CODING_MODEL` is set, while leaving normal assistant traffic on
`ASSISTANT_MODEL` or `DEFAULT_MODEL`.

## 📂 Project Structure

```text
services/
  gateway/      # Semantic Routing & Proxy
  identity/     # Auth & Credential Resolution
  execution/    # HA Integration & Handlers
  rag/          # Semantic Indexing & Search
  storage/      # Persistence Logic
  workspace_runtime/ # Sandboxed workspace inspection and agentic runtime substrate
  logging/      # Observability Hub
  automation/   # Background Tasks
  tests/        # System-wide Smoke & Unit Tests
scripts/        # Deployment & Maintenance scripts
data/           # Shared volume data
docker-compose.yml
```

## ✅ Verified Features

- **Fast Path Routing**: Semantic intent classification bypasses LLMs for >90% match rates (<100ms latency).
- **Smart Power Sync**: Automatically powers on TVs/Media players before executing playback commands.
- **Identity Injection**: Transparently injects user-specific HA credentials into execution payloads.
- **Global Observability**: Centralized logging with context-aware tracing across all services.
- **RAG Memory**: Persistent semantic history and document context for more relevant AI responses.
- **Librarian (Deep Indexing)**: Context-aware document reasoning with automated NextCloud content extraction, checkpointing, and resource-prioritized background indexing.
- **Workspace Runtime**: Mounted local workspaces can now be resolved and inspected through a dedicated service for file reads, `git status`/`git diff`, and targeted `pytest` execution.

## 🛠 Testing

We maintain a 100% green-build standard for all services.

```bash
# Run local test suite (requires venv)
bash run_local_tests.sh

# Run remote smoke test
python3 services/tests/soa_smoke_test.py http://ai.local:11435 [SECRET]
```

---

*SharedLLM: The decentralized brain for your smart home.*
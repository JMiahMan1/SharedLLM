# Jarvis (SharedLLM) - Modular SOA AI Middleware

Jarvis is a high-performance, modular Service-Oriented Architecture (SOA) that unifies smart home control, personal cloud services, and semantic memory into a single conversational AI interface.

## 🏗 Architecture Overview

Jarvis operates using a three-tier request handling hierarchy to balance speed and intelligence:

1. **Tier 1: FastPath (Semantic Matcher)**:
   - **Role**: Immediate keyword and semantic matching for common commands.
   - **Latency**: <100ms.
   - **Use Case**: Simple Home Automation commands (e.g., "Turn off the lights").
   - **Logic**: Uses a local `nomic-embed-text-v1.5` model to bypass the LLM entirely.

2. **Tier 2: Librarian (Standard LLM)**:
   - **Role**: Single-turn tool access and general conversational reasoning.
   - **Latency**: 1-3s.
   - **Use Case**: Ambiguous Home Automation queries, document retrieval, and general knowledge.
   - **Architecture**: Runs on the main Gateway context; executes tools once per request.

3. **Tier 3: Raven (Autonomous Agent)**:
   - **Role**: Multi-step, goal-oriented agent for coding and system repair.
   - **Latency**: Variable (long-running).
   - **Use Case**: Fixing bugs, deploying services, and multi-file workspace operations.
   - **Architecture**: Runs in a dedicated agent loop (30 iterations max) with tool-feedback support. Raven operates within sandboxed Docker containers via the Workspace Runtime.

### Core Services

| Service               | Port  | Description                                                                                                |
| -------               | ----  | -----------                                                                                                |
| **Gateway**           | 11435 | Orchestrator & Intent Classifier. Routes requests to specialized services.                                 |
| **Identity**          | 8001  | Secure credential management and device-to-user resolution.                                                |
| **Execution**         | 8003  | Home Assistant bridge for lights, media, climate, and security.                                            |
| **RAG**               | 8004  | Semantic memory layer using ChromaDB for knowledge retrieval.                                              |
| **Storage**           | 8005  | Shared persistence for documents and configuration.                                                        |
| **Logging**           | 8006  | Centralized observability hub for all microservices.                                                       |
| **Workspace Runtime** | 8007  | Sandboxed workspace inspection for Raven's coding and system repair tasks.                                 |
| **Geo**               | 8009  | Life360-style family location service wrapping Home Assistant (person/device_tracker/zone + history).     |
| **Automation**        | -     | Background task processor for polling and scheduled events.                                                |
| **Redis**             | 6379  | High-speed cache for session state and history.                                                            |

## 🚀 Deployment

The system is designed to run in Docker with host networking for seamless device discovery (Google Cast, DLNA).

```bash
# Pull latest changes and auto-deploy
bash scripts/deploy.sh
```

## ✅ Key Components

- **Jarvis (Core)**: The overarching middleware managing all microservices.
- **Raven (Agent)**: The designated coding and long-running task agent. It utilizes a 30-iteration autonomous loop with tool-feedback support.
- **Fast Path Routing**: Semantic intent classification bypasses LLMs for >90% match rates (<100ms latency).
- **Smart Power Sync**: Automatically powers on TVs/Media players before executing playback commands.
- **RAG Memory**: Persistent semantic history and document context for more relevant AI responses.
- **Capability Enforcement**: Granular, per-workspace security policies (read, write, git_status, git_diff, git_write, pytest) that confine autonomous agents to authorized operations.
- **Dynamic Sync Exclusions**: Provider-agnostic file filtering (e.g., .git, node_modules) managed via a tag-based UI to protect repository integrity during automated syncs.

---

*Jarvis: The decentralized brain for your smart home.*
// Fri Jul  3 12:42:37 PM MST 2026

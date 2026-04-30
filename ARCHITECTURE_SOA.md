# SharedLLM SOA Architecture Schema

This document defines the modular service-oriented architecture for the SharedLLM system.

## Service Overview

| Service | Port | Description | Core Features | Dependencies |
|---------|------|-------------|---------------|--------------|
| **Gateway** | 8002 (Host: 11435) | Main entry point & Orchestrator | Intent classification, Command decomposition, History management, Global Health | Identity, Execution, RAG, Redis, Ollama |
| **Identity** | 8001 | Secure Identity Resolution | User/Device mapping, Credential decryption (AES-256), Security tokens | Storage (SQLite), Fernet Key |
| **Execution** | 8003 | Home Assistant Bridge | Light control, Media ops, Climate, Security, Device Discovery | Home Assistant API |
| **RAG** | 8004 | Semantic Memory Layer | HA Entity indexing, Document retrieval, Semantic search | Storage (ChromaDB), Ollama |
| **Storage** | 8005 | Persistence Layer | SQLite for Identity, ChromaDB for RAG, Redis for session state | Local Filesystem, Redis |
| **Logging** | 8006 (Host: 11436) | Observability Hub | Centralized SQLite log storage, App-ready API | Microservices (via HTTP) |
| **Automation** | - | Background Tasks | Cron-like tasks, Device polling, Sync triggers | Gateway, Execution |

## Key Workflows

### 1. Intent Orchestration
1. Client sends query to **Gateway** `/api/chat`.
2. **Gateway** calls **Identity** to resolve credentials.
3. **Gateway** contextualizes query using **Redis** history.
4. **Gateway** decomposes compound commands.
5. **Gateway** executes high-confidence commands via **Execution**.
6. **Gateway** proxies ambiguous or complex queries to **Ollama** via **RAG** context.

### 2. Device Discovery & Sync
1. **Gateway** calls **Execution** `/discovery/entities`.
2. **Gateway** triggers background sync to **RAG** `/rag/sync/ha`.
3. **RAG** updates semantic index with latest friendly names and entity IDs.

## Data Schema (Identity)

**Table: users**
- `user_id` (PK)
- `voice_id` (Unique mapping to TTS/STT)
- `ha_url` (Encrypted)
- `ha_token` (Encrypted)
- `nextcloud_url` (Encrypted)
- `nextcloud_user` (Encrypted)
- `nextcloud_pass` (Encrypted)

## Health & Readiness
The **Gateway** provides a unified health endpoint:
- `GET /health/ready`: Checks connectivity to ALL downstream microservices.

---
*Last Updated: 2026-04-30*

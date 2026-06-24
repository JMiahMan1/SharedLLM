# Raven 2.0: Fable 5-Worthy Autonomous Harness

## Overview

Phase 1 of the Raven 2.0 Architecture Overhaul: Documentation & Architecture Blueprint Complete

This document summarizes the updated architecture for Raven 2.0, transforming the traditional OpenCode-inspired AgentLoop into a Fable 5-worthy long-running, multi-modal agentic harness. The system now operates entirely within the local, privacy-first microservice ecosystem.

## Key Features

### 1. Core Substrate Redesign

**Temporal DAG Execution**
- Long-running tasks are Directed Acyclic Graphs (DAGs) of state transitions
- State persistence in Redis enables true resumability across container restarts
- Enables tasks that span days (codebase migrations, video rendering)

**Hierarchical Swarm Routing**
- Orchestrator Node replaces monolithic AgentLoop
- Delegated to specialized sub-agents: Coder, MediaCreator, SysAdmin, DataAnalyst
- Central RAG memory bank with smaller, specialized local models for VRAM efficiency

**VRAM-Aware Context Paging**
- "Context Splitting" when action log exceeds context window
- Background thread summarizes memory into ChromaDB Workspace Context Vector
- Active context window remains hyper-lean

### 2. Multi-Modal Creation Pipelines

**Workspace Runtime Extension**
- Maps `/media/scratch` for binary asset manipulation
- Integrated tools for:
  - TTS/STT: Local Kokoro/Whisper microservices
  - Graphics: Local ComfyUI/Stable Diffusion API endpoint
  - Data Management: Autonomous Nextcloud sync with deduplication

### 3. Tool Registration Registry

**Dynamic Discovery**
- Abstracted `ALLOWED_TOOLS` into dynamic registry pattern
- Services broadcast capabilities via Redis PubSub on startup
- Raven discovers new tools (image generators) without gateway code updates

### 4. Tiered Queue System

**Librarian Fast-Path**
- Bypasses Raven's heavy queue for UI interactions
- Ensures sub-200ms response for home automation commands
- Raven compiles code in background without blocking core functions

### 5. VRAM Spillover Guardrails

**Proactive Resource Management**
- Monitors `/api/ps` for VRAM constraints
- Automatically downgrades context window size or pauses
- Continues when Librarian tasks complete

### 6. Enhanced Workspace Runtime

**Media Workspace Mounts**
- Updates `docker-compose.yml` and `services/workspace_runtime/main.py`
- Supports images, audio manipulation

**Creation Tools**
- `GraphicGenerationRequest`: SD endpoint integration, Nextcloud save
- `AudioGenerationRequest`: TTS generation, media orchestration
- Enhanced `WorkspaceShellRequest`: Async mode with job ID and webhook

### 7. Guardrail Directives

**No Assumptions & Multi-Approach Validation**
- Prompt design enforces strict validation
- DockerLogsRequest used for service failures before retry
- Multiple parameter names, HTTP methods, retry strategies

## Files Updated

### 1. `docs/jarvis_os_2_master_guide.md`
- Complete architectural overhaul documentation
- Added detailed specifications for Raven 2.0
- Updated integration points and workflow definitions

### 2. `docs/RAVEN_AUDIT_BLUEPRINT.md`
- New detailed architectural blueprint
- Risk assessment and hardening roadmap
- Prioritized implementation slices (Sprint 1-4)

## Implementation Phases

### Phase 2: Core Substrate Restructuring
**Steps for the Agent:**
1. Refactor `agent_loop.py` → StateGraph object
2. Define nodes: Plan, Act, Observe, Reflect, Summarize
3. Implement Redis checkpointing at node transitions
4. Create Tiered Queue with Librarian fast-path
5. Add VRAM spillover guardrails

### Phase 3: Expanding Workspace Runtime
**Steps for the Agent:**
1. Update docker-compose.yml for media workspace
2. Enhance `services/workspace_runtime/main.py`
3. Add creation tools to `services/execution/`
4. Implement async shell commands

### Phase 4: Prompt Engineering & Behavior Constraints
**Steps for the Agent:**
1. Rewrite `services/gateway/prompts.py`
2. Design Orchestrator, Coder/Fixer, Guardrail prompts
3. Integrate with Raven 2.0 specifications

## Next Steps

Awaiting human review before proceeding to Phase 2 implementation.

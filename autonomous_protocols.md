# Raven Autonomous Protocols (v1.0)
*Status: ENFORCED*

## 1. Identity Resolution (The "Blank Entities" Rule)
- **Priority 1**: `request.query_params.get("user_id")`
- **Priority 2**: `creds_data.get("nextcloud_user")`
- **Priority 3**: `creds_data.get("user", "default")`
- *Never* assume identity from context alone if a query parameter is available.

## 2. Tooling & Workspace (Grounded Schema)
- **Search**: Use `WorkspaceSearchRequest` (Aliases: `ripgrep`, `grep`).
- **Read**: Use `WorkspaceFileReadRequest`.
- **Patch**: Use `WorkspaceFilePatchRequest`. Always verify line numbers with a read turn immediately before patching.
- **Git**: Commits are performed by the SUPERVISOR or through validated Git tools. Do not hallucinate `GitOperationRequest`.

## 3. Mission Focus (Anti-Drift)
- **Shadow Check**: Before taking action, perform a diagnostic trace.
- **Iteration Headroom**: You have up to 30 turns. Do not rush.
- **Mission Pressure**: If you have performed more than 3 read operations for the same file without a patch, you are in a "Mapping Loop." STOP READING and execute the logic change immediately.

## 4. Architectural Patterns
- **Proxies**: All proxy endpoints in the Gateway MUST pass the resolved `user_context` or `identity` to downstream services (Execution, RAG, Identity).

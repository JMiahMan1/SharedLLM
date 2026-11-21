# scripts/index_capabilities.py
import json
import logging
import os

# Import schemas from the services
import sys

import requests
from pydantic import BaseModel

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from services.execution import schemas as exec_schemas
from services.gateway import schemas as gateway_schemas

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("indexer")

# The RAG URL resolves through services.config: it reads the network-aware
# {NETWORK_MODE}_RAG_SVC_URL from .env (seeded there, editable in the UI
# settings page). It is never defined in docker-compose.
from services.config import INTERNAL_SECRET, RAG_SVC_URL  # noqa: E402


def get_json_schema(model: type[BaseModel]):
    """Returns a simplified string representation of the Pydantic model for RAG indexing."""
    return json.dumps(model.model_json_schema(), indent=2)

def index_capabilities():
    capabilities = []

    schema_map = {
        "LightControlRequest": "Controls smart lights, brightness, and colors.",
        "MediaPlayRequest": "Controls media players, plays music, handles TV casting.",
        "MediaTransportRequest": "Handles pause, resume, stop, and volume for media players.",
        "MediaStatusRequest": "Queries what is currently playing across Home Assistant media players (filterable by area or entity_id).",
        "VideoPlayRequest": "Plays a YouTube URL or video search query on a Cast or AndroidTV media player.",
        "ClimateRequest": "Sets the temperature on climate control devices (HVAC).",
        "SecurityRequest": "Controls locks and covers (open, close, lock, unlock).",
        "HAServiceRequest": "Generic Home Assistant service call for any domain.",
        "EntitySearchRequest": "Searches Home Assistant entities by name, domain, area, or state when entity_id is unknown.",
        "HAConfigRequest": "Inspects Home Assistant integrations, entity domains, and configuration snapshots.",
        "LogbookRequest": "Queries recent Home Assistant entity state history / logbook events (1-7 days).",
        "LocationRequest": "Queries family location, Home Assistant zones, travel speed, dwell time, frequented places, ETA, closest person, or vehicle/fuel cost.",
        "AnnouncementRequest": "Broadcasts a text-to-speech message to a speaker.",
        "TVCastRequest": "Powers on a TV and casts media content.",
        "CalendarRequest": "Manages calendar events (list calendars, read events, add, delete, update).",
        "NoteRequest": "Manages personal notes and checklists.",
        "ChoreRequest": "Adds, changes, removes, lists and ticks off family chores on the Skylight chore chart: a chore for someone on one day or repeating (daily, weekdays, weekly, chosen days), at a time, with stars. Only parents add, change or remove chores, never from a voice alone; anyone lists and ticks off their own. Parents approve chores a child ticked off (they earn screen time) or send them back to redo, and hear what is waiting.",
        "MealPlanRequest": "Plans a dinner into the family's week plan note (Recipes/Meal plan.md) from a spoken request: the day is read from the sentence or from `day`, the recipe is matched against the Recipes shelf, and only that day's line is rewritten. A plan it cannot read is reported, never overwritten.",
        "BibleRequest": "Holy Bible reader, read-only: read passages, search verses, fetch study notes by edition, verse of the day, catalogue of installed translations (actions: read, search, study_notes, votd, catalogue). No state or mark writes.",
        "CalibreRequest": "Read-only personal Calibre book library (plus admin-only tagging): list the shelf, search by title/author/tag words, fetch one book's metadata or its text, list tags, list books by tag, audit copies and gaps, and tag books (actions: list, search, get_book, fetch_text, tags, by_tag, audit, tag). No delete action exists.",
        "AudiobookshelfRequest": "Family Audiobookshelf (ABS) library for audiobooks and podcasts: list libraries, browse items, search, inspect book details, check listening progress or last played, and play or resume (actions: libraries, list, search, get_book, progress, last_played, play, play_podcast_episode, resume, update_progress).",
        "ContextSearchRequest": "Semantic RAG search over indexed collections. collection_name is REQUIRED: calibre_files (books), nextcloud_files (cloud docs/notes/recipes), home_devices (the home's devices), system_learnings (past lessons), system_capabilities (tools/binaries), user_facts (remembered user facts), conversation_memory (intercom transcripts), mission_history (past Raven runs), network_topology (Docker/LAN topology), telemetry_alerts (device alerts). Optional tags filters topic folders or Calibre tags.",
        "TimerRequest": "Sets, lists, or deletes timers and alarms.",
        "TodoListRequest": "Adds to, reads, ticks off and removes items on the home's to-do lists (the hub's shopping list and reminder lists), found by the name the person used; every household member may use them.",
        "TalkRequest": "Manages messaging, voice messages, and user presence via Nextcloud Talk.",
        "WebSearchRequest": "Performs a web search via the private search engine.",
        "WebReadRequest": "Fetches and converts a webpage to markdown.",
        "WebScraperRequest": "Scrapes product prices or dynamic web pages using Playwright or Camoufox with Vision OCR.",
        "BrowserAutomationRequest": "Opens a real web page in a headless browser, runs click/type/wait actions, and returns visible text plus optional screenshot.",
        "WorkspaceFileReadRequest": "CRITICAL: Reads a file from the local Git workspace. You HAVE access. Use this for CODE, SCRIPTS, and CONFIG. Do NOT use Storage tools for code.",
        "WorkspaceFileWriteRequest": "Writes or overwrites a file in the local Git workspace. Use this for modifying CODE. Always run tests after writing.",
        "WorkspaceFilePatchRequest": "Surgically patches a file in the local Git workspace using old_text/new_text chunks. PREFERRED for small code edits.",
        "WorkspaceShellRequest": "Executes a shell command in the workspace root. Use for advanced CLI operations.",
        "WorkspaceSearchRequest": "Performs a codebase-wide search (grep/ripgrep) to find patterns or function definitions.",
        "WorkspaceLintRequest": "Runs linters (black, flake8, eslint, etc.) on a specific file to ensure code quality.",
        "WorkspaceCreateRequest": "Creates a new isolated workspace environment (id, display_name, scope).",
        "WorkspaceSettingsUpdateRequest": "Updates an existing workspace's settings (repo_url, git_remote, default_branch, display_name, sync_mode, nextcloud_path).",
        "WorkspaceBootstrapRequest": "Autonomously initializes or clones a repository into a workspace path if it is missing.",
        "CodeSearchRequest": "Searches code across repositories or workspace files.",
        "GhRequest": "Runs a GitHub CLI (gh) command (repos, issues, PRs, workflows) inside the workspace.",
        "DiscoverySyncRequest": "Synchronizes Home Assistant entities into the discovery RAG for device control.",
        "StorageFileReadRequest": "Reads a file from Nextcloud storage (Documents/Notes). Do NOT use for code analysis.",
        "StorageFileWriteRequest": "Writes a file to Nextcloud storage.",
        "DockerLogsRequest": "CRITICAL: Fetches recent log output. You ARE authorized to display these logs to the user.",
        "DockerComposeRequest": "Inspects or manages Docker Compose services and containers.",
        "ExecutionLogRequest": "Fetches recent SharedLLM execution service/handler logs filtered by handler or keyword.",
        "GitOperationRequest": "Performs Git lifecycle operations on the workspace repo (status, add, commit, push, pull, fetch, log, branch, checkout, init, remote_add, repo_create).",
        "DeploymentRequest": "Restarts or inspects SharedLLM Docker containers.",
        "ControlPlaneRequest": "Restarts or checks status of a SharedLLM service via the control plane.",
        "VolumeInventoryRequest": "Inspects Docker volume usage (Admin only).",
        "NetworkDeviceScanRequest": "Scans the local network (LAN) for devices, IPs, and MAC addresses.",
        "RedisInspectRequest": "Read-only Redis inspection (ping, get, keys, ttl) for diagnostics and self-tests.",
        "LLMInfoRequest": "Inspects loaded LLM models, VRAM usage, and Ollama backend state.",
        "AiCapabilitiesRequest": "Checks which AI media capabilities (image editing, face swap, voice profiles, video) can actually run right now.",
        "IdentityRequest": "Resolves user identity and integration credential context.",
        "IdentityManageRequest": "Admin management of Identity users, passwords, and API keys.",
        "CapabilityIndexRequest": "Triggers this re-indexing script to refresh tool definitions.",
        "SystemLearningRequest": "Persists verified bug fixes and architectural insights to the RAG ledger (`ingest`) or records self-test results (`validate`).",
        "TTSRequest": "Converts text to speech using local Kokoro engine. Supports storybook mode.",
        "STTRequest": "Transcribes speech from an audio or video file in the workspace into text.",
        "OcrRequest": "Extracts visible text from a workspace image or scanned document/receipt via vision OCR.",
        "ImageEditRequest": "Edits a workspace image via img2img (single-image edit with image_path, or two-image face swap with image_path + face_image_path).",
        "PodcastRenderRequest": "Renders a two-host speaker-tagged podcast script into a workspace WAV file with TTS and a ducked music bed.",
        "SpeakerIdentifyRequest": "Identifies which enrolled household speaker is speaking in a workspace audio clip.",
        "ListVoicesRequest": "Lists enrolled speaker voice profiles and curated podcast host pairs.",
        "DocumentBroadcastRequest": "Reads a Nextcloud document and broadcasts it as TTS to a Home Assistant media player.",
        "NightModeRequest": "Runs the composite night-mode scene across lights, climate, and media.",
        "StorageTextToAudioRequest": "Converts a text file in Nextcloud storage to an audio file using Kokoro narration.",
        "AudiobookRegenerateRequest": "Regenerates an audiobook chapter or full book from text files via TTS.",
        "RavenMissionRequest": "Dispatch a background Raven mission: an autonomous agent that plans, writes code, creates its own workspace, builds, tests, and can push to GitHub. Use this for complex multi-step tasks such as building an app, running long research, or orchestrating multi-tool workflows. The mission runs asynchronously in the Raven queue and returns a mission id."
    }

    # Inline JSON schemas for string-type / raw-JSON tools that have no Pydantic
    # model in services.execution.schemas or services.gateway.schemas.
    _inline_schemas = {
        "RavenMissionRequest": json.dumps({
            "title": "RavenMissionRequest",
            "type": "object",
            "properties": {
                "mission": {"title": "Mission", "type": "string", "description": "The full task/mission description for Raven to execute."},
                "workspace_id": {"title": "Workspace Id", "type": "string", "description": "Optional existing workspace id to run the mission in."},
            },
            "required": ["mission"],
        }, indent=2),
        "WorkspaceCreateRequest": json.dumps({
            "title": "WorkspaceCreateRequest",
            "type": "object",
            "properties": {
                "id": {"title": "Id", "type": "string", "description": "Unique workspace slug."},
                "display_name": {"title": "Display Name", "type": "string"},
                "scope": {"title": "Scope", "type": "string", "description": "user or system"},
            },
        }, indent=2),
        "WorkspaceSettingsUpdateRequest": json.dumps({
            "title": "WorkspaceSettingsUpdateRequest",
            "type": "object",
            "properties": {
                "workspace_id": {"title": "Workspace Id", "type": "string"},
                "repo_url": {"title": "Repo Url", "type": "string"},
                "git_remote": {"title": "Git Remote", "type": "string"},
                "default_branch": {"title": "Default Branch", "type": "string"},
                "display_name": {"title": "Display Name", "type": "string"},
                "sync_mode": {"title": "Sync Mode", "type": "string"},
                "nextcloud_path": {"title": "Nextcloud Path", "type": "string"},
            },
            "required": ["workspace_id"],
        }, indent=2),
        "STTRequest": json.dumps({
            "title": "STTRequest",
            "type": "object",
            "properties": {
                "file_path": {"title": "File Path", "type": "string", "description": "Workspace-relative path to the audio or video file."},
                "language": {"title": "Language", "type": "string"},
                "model": {"title": "Model", "type": "string"},
            },
            "required": ["file_path"],
        }, indent=2),
        "AiCapabilitiesRequest": json.dumps({
            "title": "AiCapabilitiesRequest",
            "type": "object",
            "properties": {},
        }, indent=2),
    }

    # Resolve schemas from execution first, then gateway, then inline fallbacks.
    for class_name, description in schema_map.items():
        if class_name in _inline_schemas:
            capabilities.append({
                "name": class_name,
                "description": description,
                "schema": _inline_schemas[class_name],
                "type": "execution_schema",
            })
            log.info(f"Prepared inline schema: {class_name}")
            continue

        model = getattr(exec_schemas, class_name, None) or getattr(gateway_schemas, class_name, None)
        if not model:
            log.warning(f"No request model named {class_name}; not indexed.")
            continue

        capabilities.append({
            "name": class_name,
            "description": description,
            "schema": get_json_schema(model),
            "type": "execution_schema",
        })
        log.info(f"Prepared schema: {class_name}")

    # Process Storage/Gateway Schemas (exclude StorageListRequest: /execute/storage_list 404s)
    storage_map = {
        "StorageIndexRequest": "Triggers a recursive scan and indexes all files/folders in NextCloud for use in RAG context.",
        "StorageStatusRequest": "Retrieves the current indexing status and file counts from the RAG and storage backends."
    }

    for class_name, description in storage_map.items():
        model = getattr(gateway_schemas, class_name, None)
        if model:
            capabilities.append({
                "name": class_name,
                "description": description,
                "schema": get_json_schema(model),
                "type": "execution_schema"
            })
            log.info(f"Prepared storage schema: {class_name}")

    # The Workspace Studio's tools, from the studio's own action table: the same
    # body model, description and hint the routes and the tool registry use.
    from services.gateway.studio import actions as studio_actions
    from services.gateway.studio import tools as studio_tools

    for class_name, action_name in studio_tools.TOOL_ACTIONS.items():
        action = studio_actions.ACTIONS[action_name]
        capabilities.append({
            "name": class_name,
            "description": f"{action.description} {action.hint}",
            "schema": get_json_schema(action.body),
            "type": "execution_schema",
        })
        log.info(f"Prepared studio schema: {class_name}")

    log.info("Skipping legacy phrasebook intents.")

    if not capabilities:
        log.warning("No capabilities found to index.")
        return

    try:
        log.info(f"Attempting to sync with RAG at {RAG_SVC_URL}...")

        # ADDED: Wait for RAG to be ready with a loop
        max_retries = 10
        for i in range(max_retries):
            try:
                # Use the health endpoint to check readiness
                h_resp = requests.get(f"{RAG_SVC_URL}/health", timeout=5)
                if h_resp.status_code == 200:
                    log.info("RAG service is healthy and ready.")
                    break
            except Exception:
                pass

            log.info(f"RAG not ready yet (attempt {i+1}/{max_retries}), waiting 5s...")
            import time
            time.sleep(5)

        resp = requests.post(
            f"{RAG_SVC_URL}/rag/sync/capabilities",
            json={"capabilities": capabilities},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            # RAG embeds every capability; right after a deploy it is also
            # syncing Home Assistant, and this took 11 minutes on 2026-10-15.
            # It finishes the job even when a client gives up, so a short
            # timeout only made a successful sync look failed.
            timeout=1800
        )
        if resp.status_code == 200:
            log.info(f"Successfully indexed {len(capabilities)} capabilities into RAG.")
        else:
            raise RuntimeError(
                f"RAG rejected the capability sync: {resp.status_code} {resp.text[:400]}"
            )
    except Exception as e:
        log.error(f"Capability sync failed: {e}")
        raise

if __name__ == "__main__":
    index_capabilities()

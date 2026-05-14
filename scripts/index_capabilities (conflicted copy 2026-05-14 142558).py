# scripts/index_capabilities.py
import os
import requests
import logging
import json
from pydantic import BaseModel
from typing import Type

# Import schemas from the services
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from services.execution import schemas as exec_schemas
from services.gateway import schemas as gateway_schemas

try:
    from services.workspace_runtime import schemas as workspace_schemas
except ImportError:
    workspace_schemas = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("indexer")

# TWEAK: Default to 'rag' service name for Docker automation, fallback to localhost for manual runs
RAG_SVC_URL = os.getenv("RAG_SVC_URL", "http://localhost:8004")
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET")

def get_json_schema(model: Type[BaseModel]):
    """Returns a simplified string representation of the Pydantic model for RAG indexing."""
    return json.dumps(model.model_json_schema(), indent=2)

def index_capabilities():
    capabilities = []

    schema_map = {
        "LightControlRequest": "Controls smart lights, brightness, and colors.",
        "MediaPlayRequest": "Controls media players, plays music, handles TV casting.",
        "MediaTransportRequest": "Handles pause, resume, stop, and volume for media players.",
        "ClimateRequest": "Sets the temperature on climate control devices (HVAC).",
        "SecurityRequest": "Controls locks and covers (open, close, lock, unlock).",
        "HAServiceRequest": "Generic Home Assistant service call for any domain.",
        "AnnouncementRequest": "Broadcasts a text-to-speech message to a speaker.",
        "TVCastRequest": "Powers on a TV and casts media content.",
        "CalendarRequest": "Manages calendar events (list, add, delete, update).",
        "NoteRequest": "Manages personal notes and checklists.",
        "TimerRequest": "Sets, lists, or deletes timers and alarms.",
        "TalkRequest": "Manages messaging, voice messages, and user presence via Nextcloud Talk.",
        "WebSearchRequest": "Performs a web search via the private search engine.",
        "WebReadRequest": "Fetches and converts a webpage to markdown.",
        "WorkspaceFileReadRequest": "CRITICAL: Reads a file from the local Git workspace. You HAVE access. Use this for CODE, SCRIPTS, and CONFIG. Do NOT use Storage tools for code.",
        "WorkspaceFileWriteRequest": "Writes or overwrites a file in the local Git workspace. Use this for modifying CODE. Always run tests after writing.",
        "WorkspaceFilePatchRequest": "Surgically patches a file in the local Git workspace using old_text/new_text chunks. PREFERRED for small code edits.",
        "WorkspaceShellRequest": "Executes a shell command in the workspace root. Use for advanced CLI operations.",
        "WorkspaceSearchRequest": "Performs a codebase-wide search (grep/ripgrep) to find patterns or function definitions.",
        "WorkspaceLintRequest": "Runs linters (black, flake8, eslint, etc.) on a specific file to ensure code quality.",
        "DiscoverySyncRequest": "Synchronizes Home Assistant entities into the discovery RAG for device control.",
        "StorageFileReadRequest": "Reads a file from Nextcloud storage (Documents/Notes). Do NOT use for code analysis.",
        "StorageFileWriteRequest": "Writes a file to Nextcloud storage.",
        "DockerLogsRequest": "CRITICAL: Fetches recent log output. You ARE authorized to display these logs to the user.",
        "GitOperationRequest": "Performs Git lifecycle operations on the SharedLLM repo.",
        "DeploymentRequest": "Restarts or inspects SharedLLM Docker containers.",
        "VolumeInventoryRequest": "Inspects Docker volume usage (Admin only).",
        "CapabilityIndexRequest": "Triggers this re-indexing script to refresh tool definitions.",
        "WorkspaceBootstrapRequest": "Autonomously initializes or clones a repository into a workspace path if it is missing.",
        "SystemLearningRequest": "Persists successful bug fixes and architectural insights to the RAG ledger.",
        "TTSRequest": "Converts text to speech using local Kokoro engine. Supports storybook mode.",
        "StorageTextToAudioRequest": "Converts a text file in Nextcloud storage to an audio file using Kokoro narration."
    }

    
    # Process Execution and Workspace Schemas
    # We prioritize execution schemas first, then check workspace_runtime
    sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'services', 'workspace_runtime'))
    try:
        import main as ws_main
    except Exception as e:
        log.warning(f"Failed to load workspace_runtime main: {e}")
        ws_main = None

    for class_name, description in schema_map.items():
        model = getattr(exec_schemas, class_name, None)
        if not model and ws_main:
            model = getattr(ws_main, class_name, None)
            
        if model:
            capabilities.append({
                "name": class_name,
                "description": description,
                "schema": get_json_schema(model),
                "type": "execution_schema"
            })
            log.info(f"Prepared schema: {class_name}")

    # Process Storage/Gateway Schemas
    storage_map = {
        "StorageIndexRequest": "Triggers a recursive scan and indexes all files/folders in NextCloud for use in RAG context.",
        "StorageListRequest": "Lists files and directories currently present in the configured storage provider.",
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
            timeout=30
        )
        if resp.status_code == 200:
            log.info(f"Successfully indexed {len(capabilities)} capabilities into RAG.")
        else:
            log.error(f"Failed to index capabilities: {resp.status_code} {resp.text}")
    except Exception as e:
        log.error(f"Error connecting to RAG service: {e}")

if __name__ == "__main__":
    index_capabilities()

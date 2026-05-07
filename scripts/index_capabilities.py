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
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")

def get_json_schema(model: Type[BaseModel]):
    """Returns a simplified string representation of the Pydantic model for RAG indexing."""
    return json.dumps(model.model_json_schema(), indent=2)

def index_capabilities():
    capabilities = []

    schema_map = {
        "LightControlRequest": "Controls smart lights, brightness, and colors. Use this for all light-related commands.",
        "MediaPlayRequest": "Controls media players, plays music, handles TV casting. Use for playing content.",
        "MediaTransportRequest": "Handles pause, resume, stop, and volume for media players.",
        "HAServiceRequest": "Generic Home Assistant service call for any domain not covered by specialized tools.",
        "AnnouncementRequest": "Broadcasts a text-to-speech message to a speaker.",
        "TVCastRequest": "Powers on a TV and casts media content.",
        "CalendarRequest": "Manages calendar events (list, add, delete).",
        "NoteRequest": "Manages personal notes and checklists.",
        "TimerRequest": "Sets, lists, or deletes timers and alarms.",
        "TalkRequest": "Manages messaging, voice messages, and user presence.",
        "WebSearchRequest": "Performs a web search via the private search engine.",
        "WebReadRequest": "Fetches and converts a webpage to markdown.",
        "WorkspaceFileAction": "Orchestrates file writes and patches within a Git-backed workspace. Primary tool for code edits.",
        "WorkspaceGitAction": "Performs Git lifecycle operations like branch, commit, push, and pull.",
        "WorkspaceSyncAction": "Synchronizes workspace files with Nextcloud storage.",
        "DockerLogsRequest": "Fetches recent log output from a Docker container for diagnostics.",
        "GitOperationRequest": "Performs Git lifecycle operations specifically on the SharedLLM core repo.",
        "DeploymentRequest": "Restarts or inspects SharedLLM Docker containers.",
        "VolumeInventoryRequest": "Inspects Docker volume usage (Admin only).",
        "CapabilityIndexRequest": "Triggers this re-indexing script to refresh tool definitions."
    }
    
    # Process Execution Schemas
    for class_name, description in schema_map.items():
        model = getattr(exec_schemas, class_name, None)
        if model:
            capabilities.append({
                "name": class_name,
                "description": description,
                "schema": get_json_schema(model),
                "type": "execution_schema"
            })
            log.info(f"Prepared execution schema: {class_name}")

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

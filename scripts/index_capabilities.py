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
try:
    from services.workspace_runtime import schemas as workspace_schemas
except ImportError:
    workspace_schemas = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("indexer")

RAG_SVC_URL = os.getenv("RAG_SVC_URL", "http://127.0.0.1:8004")
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")
PHRASEBOOK_PATH = os.getenv("PHRASEBOOK_PATH", "data/phrasebook.json")

def get_json_schema(model: Type[BaseModel]):
    """Returns a simplified string representation of the Pydantic model for RAG indexing."""
    return json.dumps(model.model_json_schema(), indent=2)

def index_capabilities():
    capabilities = []

    # 1. Index Execution Schemas
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
    }
    
    workspace_map = {
        "WorkspaceFileAction": "Orchestrates file writes and patches within a Git-backed workspace. Use for editing code.",
        "WorkspaceGitAction": "Performs Git lifecycle operations (pull, commit, branch, status).",
        "WorkspaceSyncAction": "Synchronizes workspace files with Nextcloud or other storage providers."
    }

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

    if workspace_schemas:
        for class_name, description in workspace_map.items():
            model = getattr(workspace_schemas, class_name, None)
            if model:
                capabilities.append({
                    "name": class_name,
                    "description": description,
                    "schema": get_json_schema(model),
                    "type": "execution_schema"
                })
                log.info(f"Prepared workspace schema: {class_name}")

    # 2. Index Intents from Phrasebook
    if os.path.exists(PHRASEBOOK_PATH):
        try:
            with open(PHRASEBOOK_PATH, "r") as f:
                phrasebook = json.load(f)
                for intent, examples in phrasebook.items():
                    capabilities.append({
                        "name": intent,
                        "description": f"Intent: {intent}. Examples: {', '.join(examples[:3])}",
                        "schema": f"Intent label used for fast-path routing: {intent}",
                        "type": "intent"
                    })
            log.info(f"Prepared {len(phrasebook)} intents from phrasebook.")
        except Exception as e:
            log.error(f"Failed to read phrasebook: {e}")

    # 3. Push to RAG Service
    if not capabilities:
        log.warning("No capabilities found to index.")
        return

    try:
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

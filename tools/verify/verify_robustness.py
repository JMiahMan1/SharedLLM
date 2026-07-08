
import logging
import os
import sys

from fastapi.testclient import TestClient

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Import App
from app.main import app  # pyright: ignore[reportMissingImports]

from app.settings import GlobalResources

# Setup Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("VERIFY")

# --- Mock Logic ---

class MockCollection:
    def __init__(self, documents):
        self.documents = documents
        self._collection = self # Hack for accessing internal collection

    def get(self, ids=None, where=None, include=None):
        # Naive implementation of Chroma get
        results = []
        for doc in self.documents:
            match = True
            if ids and doc.metadata.get("entity_id") not in ids:
                match = False
            if where:
                for k, v in where.items():
                    if doc.metadata.get(k) != v:
                        match = False
                        break
            if match:
                results.append(doc)

        # Format response to match Chroma (dict with lists)
        return {
            "ids": [d.metadata.get("entity_id") for d in results],
            "metadatas": [d.metadata for d in results],
            "documents": [d.page_content for d in results]
        }

    def similarity_search(self, query, k=5):
        # Return docs only
        scored = self.similarity_search_with_score(query, k)
        return [s[0] for s in scored]

    def similarity_search_with_score(self, query, k=5):
        # Naive text match scoring
        scored = []
        q_norm = query.lower()

        for doc in self.documents:
            content = doc.page_content.lower()
            fname = doc.metadata.get("friendly_name", "").lower()
            eid = doc.metadata.get("entity_id", "").lower()

            score = 2.0 # No match

            if q_norm == fname or q_norm == eid:
                score = 0.0 # Exact
            elif q_norm in fname or q_norm in eid:
                score = 0.5 # Partial
            elif q_norm in content:
                score = 0.8

            scored.append((doc, score))

        scored.sort(key=lambda x: x[1])
        return scored[:k]


def discover_entities_mock():
    """Fetches real HA data but puts it in a Mock Collection."""
    log.info("Fetching HA Data for Mock Collection...")
    from app.utils.ha_fetch import fetch_ha_data, get_device_info  # pyright: ignore[reportMissingImports]
    from langchain_core.documents import Document  # pyright: ignore[assignment]

    # Fetch raw data
    states, device_registry, entity_registry, area_registry = fetch_ha_data()

    docs = []
    ALLOWED_DOMAINS = ["light", "switch", "media_player", "script", "scene", "lock", "fan", "cover"]

    for s in states:
        eid = s["entity_id"]
        if eid.split('.')[0] not in ALLOWED_DOMAINS: continue

        attrs = s.get("attributes", {})
        _device_name, integration, area_name = get_device_info(eid, device_registry, entity_registry, area_registry)

        # Build Metadata
        metadata = {
            "entity_id": eid,
            "domain": eid.split('.')[0],
            "friendly_name": attrs.get("friendly_name", eid),
            "integration": integration,
            "area_name": area_name,
            "attributes": str(attrs),
            "capabilities": str(attrs.get("supported_features", 0))
        }

        content = f"{metadata['friendly_name']} ({eid}) is {integration}."
        docs.append(Document(page_content=content, metadata=metadata))

    log.info(f"Mock Collection Populated with {len(docs)} documents.")
    return MockCollection(docs)


def discover_entities(collection):
    """
    Dynamically find suitable test candidates from the collection.
    """
    log.info("Discovering test candidates from Database...")

    candidates = {
        "tv": None,
        "speaker": None,
        "light": None,
        "area_group": None
    }

    # Helper to scan
    results = collection.similarity_search("device", k=100)

    for doc in results:
        eid = doc.metadata.get("entity_id", "")
        domain = eid.split('.')[0]
        integ = doc.metadata.get("integration", "unknown").lower()
        friendly_name = doc.metadata.get("friendly_name", "")
        area = doc.metadata.get("area_name", "")

        # Find TV
        if not candidates["tv"]:
            if domain == "media_player" and any(x in integ for x in ["roku", "androidtv", "webostv", "samsungtv", "braviatv", "tv"]):
                if "cast" not in integ:
                    candidates["tv"] = {"eid": eid, "name": friendly_name, "integ": integ, "area": area}  # type: ignore[dict-item]
                    log.info(f"Found TV Candidate: {friendly_name} ({eid}) [{integ}]")

        # Find Speaker
        if not candidates["speaker"]:
            if domain == "media_player" and ("music_assistant" in integ or "sonos" in integ or "speaker" in integ):
                 candidates["speaker"] = {"eid": eid, "name": friendly_name, "integ": integ, "area": area}  # type: ignore[dict-item]
                 log.info(f"Found Speaker Candidate: {friendly_name} ({eid}) [{integ}]")

        # Find Light
        if not candidates["light"]:
            if domain == "light":
                candidates["light"] = {"eid": eid, "name": friendly_name, "integ": integ, "area": area}  # type: ignore[dict-item]
                log.info(f"Found Light Candidate: {friendly_name} ({eid})")

    return candidates


def test_ping(client):
    log.info("--- Testing Ping ---")
    resp = client.get("/api/ping")
    log.info(f"Ping Response: {resp.status_code} {resp.text}")

    # Debug routes
    # log.info([r.path for r in app.routes])


def test_intent_splitting(client, candidates):
    log.info("--- Testing Intent Splitting ---")

    tv = candidates.get("tv")
    speaker = candidates.get("speaker")

    if tv:
        q = f"Watch Netflix on {tv['name']}"
        log.info(f"TEST: '{q}' (Expect TV)")
        resp = client.post("/api/chat", json={"query": q, "user": "test_script"})
        try:
            res_json = resp.json()
            log.info(f"Response: {res_json}")
        except Exception:
            log.error(f"Failed to parse response: {resp.text}")

    if speaker:
        q = f"Play music on {speaker['name']}"
        log.info(f"TEST: '{q}' (Expect Speaker)")
        resp = client.post("/api/chat", json={"query": q, "user": "test_script"})
        try:
            res_json = resp.json()
            log.info(f"Response: {res_json}")
        except Exception:
            log.error(f"Failed to parse response: {resp.text}")

def test_power_control(client, candidates):
    log.info("--- Testing Power Control ---")
    tv = candidates.get("tv")

    if tv:
        q = f"Turn off {tv['name']}"
        log.info(f"TEST: '{q}' (Expect Power Off command to TV)")
        resp = client.post("/api/chat", json={"query": q, "user": "test_script"})
        try:
            log.info(f"Response: {resp.json()}")
        except Exception:
            log.error(f"Failed to parse response: {resp.text}")

def main():
    log.info("Initializing Verification (MOCKED DB)...")

    mock_collection = discover_entities_mock()
    if not mock_collection.documents:
        log.error("Failed to fetch any entities from HA.")
        return

    # Patch GlobalResources
    GlobalResources.ha_collection = mock_collection  # type: ignore[attr-defined]

    # We also need to populate verify candidates
    candidates = discover_entities(mock_collection)

    with TestClient(app) as client:
        # Re-patch
        GlobalResources.ha_collection = mock_collection  # type: ignore[attr-defined]

        if not any(candidates.values()):
            log.warning("No suitable candidates found in Mock.")
            return

        test_ping(client)
        test_intent_splitting(client, candidates)
        test_power_control(client, candidates)

        log.info("Verification Complete.")

if __name__ == "__main__":
    main()

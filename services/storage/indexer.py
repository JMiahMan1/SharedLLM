# services/storage/indexer.py
from __future__ import annotations
import asyncio
import json
import os
import logging
from typing import TYPE_CHECKING
from collections import Counter, defaultdict
from pathlib import PurePosixPath

if TYPE_CHECKING:
    from .providers import StorageProvider

try:
    from .models import ContentIndexItem, StorageEntry
except ImportError:
    from models import ContentIndexItem, StorageEntry

log = logging.getLogger("storage.indexer")
_indexer_state: dict = {"paused": False, "expiry": 0.0}

def set_indexer_pause(paused: bool):
    _indexer_state["paused"] = paused
    if paused:
        import time
        _indexer_state["expiry"] = time.time() + 60.0

def is_indexer_paused():
    if _indexer_state["paused"]:
        import time
        if time.time() > _indexer_state["expiry"]:
            _indexer_state["paused"] = False
    return _indexer_state["paused"]

GLOBAL_SKIP_LIST = [
    "node_modules", ".venv", "venv", ".git", "__pycache__", ".pytest_cache", 
    ".cache", ".local", ".vscode", ".idea", "dist", "build", ".tox", ".nox",
    "site-packages", "bin", "include", "lib", "lib64"
]

class CheckpointManager:
    def __init__(self, checkpoint_file: str = "index_checkpoint.json"):
        self.checkpoint_file = checkpoint_file
        self.data = self._load()

    def _load(self):
        if os.path.exists(self.checkpoint_file):
            try:
                with open(self.checkpoint_file, "r") as f:
                    return json.load(f)
            except Exception as e:
                log.error(f"Failed to load checkpoint: {e}")
        return {}

    def save(self):
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.checkpoint_file)), exist_ok=True)
            with open(self.checkpoint_file, "w") as f:
                json.dump(self.data, f)
        except Exception as e:
            log.error(f"Failed to save checkpoint: {e}")

    def is_indexed(self, path: str, mtime: str) -> bool:
        return self.data.get(path) == mtime

    def mark_indexed(self, path: str, mtime: str):
        self.data[path] = mtime


FILE_RULES = {
    ".txt": {
        "item_type": "document",
        "subtype": "plain_text",
        "role": "text document",
        "capabilities": ["full_text", "semantic_search"],
        "tools": ["rag"],
        "usage": "Useful for general text extraction.",
    },
    ".md": {
        "item_type": "document",
        "subtype": "markdown",
        "role": "formatted documentation",
        "capabilities": ["full_text", "semantic_search", "structure_scan"],
        "tools": ["rag", "document_parser"],
        "usage": "Useful for structured documentation and personal notes.",
    },
    ".pdf": {
        "item_type": "document",
        "subtype": "pdf",
        "role": "rich document",
        "capabilities": ["full_text", "semantic_search"],
        "tools": ["rag", "pdf_parser"],
        "usage": "Useful for static documents and reports.",
    },
    ".docx": {
        "item_type": "document",
        "subtype": "word_processing",
        "role": "word processing document",
        "capabilities": ["full_text", "semantic_search", "metadata_only"],
        "tools": ["rag", "document_parser"],
        "usage": "Useful for document text extraction and summary.",
    },
    ".csv": {
        "item_type": "document",
        "subtype": "spreadsheet",
        "role": "tabular dataset",
        "capabilities": ["full_text", "table_extraction", "structured_parse"],
        "tools": ["table_parser", "rag"],
        "usage": "Useful for table parsing, aggregation, and retrieval.",
    },
}

def build_content_index(entries: list[StorageEntry]) -> list[ContentIndexItem]:
    # Filter out skipped paths
    normalized = []
    for entry in entries:
        parts = entry.path.strip("/").split("/")
        if any(p in GLOBAL_SKIP_LIST for p in parts):
            continue
        normalized.append(entry)

    path_map = {entry.path: entry for entry in normalized}
    child_map = _build_child_map(normalized)

    items = [
        _classify_directory(entry, child_map, path_map) if entry.is_dir else _classify_file(entry)
        for entry in normalized
    ]
    item_map = {item.path: item for item in items}

    for item in items:
        item.related_items = _related_items(item.path, item_map, child_map)
    return items

async def extract_and_chunk_contents(
    provider: StorageProvider,
    items: list[ContentIndexItem],
    checkpoint: CheckpointManager | None = None
) -> list[dict]:
    chunks = []
    for item in items:
        # Checkpoint skip
        if checkpoint and item.mtime and checkpoint.is_indexed(item.path, item.mtime):
            continue

        # Resource Prioritization: Pause
        while is_indexer_paused():
            await asyncio.sleep(1.0)

        log.info(f"Indexing metadata for: {item.path}")
        # Always index the metadata/skeleton of the item
        chunks.append({
            "content": f"File/Folder: {item.name}\nPath: {item.path}\nType: {item.item_type}/{item.subtype}\nRole: {item.role}",
            "metadata": {
                "path": item.path,
                "name": item.name,
                "is_dir": item.is_dir,
                "item_type": item.item_type,
                "subtype": item.subtype,
                "role": item.role,
                "is_metadata": True,
                "session_id": "temp" # Filled by main.py
            }
        })

        # Optionally index full text
        if not item.is_dir and "full_text" in item.extractable_capabilities:
            log.info(f"Extracting full text for: {item.path}")
            content = await provider.get_content(item.path)
            if content:
                file_chunks = chunk_text(content)
                for i, text in enumerate(file_chunks):
                    chunks.append({
                        "content": text,
                        "metadata": {
                            "path": item.path,
                            "name": item.name,
                            "chunk_index": i,
                            "item_type": item.item_type,
                            "subtype": item.subtype,
                            "is_chunk": True
                        }
                    })
            
        if checkpoint and item.mtime:
            checkpoint.mark_indexed(item.path, item.mtime)
            checkpoint.save()
            
    return chunks

def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 200) -> list[str]:
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += (chunk_size - overlap)
    return chunks

def _build_child_map(entries: list[StorageEntry]) -> dict[str, list[StorageEntry]]:
    child_map = defaultdict(list)
    for entry in entries:
        parent = str(PurePosixPath(entry.path).parent)
        if parent == ".":
            parent = "/"
        child_map[parent].append(entry)
    return child_map

def _classify_directory(
    entry: StorageEntry,
    child_map: dict[str, list[StorageEntry]],
    path_map: dict[str, StorageEntry],
) -> ContentIndexItem:
    children = child_map.get(entry.path, [])
    child_exts = Counter(PurePosixPath(child.path).suffix.lower() for child in children if not child.is_dir)
    
    subtype = "generic_directory"
    role = "folder"
    capabilities = ["structure_scan"]
    tools = ["indexer"]
    
    if child_exts[".md"] >= 3:
        subtype = "document_collection"
        role = "document collection"
        capabilities.extend(["full_text", "semantic_search"])
    
    return ContentIndexItem(
        path=entry.path,
        name=entry.name,
        is_dir=True,
        item_type="folder",
        subtype=subtype,
        role=role,
        extractable_capabilities=capabilities,
        recommended_tools=tools,
        size=entry.size,
        mtime=entry.mtime,
    )

def _classify_file(entry: StorageEntry) -> ContentIndexItem:
    suffix = PurePosixPath(entry.path).suffix.lower()
    rule = FILE_RULES.get(suffix)

    if rule is None:
        item_type = "binary" if entry.content_type and not entry.content_type.startswith("text/") else "document"
        subtype = "unknown_binary" if item_type == "binary" else "unknown_text"
        role = "unclassified file"
        capabilities = ["metadata_only"] if item_type == "binary" else ["full_text"]
        tools = ["media"] if item_type == "binary" else ["rag"]
        usage = "Useful for generic text extraction if content is parseable."
        restrictions = ["binary_only"] if item_type == "binary" else []
    else:
        item_type = str(rule["item_type"])
        subtype = str(rule["subtype"])
        role = str(rule["role"])
        capabilities = list(rule["capabilities"])
        tools = list(rule["tools"])
        usage = str(rule["usage"])
        restrictions = list(rule.get("restrictions", []))

    item = ContentIndexItem(
        path=entry.path,
        name=entry.name,
        is_dir=False,
        item_type=item_type,
        subtype=subtype,
        role=role,
        extension=suffix,
        size=entry.size,
        mtime=entry.mtime,
        extractable_capabilities=capabilities,
        recommended_tools=tools,
        restrictions=restrictions,
        usage_hints=usage,
    )
    log.info(f"Classified file: {item.path} -> {item.item_type}/{item.subtype} [Caps: {item.extractable_capabilities}]")
    return item

def _related_items(
    path: str,
    item_map: dict[str, ContentIndexItem],
    child_map: dict[str, list[StorageEntry]],
) -> list[str]:
    related = []
    parent = str(PurePosixPath(path).parent)
    if parent == ".":
        parent = "/"

    siblings = child_map.get(parent, [])
    for sibling in siblings:
        if sibling.path != path:
            related.append(sibling.path)
        if len(related) >= 5:
            break
    return related

def summarize_index(items: list[ContentIndexItem]) -> dict:
    types = Counter(i.item_type for i in items)
    subtypes = Counter(i.subtype for i in items)
    return {
        "total_items": len(items),
        "type_breakdown": dict(types),
        "subtype_breakdown": dict(subtypes),
    }

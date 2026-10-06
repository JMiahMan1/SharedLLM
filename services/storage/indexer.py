# services/storage/indexer.py
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
from collections import Counter, defaultdict
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

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

# A section at or below this length is embedded whole rather than windowed.
# It matches ``chunk_text``'s window so a section is never split without
# reason, and it keeps the embedding focused: a 2000-character window of one
# chapter retrieves better than a 2000-character window spanning three.
_SECTION_WHOLE_LIMIT = 1000

class CheckpointManager:
    def __init__(self, checkpoint_file: str = "index_checkpoint.json"):
        self.checkpoint_file = checkpoint_file
        self.data = self._load()

    def _load(self):
        if os.path.exists(self.checkpoint_file):
            try:
                with open(self.checkpoint_file) as f:
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
        "capabilities": ["metadata_only"],
        "tools": [],
        "restrictions": ["no_text_extractor"],
        "usage": "Static documents and reports. Metadata only until a PDF text extractor runs in the storage image.",
    },
    ".docx": {
        "item_type": "document",
        "subtype": "word_processing",
        "role": "word processing document",
        "capabilities": ["metadata_only"],
        "tools": [],
        "restrictions": ["no_text_extractor"],
        "usage": "Word processing documents. Metadata only until a DOCX text extractor runs in the storage image.",
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

def _is_provider_document(entry: StorageEntry) -> bool:
    """True when a provider synthesised this entry and vouched for it.

    ``GLOBAL_SKIP_LIST`` exists so the indexer does not descend into a source
    tree and embed a million lines of vendored code. A provider that hands us a
    *file* carrying its own metadata has already decided the thing is a
    document, so a Calibre author directory that happens to be called "lib"
    must not silently delete a whole shelf. Directory entries stay filtered:
    a synthetic collection still gets skipped if it is named like a build dir.
    """
    return not entry.is_dir and bool(entry.metadata)


def build_content_index(entries: list[StorageEntry]) -> list[ContentIndexItem]:
    # Filter out skipped paths
    normalized = []
    for entry in entries:
        parts = entry.path.strip("/").split("/")
        if any(p in GLOBAL_SKIP_LIST for p in parts) and not _is_provider_document(entry):
            log.warning(
                "Skipping %s: a path segment is on the code-tree skip list and the "
                "provider did not mark it as a document",
                entry.path,
            )
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
            "content": _skeleton_content(item),
            "metadata": {
                **_scalar_metadata(item.metadata),
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
            sections = await _provider_sections(provider, item.path)
            if sections:
                chunks.extend(_section_chunks(item, sections))
            else:
                res = provider.get_content(item.path)
                content = await res if inspect.isawaitable(res) else res
                if content:
                    file_chunks = chunk_text(content)
                    for i, text in enumerate(file_chunks):
                        chunks.append({
                            "content": text,
                            "metadata": _chunk_metadata(item, i)
                        })

        if checkpoint and item.mtime:
            checkpoint.mark_indexed(item.path, item.mtime)
            checkpoint.save()

    return chunks

async def _provider_sections(provider: StorageProvider, path: str) -> list:
    """Ask a provider for the document's real structure, tolerating its absence."""
    getter = getattr(provider, "get_sections", None)
    if getter is None:
        return []
    res = getter(path)
    if inspect.isawaitable(res):
        res = await res
    return list(res or [])

def _section_chunks(item: ContentIndexItem, sections: list) -> list[dict]:
    """Turn labelled sections into chunks that keep their label.

    A section that already fits inside one chunk is emitted whole, because a
    chapter boundary is a far better retrieval unit than an arbitrary 1000
    character cut. Only a section too large to embed usefully is windowed, and
    every window keeps the parent section's label so the citation still points
    at the right chapter.
    """
    chunks: list[dict] = []
    part = 0
    for section in sections:
        text = (section.text or "").strip()
        if not text:
            continue
        windows = chunk_text(text) if len(text) > _SECTION_WHOLE_LIMIT else [text]
        for window in windows:
            chunks.append({
                "content": window,
                "metadata": _chunk_metadata(
                    item,
                    part,
                    part_label=section.label,
                    section_ordinal=section.ordinal,
                )
            })
            part += 1
    return chunks

def _chunk_metadata(
    item: ContentIndexItem,
    chunk_index: int,
    part_label: str | None = None,
    section_ordinal: int | None = None,
) -> dict:
    """Build chunk metadata, flattened to scalars and led by the locator.

    Only scalars survive: ``/rag/sync/files`` coerces every other type to
    ``str()`` before storing it, so a nested dict would arrive at search time as
    a Python repr rather than as metadata. Provider metadata is merged in last
    and never allowed to overwrite the identity of the row, because ``path``
    and ``chunk_index`` are what the indexer uses to decide what is stale.
    """
    meta: dict = {
        "path": item.path,
        "name": item.name,
        "chunk_index": chunk_index,
        "item_type": item.item_type,
        "subtype": item.subtype,
        "is_chunk": True,
    }
    if part_label:
        meta["chapter"] = part_label
    if section_ordinal is not None:
        meta["chapter_ordinal"] = section_ordinal
    for key, value in _scalar_metadata(item.metadata).items():
        meta.setdefault(key, value)
    return meta

def _skeleton_content(item: ContentIndexItem) -> str:
    """Render an item's identity as prose, including anything a provider added.

    The skeleton row is the only thing indexed for a document the indexer
    cannot read the body of, and it is also what a search for an author or a
    title matches against. A provider that knows more than the path -- a Calibre
    book knows its author, series and tags -- says so here.
    """
    lines = [
        f"File/Folder: {item.name}",
        f"Path: {item.path}",
        f"Type: {item.item_type}/{item.subtype}",
        f"Role: {item.role}",
    ]
    for key, value in _scalar_metadata(item.metadata).items():
        lines.append(f"{key.replace('_', ' ').capitalize()}: {value}")
    return "\n".join(lines)

def _scalar_metadata(metadata: dict) -> dict:
    """Keep only str/int/float/bool values, and stringify the rest predictably."""
    out: dict = {}
    for key, value in (metadata or {}).items():
        if isinstance(value, (str, int, float, bool)):
            out[key] = value
        elif isinstance(value, (list, tuple, set)):
            out[key] = "; ".join(str(v) for v in value)
        elif value is None:
            continue
        else:
            out[key] = str(value)
    return out

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
        metadata=dict(entry.metadata),
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
        metadata=dict(entry.metadata),
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

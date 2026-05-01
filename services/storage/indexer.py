from __future__ import annotations
import asyncio
import json
import os
from typing import TYPE_CHECKING
from collections import Counter, defaultdict
from pathlib import PurePosixPath

if TYPE_CHECKING:
    from .providers import StorageProvider

try:
    from .models import ContentIndexItem, StorageEntry
except ImportError:
    from models import ContentIndexItem, StorageEntry

INDEXER_PAUSED = False

def set_indexer_pause(paused: bool):
    global INDEXER_PAUSED
    INDEXER_PAUSED = paused

class CheckpointManager:
    def __init__(self, checkpoint_file: str = "/data/index_checkpoint.json"):
        self.checkpoint_file = checkpoint_file
        self.data = self._load()

    def _load(self):
        if os.path.exists(self.checkpoint_file):
            try:
                with open(self.checkpoint_file, "r") as f:
                    return json.load(f)
            except:
                return {}
        return {}

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.checkpoint_file), exist_ok=True)
            with open(self.checkpoint_file, "w") as f:
                json.dump(self.data, f)
        except Exception as e:
            print(f"Failed to save checkpoint: {e}")

    def is_indexed(self, path: str, mtime: str) -> bool:
        return self.data.get(path) == mtime

    def mark_indexed(self, path: str, mtime: str):
        self.data[path] = mtime



FILE_RULES = {
    ".md": {
        "item_type": "markdown",
        "subtype": "markdown_document",
        "role": "text knowledge document",
        "capabilities": ["full_text", "semantic_search", "structure_scan"],
        "tools": ["rag", "notes", "document_parser"],
        "usage": "Useful for summarization, note retrieval, and cross-linking with other text documents.",
    },
    ".markdown": {
        "item_type": "markdown",
        "subtype": "markdown_document",
        "role": "text knowledge document",
        "capabilities": ["full_text", "semantic_search", "structure_scan"],
        "tools": ["rag", "notes", "document_parser"],
        "usage": "Useful for summarization, note retrieval, and cross-linking with other text documents.",
    },
    ".mdx": {
        "item_type": "markdown",
        "subtype": "mdx_document",
        "role": "markdown with embedded components",
        "capabilities": ["full_text", "semantic_search", "structure_scan"],
        "tools": ["rag", "document_parser"],
        "usage": "Useful as documentation, though embedded syntax may need specialized parsing.",
    },
    ".txt": {
        "item_type": "document",
        "subtype": "plain_text",
        "role": "plain text document",
        "capabilities": ["full_text", "semantic_search"],
        "tools": ["rag", "document_parser"],
        "usage": "Useful for simple text extraction and retrieval.",
    },
    ".text": {
        "item_type": "document",
        "subtype": "plain_text",
        "role": "plain text document",
        "capabilities": ["full_text", "semantic_search"],
        "tools": ["rag", "document_parser"],
        "usage": "Useful for simple text extraction and retrieval.",
    },
    ".log": {
        "item_type": "document",
        "subtype": "log_file",
        "role": "log output",
        "capabilities": ["full_text", "semantic_search"],
        "tools": ["rag", "document_parser"],
        "usage": "Useful for troubleshooting and timeline reconstruction.",
    },
    ".rtf": {
        "item_type": "document",
        "subtype": "rich_text",
        "role": "rich text document",
        "capabilities": ["full_text", "semantic_search"],
        "tools": ["rag", "document_parser"],
        "usage": "Useful for extracting formatted text content.",
    },
    ".pdf": {
        "item_type": "document",
        "subtype": "pdf",
        "role": "portable document",
        "capabilities": ["full_text", "semantic_search", "metadata_only"],
        "tools": ["rag", "document_parser"],
        "usage": "Useful for document summarization, retrieval, and metadata extraction.",
    },
    ".doc": {
        "item_type": "document",
        "subtype": "word_processing",
        "role": "word processing document",
        "capabilities": ["full_text", "semantic_search", "metadata_only"],
        "tools": ["rag", "document_parser"],
        "usage": "Useful for document text extraction and summary.",
    },
    ".docx": {
        "item_type": "document",
        "subtype": "word_processing",
        "role": "word processing document",
        "capabilities": ["full_text", "semantic_search", "metadata_only"],
        "tools": ["rag", "document_parser"],
        "usage": "Useful for document text extraction and summary.",
    },
    ".odt": {
        "item_type": "document",
        "subtype": "word_processing",
        "role": "open document text file",
        "capabilities": ["full_text", "semantic_search", "metadata_only"],
        "tools": ["rag", "document_parser"],
        "usage": "Useful for open document text extraction and indexing.",
    },
    ".csv": {
        "item_type": "document",
        "subtype": "spreadsheet",
        "role": "tabular dataset",
        "capabilities": ["full_text", "table_extraction", "structured_parse"],
        "tools": ["table_parser", "rag"],
        "usage": "Useful for table parsing, aggregation, and retrieval.",
    },
    ".tsv": {
        "item_type": "document",
        "subtype": "spreadsheet",
        "role": "tabular dataset",
        "capabilities": ["full_text", "table_extraction", "structured_parse"],
        "tools": ["table_parser", "rag"],
        "usage": "Useful for table parsing, aggregation, and retrieval.",
    },
    ".xls": {
        "item_type": "document",
        "subtype": "spreadsheet",
        "role": "spreadsheet workbook",
        "capabilities": ["table_extraction", "metadata_only", "structured_parse"],
        "tools": ["table_parser"],
        "usage": "Useful for sheet extraction and spreadsheet analysis.",
    },
    ".xlsx": {
        "item_type": "document",
        "subtype": "spreadsheet",
        "role": "spreadsheet workbook",
        "capabilities": ["table_extraction", "metadata_only", "structured_parse"],
        "tools": ["table_parser"],
        "usage": "Useful for sheet extraction and spreadsheet analysis.",
    },
    ".ods": {
        "item_type": "document",
        "subtype": "spreadsheet",
        "role": "open spreadsheet workbook",
        "capabilities": ["table_extraction", "metadata_only", "structured_parse"],
        "tools": ["table_parser"],
        "usage": "Useful for sheet extraction and spreadsheet analysis.",
    },
    ".ppt": {
        "item_type": "document",
        "subtype": "presentation",
        "role": "slide deck",
        "capabilities": ["full_text", "metadata_only"],
        "tools": ["document_parser", "rag"],
        "usage": "Useful for slide summarization and presentation review.",
    },
    ".pptx": {
        "item_type": "document",
        "subtype": "presentation",
        "role": "slide deck",
        "capabilities": ["full_text", "metadata_only"],
        "tools": ["document_parser", "rag"],
        "usage": "Useful for slide summarization and presentation review.",
    },
    ".odp": {
        "item_type": "document",
        "subtype": "presentation",
        "role": "open presentation deck",
        "capabilities": ["full_text", "metadata_only"],
        "tools": ["document_parser", "rag"],
        "usage": "Useful for slide summarization and presentation review.",
    },
    ".epub": {
        "item_type": "ebook",
        "subtype": "epub",
        "role": "ebook",
        "capabilities": ["full_text", "semantic_search", "metadata_only"],
        "tools": ["ebook_parser", "rag"],
        "usage": "Useful for chapter extraction, search, and long-form reading summaries.",
    },
    ".mobi": {
        "item_type": "ebook",
        "subtype": "mobi",
        "role": "ebook",
        "capabilities": ["full_text", "semantic_search", "metadata_only"],
        "tools": ["ebook_parser", "rag"],
        "usage": "Useful for chapter extraction, search, and long-form reading summaries.",
    },
    ".azw": {
        "item_type": "ebook",
        "subtype": "kindle_ebook",
        "role": "ebook",
        "capabilities": ["full_text", "semantic_search", "metadata_only"],
        "tools": ["ebook_parser", "rag"],
        "usage": "Useful for chapter extraction, search, and long-form reading summaries.",
    },
    ".azw3": {
        "item_type": "ebook",
        "subtype": "kindle_ebook",
        "role": "ebook",
        "capabilities": ["full_text", "semantic_search", "metadata_only"],
        "tools": ["ebook_parser", "rag"],
        "usage": "Useful for chapter extraction, search, and long-form reading summaries.",
    },
    ".fb2": {
        "item_type": "ebook",
        "subtype": "fictionbook",
        "role": "ebook",
        "capabilities": ["full_text", "semantic_search", "metadata_only"],
        "tools": ["ebook_parser", "rag"],
        "usage": "Useful for chapter extraction, search, and long-form reading summaries.",
    },
    ".html": {
        "item_type": "document",
        "subtype": "html_document",
        "role": "web document",
        "capabilities": ["full_text", "semantic_search", "structured_parse"],
        "tools": ["document_parser", "rag"],
        "usage": "Useful for text extraction from exported or saved web pages.",
    },
    ".htm": {
        "item_type": "document",
        "subtype": "html_document",
        "role": "web document",
        "capabilities": ["full_text", "semantic_search", "structured_parse"],
        "tools": ["document_parser", "rag"],
        "usage": "Useful for text extraction from exported or saved web pages.",
    },
    ".xml": {
        "item_type": "structured_data",
        "subtype": "xml",
        "role": "structured text document",
        "capabilities": ["full_text", "structured_parse"],
        "tools": ["structured_parser", "rag"],
        "usage": "Useful for structured parsing and metadata extraction.",
    },
    ".json": {
        "item_type": "structured_data",
        "subtype": "json",
        "role": "structured data file",
        "capabilities": ["full_text", "structured_parse"],
        "tools": ["structured_parser", "rag"],
        "usage": "Useful for structured parsing, schemas, and configuration review.",
    },
    ".yaml": {
        "item_type": "structured_data",
        "subtype": "yaml",
        "role": "structured data file",
        "capabilities": ["full_text", "structured_parse"],
        "tools": ["structured_parser", "rag"],
        "usage": "Useful for structured parsing, schemas, and configuration review.",
    },
    ".yml": {
        "item_type": "structured_data",
        "subtype": "yaml",
        "role": "structured data file",
        "capabilities": ["full_text", "structured_parse"],
        "tools": ["structured_parser", "rag"],
        "usage": "Useful for structured parsing, schemas, and configuration review.",
    },
    ".org": {
        "item_type": "document",
        "subtype": "org_mode",
        "role": "structured notes document",
        "capabilities": ["full_text", "semantic_search", "structure_scan"],
        "tools": ["notes", "rag"],
        "usage": "Useful for personal knowledge management and note retrieval.",
    },
    ".rst": {
        "item_type": "document",
        "subtype": "restructured_text",
        "role": "documentation source",
        "capabilities": ["full_text", "semantic_search", "structure_scan"],
        "tools": ["document_parser", "rag"],
        "usage": "Useful for documentation indexing and summarization.",
    },
    ".tex": {
        "item_type": "document",
        "subtype": "latex",
        "role": "typesetting source",
        "capabilities": ["full_text", "semantic_search", "structure_scan"],
        "tools": ["document_parser", "rag"],
        "usage": "Useful for academic and technical document indexing.",
    },
    ".eml": {
        "item_type": "document",
        "subtype": "email_message",
        "role": "single email export",
        "capabilities": ["full_text", "metadata_only"],
        "tools": ["document_parser", "rag"],
        "usage": "Useful for communication archives and message search.",
    },
    ".mbox": {
        "item_type": "document",
        "subtype": "mail_archive",
        "role": "email archive",
        "capabilities": ["full_text", "metadata_only"],
        "tools": ["document_parser", "rag"],
        "usage": "Useful for bulk communication archives and search.",
    },
    ".enex": {
        "item_type": "document",
        "subtype": "note_export",
        "role": "note export archive",
        "capabilities": ["full_text", "structured_parse", "metadata_only"],
        "tools": ["notes", "structured_parser", "rag"],
        "usage": "Useful for importing and retrieving note collections.",
    },
    ".ics": {
        "item_type": "structured_data",
        "subtype": "calendar_event_set",
        "role": "calendar export",
        "capabilities": ["full_text", "structured_parse", "metadata_only"],
        "tools": ["calendar", "structured_parser"],
        "usage": "Useful for event extraction and calendar synchronization.",
    },
    ".vcf": {
        "item_type": "structured_data",
        "subtype": "contact_card",
        "role": "contact export",
        "capabilities": ["full_text", "structured_parse", "metadata_only"],
        "tools": ["contacts", "structured_parser"],
        "usage": "Useful for contact import and directory indexing.",
    },
    ".png": {
        "item_type": "image",
        "subtype": "raster_image",
        "role": "image asset",
        "capabilities": ["metadata_only", "thumbnail", "ocr", "visual_description"],
        "tools": ["image_understanding", "ocr", "media"],
        "usage": "Useful for OCR, preview generation, and visual description.",
    },
    ".jpg": {
        "item_type": "image",
        "subtype": "raster_image",
        "role": "image asset",
        "capabilities": ["metadata_only", "thumbnail", "ocr", "visual_description"],
        "tools": ["image_understanding", "ocr", "media"],
        "usage": "Useful for OCR, preview generation, and visual description.",
    },
    ".jpeg": {
        "item_type": "image",
        "subtype": "raster_image",
        "role": "image asset",
        "capabilities": ["metadata_only", "thumbnail", "ocr", "visual_description"],
        "tools": ["image_understanding", "ocr", "media"],
        "usage": "Useful for OCR, preview generation, and visual description.",
    },
    ".webp": {
        "item_type": "image",
        "subtype": "raster_image",
        "role": "image asset",
        "capabilities": ["metadata_only", "thumbnail", "ocr", "visual_description"],
        "tools": ["image_understanding", "ocr", "media"],
        "usage": "Useful for OCR, preview generation, and visual description.",
    },
    ".gif": {
        "item_type": "image",
        "subtype": "animated_image",
        "role": "animated image asset",
        "capabilities": ["metadata_only", "thumbnail", "visual_description"],
        "tools": ["image_understanding", "media"],
        "usage": "Useful for previewing and describing short animations.",
    },
    ".bmp": {
        "item_type": "image",
        "subtype": "raster_image",
        "role": "image asset",
        "capabilities": ["metadata_only", "thumbnail", "ocr", "visual_description"],
        "tools": ["image_understanding", "ocr", "media"],
        "usage": "Useful for OCR, preview generation, and visual description.",
    },
    ".tif": {
        "item_type": "image",
        "subtype": "scan_image",
        "role": "image asset",
        "capabilities": ["metadata_only", "thumbnail", "ocr", "visual_description"],
        "tools": ["image_understanding", "ocr", "media"],
        "usage": "Useful for scanned document OCR and image preview.",
    },
    ".tiff": {
        "item_type": "image",
        "subtype": "scan_image",
        "role": "image asset",
        "capabilities": ["metadata_only", "thumbnail", "ocr", "visual_description"],
        "tools": ["image_understanding", "ocr", "media"],
        "usage": "Useful for scanned document OCR and image preview.",
    },
    ".heic": {
        "item_type": "image",
        "subtype": "photo_image",
        "role": "photo asset",
        "capabilities": ["metadata_only", "thumbnail", "visual_description"],
        "tools": ["image_understanding", "media"],
        "usage": "Useful for photo preview and visual description.",
    },
    ".avif": {
        "item_type": "image",
        "subtype": "raster_image",
        "role": "image asset",
        "capabilities": ["metadata_only", "thumbnail", "visual_description"],
        "tools": ["image_understanding", "media"],
        "usage": "Useful for preview and visual description.",
    },
    ".svg": {
        "item_type": "image",
        "subtype": "vector_image",
        "role": "vector asset",
        "capabilities": ["full_text", "thumbnail", "structure_scan"],
        "tools": ["image_understanding", "structured_parser"],
        "usage": "Useful for vector asset inspection and text-based parsing.",
    },
    ".mp3": {
        "item_type": "audio",
        "subtype": "music_or_speech_audio",
        "role": "audio media",
        "capabilities": ["metadata_only", "playback", "transcription"],
        "tools": ["media", "transcription"],
        "usage": "Useful for metadata extraction, playback, and speech transcription when applicable.",
    },
    ".m4a": {
        "item_type": "audio",
        "subtype": "music_or_speech_audio",
        "role": "audio media",
        "capabilities": ["metadata_only", "playback", "transcription"],
        "tools": ["media", "transcription"],
        "usage": "Useful for metadata extraction, playback, and speech transcription when applicable.",
    },
    ".wav": {
        "item_type": "audio",
        "subtype": "music_or_speech_audio",
        "role": "audio media",
        "capabilities": ["metadata_only", "playback", "transcription"],
        "tools": ["media", "transcription"],
        "usage": "Useful for metadata extraction, playback, and speech transcription when applicable.",
    },
    ".flac": {
        "item_type": "audio",
        "subtype": "music_or_speech_audio",
        "role": "audio media",
        "capabilities": ["metadata_only", "playback", "transcription"],
        "tools": ["media", "transcription"],
        "usage": "Useful for metadata extraction, playback, and speech transcription when applicable.",
    },
    ".ogg": {
        "item_type": "audio",
        "subtype": "music_or_speech_audio",
        "role": "audio media",
        "capabilities": ["metadata_only", "playback", "transcription"],
        "tools": ["media", "transcription"],
        "usage": "Useful for metadata extraction, playback, and speech transcription when applicable.",
    },
    ".aac": {
        "item_type": "audio",
        "subtype": "music_or_speech_audio",
        "role": "audio media",
        "capabilities": ["metadata_only", "playback", "transcription"],
        "tools": ["media", "transcription"],
        "usage": "Useful for metadata extraction, playback, and speech transcription when applicable.",
    },
    ".mp4": {
        "item_type": "video",
        "subtype": "video_file",
        "role": "video media",
        "capabilities": ["metadata_only", "playback", "thumbnail", "transcription", "visual_description"],
        "tools": ["media", "transcription", "image_understanding"],
        "usage": "Useful for playback, transcript extraction, and visual summarization.",
    },
    ".mkv": {
        "item_type": "video",
        "subtype": "video_file",
        "role": "video media",
        "capabilities": ["metadata_only", "playback", "thumbnail", "transcription", "visual_description"],
        "tools": ["media", "transcription", "image_understanding"],
        "usage": "Useful for playback, transcript extraction, and visual summarization.",
    },
    ".mov": {
        "item_type": "video",
        "subtype": "video_file",
        "role": "video media",
        "capabilities": ["metadata_only", "playback", "thumbnail", "transcription", "visual_description"],
        "tools": ["media", "transcription", "image_understanding"],
        "usage": "Useful for playback, transcript extraction, and visual summarization.",
    },
    ".avi": {
        "item_type": "video",
        "subtype": "video_file",
        "role": "video media",
        "capabilities": ["metadata_only", "playback", "thumbnail", "transcription", "visual_description"],
        "tools": ["media", "transcription", "image_understanding"],
        "usage": "Useful for playback, transcript extraction, and visual summarization.",
    },
    ".webm": {
        "item_type": "video",
        "subtype": "video_file",
        "role": "video media",
        "capabilities": ["metadata_only", "playback", "thumbnail", "transcription", "visual_description"],
        "tools": ["media", "transcription", "image_understanding"],
        "usage": "Useful for playback, transcript extraction, and visual summarization.",
    },
    ".m4v": {
        "item_type": "video",
        "subtype": "video_file",
        "role": "video media",
        "capabilities": ["metadata_only", "playback", "thumbnail", "transcription", "visual_description"],
        "tools": ["media", "transcription", "image_understanding"],
        "usage": "Useful for playback, transcript extraction, and visual summarization.",
    },
    ".py": {
        "item_type": "source_code",
        "subtype": "python",
        "role": "source code",
        "capabilities": ["full_text", "code_navigation", "structure_scan"],
        "tools": ["repo_scanner", "code_navigation", "rag"],
        "usage": "Useful for code navigation, architecture summaries, and implementation changes.",
    },
    ".js": {
        "item_type": "source_code",
        "subtype": "javascript",
        "role": "source code",
        "capabilities": ["full_text", "code_navigation", "structure_scan"],
        "tools": ["repo_scanner", "code_navigation", "rag"],
        "usage": "Useful for code navigation, architecture summaries, and implementation changes.",
    },
    ".ts": {
        "item_type": "source_code",
        "subtype": "typescript",
        "role": "source code",
        "capabilities": ["full_text", "code_navigation", "structure_scan"],
        "tools": ["repo_scanner", "code_navigation", "rag"],
        "usage": "Useful for code navigation, architecture summaries, and implementation changes.",
    },
    ".tsx": {
        "item_type": "source_code",
        "subtype": "typescript_react",
        "role": "source code",
        "capabilities": ["full_text", "code_navigation", "structure_scan"],
        "tools": ["repo_scanner", "code_navigation", "rag"],
        "usage": "Useful for code navigation, architecture summaries, and implementation changes.",
    },
    ".sh": {
        "item_type": "script",
        "subtype": "shell",
        "role": "automation script",
        "capabilities": ["full_text", "structure_scan"],
        "tools": ["repo_scanner", "rag"],
        "usage": "Useful for deployment, automation, and operational review.",
    },
}


def build_content_index(entries: list[StorageEntry]) -> list[ContentIndexItem]:
    normalized = [_normalize_entry(entry) for entry in entries]
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


def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 200) -> list[str]:
    """Split text into overlapping chunks."""
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += (chunk_size - overlap)
    return chunks


async def extract_and_chunk_contents(
    provider: "StorageProvider", 
    items: list[ContentIndexItem],
    checkpoint: CheckpointManager | None = None
) -> list[dict]:
    """Extract content from files and return chunks with metadata."""
    all_chunks = []
    
    # Filter for items that have 'full_text' capability and are not directories
    text_items = [
        item for item in items 
        if not item.is_dir and "full_text" in item.extractable_capabilities
    ]
    
    for item in text_items:
        if "full_text" not in item.extractable_capabilities:
            continue
            
        log.info(f"Indexing content for: {item.path}")

        # 1. Check Pause
        while INDEXER_PAUSED:
            await asyncio.sleep(1.0)

        # 2. Check Checkpoint
        if checkpoint and checkpoint.is_indexed(item.path, str(item.mtime)):
            continue

        content = provider.get_content(item.path)
        if content:
            chunks = chunk_text(content)
            for i, chunk in enumerate(chunks):
                all_chunks.append({
                    "content": chunk,
                    "metadata": {
                        "path": item.path,
                        "name": item.name,
                        "chunk_index": i,
                        "item_type": item.item_type,
                        "subtype": item.subtype
                    }
                })
            
            # 3. Update Checkpoint after successful extraction
            if checkpoint:
                checkpoint.mark_indexed(item.path, str(item.mtime))
                checkpoint.save()
        
        # Yield to event loop
        await asyncio.sleep(0.1)

    return all_chunks


def summarize_index(items: list[ContentIndexItem]) -> dict[str, object]:
    item_counts = Counter(item.item_type for item in items)
    tool_counts = Counter(tool for item in items for tool in item.recommended_tools)
    return {
        "total_items": len(items),
        "item_types": dict(item_counts),
        "tool_coverage": dict(tool_counts),
    }


def _normalize_entry(entry: StorageEntry) -> StorageEntry:
    path = PurePosixPath(entry.path)
    normalized_path = str(path)
    if not normalized_path.startswith("/"):
        normalized_path = "/" + normalized_path.lstrip("./")
    if normalized_path != "/" and str(entry.path).endswith("/") and not normalized_path.endswith("/"):
        normalized_path += "/"
    return StorageEntry(
        path=normalized_path.rstrip("/") or "/",
        name=entry.name,
        is_dir=entry.is_dir,
        size=entry.size,
        mtime=entry.mtime,
        content_type=entry.content_type,
    )


def _build_child_map(entries: list[StorageEntry]) -> dict[str, list[StorageEntry]]:
    child_map: dict[str, list[StorageEntry]] = defaultdict(list)
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
    child_names = {child.name for child in children}
    child_paths = {child.path for child in children}
    child_exts = Counter(PurePosixPath(child.path).suffix.lower() for child in children if not child.is_dir)
    signals = []
    capabilities = ["structure_scan"]
    tools = ["indexer"]
    restrictions = []
    role = "folder"
    subtype = "generic_directory"

    if f"{entry.path}/.git".replace("//", "/") in child_paths or ".git" in child_names:
        subtype = "git_repo"
        role = "git repository root"
        signals.append(".git marker present")
        capabilities.extend(["git_metadata", "code_navigation", "semantic_search"])
        tools = ["repo_scanner", "code_navigation", "rag"]
    elif ".obsidian" in child_names:
        subtype = "notes_vault"
        role = "notes workspace"
        signals.append(".obsidian marker present")
        capabilities.extend(["full_text", "semantic_search", "crosslink_analysis"])
        tools = ["notes", "rag"]
    elif child_exts[".md"] >= 3 or child_exts[".markdown"] >= 3:
        subtype = "document_collection"
        role = "document collection"
        signals.append("markdown-heavy folder")
        capabilities.extend(["full_text", "semantic_search"])
        tools = ["rag", "document_parser"]
    elif child_exts[".mp3"] + child_exts[".m4a"] + child_exts[".flac"] >= 3:
        subtype = "audio_collection"
        role = "audio collection"
        signals.append("audio-heavy folder")
        capabilities.extend(["metadata_only", "playback"])
        tools = ["media", "transcription"]
    elif child_exts[".mp4"] + child_exts[".mkv"] + child_exts[".mov"] >= 2:
        subtype = "video_collection"
        role = "video collection"
        signals.append("video-heavy folder")
        capabilities.extend(["metadata_only", "playback", "thumbnail"])
        tools = ["media", "transcription", "image_understanding"]
    elif child_exts[".epub"] + child_exts[".mobi"] + child_exts[".azw3"] >= 2:
        subtype = "ebook_collection"
        role = "ebook collection"
        signals.append("ebook-heavy folder")
        capabilities.extend(["full_text", "semantic_search"])
        tools = ["ebook_parser", "rag"]

    if entry.name.lower() in {"docs", "documentation"}:
        signals.append("documentation folder name")
        if subtype == "generic_directory":
            subtype = "documentation_collection"
            role = "documentation folder"
            capabilities.extend(["full_text", "semantic_search"])
            tools = ["document_parser", "rag"]

    usage = _directory_usage_hint(subtype)

    return ContentIndexItem(
        path=entry.path,
        name=entry.name,
        is_dir=True,
        item_type="directory",
        subtype=subtype,
        role=role,
        mime_type=entry.content_type,
        size=entry.size,
        mtime=entry.mtime,
        signals=signals or ["directory structure"],
        extractable_capabilities=_unique(capabilities),
        recommended_tools=_unique(tools),
        restrictions=restrictions,
        usage_hints=usage,
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
        usage = "Useful only after a specialized parser is assigned." if item_type == "binary" else "Useful for generic text extraction if content is parseable."
        signals = [f"extension {suffix or 'none'} not mapped"]
        restrictions = ["binary_only"] if item_type == "binary" else []
    else:
        item_type = rule["item_type"]
        subtype = rule["subtype"]
        role = rule["role"]
        capabilities = rule["capabilities"]
        tools = rule["tools"]
        usage = rule["usage"]
        signals = [f"extension match {suffix}"]
        restrictions = ["do_not_edit"] if item_type in {"audio", "video", "image", "ebook"} else []

    if entry.size and entry.size > 50 * 1024 * 1024:
        restrictions.append("large_file")
        signals.append("size over 50MB")

    return ContentIndexItem(
        path=entry.path,
        name=entry.name,
        is_dir=False,
        item_type=item_type,
        subtype=subtype,
        role=role,
        mime_type=entry.content_type,
        extension=suffix or None,
        size=entry.size,
        mtime=entry.mtime,
        signals=signals,
        extractable_capabilities=_unique(capabilities),
        recommended_tools=_unique(tools),
        restrictions=_unique(restrictions),
        usage_hints=usage,
    )


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


def _directory_usage_hint(subtype: str) -> str:
    if subtype == "git_repo":
        return "Useful for git metadata, code navigation, structure-aware summaries, and repository-scoped assistance."
    if subtype == "notes_vault":
        return "Useful for note retrieval, cross-link analysis, and personal knowledge search."
    if subtype == "document_collection":
        return "Useful for document indexing, summarization, and semantic retrieval."
    if subtype == "audio_collection":
        return "Useful for media cataloging, playback, and optional speech transcription."
    if subtype == "video_collection":
        return "Useful for media cataloging, preview generation, and transcription workflows."
    if subtype == "ebook_collection":
        return "Useful for library indexing, long-form search, and chapter extraction."
    if subtype == "documentation_collection":
        return "Useful for architectural summaries and human-facing technical documentation search."
    return "Useful for structural scanning and deeper classification of child items."


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))

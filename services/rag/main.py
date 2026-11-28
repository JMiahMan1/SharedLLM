# services/rag/main.py
"""
Microservice 4: Context & RAG Service

Vector/semantic search and ingestion. ChromaDB has been replaced with a native
SQLite database backed by the ``sqlite-vec`` extension (with a pure-numpy
fallback). See ``docs/SQLITE_VEC_MIGRATION.md``.

Key design notes:
* The embedding **dimension is detected dynamically** from the active
  ``EMBEDDING_MODEL``. The migration blueprint hardcodes ``float[384]``, but the
  deployed model (``nomic-ai/nomic-embed-text-v1.5``) produces 768-dim vectors,
  so a hardcoded dimension would corrupt every insert. We probe the model at
  startup instead.
* All collections (generic + the Section 6 structured ones) store their vectors
  in a single unified ``vec_rag_items`` table keyed by ``id``, and their
  metadata in ``rag_items``. Structured collections additionally mirror into
  dedicated relational tables for direct SQL queries.
"""
import asyncio
import hashlib
import json
import logging
import os
import re
import threading
import time
import traceback
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import asynccontextmanager, suppress
from typing import Any, Literal

import redis
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from services.common.entity_ids import usable_entity_id as _usable_entity_id
from services.common.rag_collections import (
    DEVICE_SOURCES,
    HOME_DEVICES,
    LEGACY_HOME_DEVICES,
    SOURCE_HOME_ASSISTANT,
    SOURCE_HOMEHUB,
    canonical_collection,
)
from services.common.secrets import secret_matches
from services.config import INTERNAL_SECRET
from services.rag import db, topics
from services.rag import sim as sim_store
from services.rag.schemas import IngestRequest, SearchRequest, SearchResponse, SearchResultItem
from services.rag.store import VectorStoreAdapter, build_adapter
from services.shared.info_endpoint import info_router

log = logging.getLogger("rag")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s")

DEFAULT_EMBEDDING_MODEL = "nomic-ai/nomic-embed-text-v1.5"
DEFAULT_EMBEDDING_DIM = 768

EMBED_BATCH_SIZE: int = 128
EMBED_CHAR_BUDGET: int = 5_000_000
EMBED_MAX_CHARS: int = 4000

# Globals populated in the lifespan
conn = None
adapter: VectorStoreAdapter | None = None
BACKEND = "unknown"
embedder = None
#: A cross-encoder: it reads a question and a passage together and scores how
#: well the passage answers it. The Bible's meaning search uses it on its best
#: few dozen candidates, so verses that only share a word or a mood with the
#: question (Deuteronomy 6:2's "fear the LORD" for "worry") sink and drop out.
#: Chosen by measurement on the live Bible (MiniLM-L-12 ranked about the same
#: at twice the time; bge-reranker-base took six times as long).
RERANK_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"
RERANK_MAX_TEXTS = 64
RERANK_MAX_CHARS = 2_000
reranker = None
_reranker_lock = threading.Lock()
EMBEDDING_MODEL = DEFAULT_EMBEDDING_MODEL
EMBEDDING_DIM = DEFAULT_EMBEDDING_DIM


def _conn():
    assert conn is not None, "RAG database not initialized"
    return conn


def _adapter():
    assert adapter is not None, "RAG vector adapter not initialized"
    return adapter

ACTIVE_STATES = {"on", "playing", "idle", "standby", "home", "cooling", "heating", "drying", "cleaning"}


def embed(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts into float vectors using fastembed."""
    if embedder is None:
        raise RuntimeError("Embedder not initialized")
    return [v.tolist() for v in embedder.embed(texts)]


def _batch_by_cost(
    texts: list[str], batch_size: int, char_budget: int
) -> Iterator[list[str]]:
    """Yield batches whose ``count * longest**2`` stays inside ``char_budget``.

    ONNX pads a batch to its longest text, and attention is quadratic in that
    length, so one long string multiplies the activation cost of every other
    text it travels with. Splitting on a fixed count alone therefore lets a
    single pathological entry (see ``embed_batched``) decide the peak memory
    of the whole run. A text that alone exceeds the budget still forms a batch
    of one rather than being dropped -- losing it silently would index fewer
    documents than the caller believes were submitted.
    """
    batch: list[str] = []
    longest = 0
    for text in texts:
        length = len(text)
        if batch and (
            len(batch) >= batch_size
            or (len(batch) + 1) * max(longest, length) ** 2 > char_budget
        ):
            yield batch
            batch = []
            longest = 0
        batch.append(text)
        longest = max(longest, length)
    if batch:
        yield batch


def embed_batched(
    texts: list[str],
    batch_size: int = EMBED_BATCH_SIZE,
    char_budget: int = EMBED_CHAR_BUDGET,
    max_chars: int = EMBED_MAX_CHARS,
) -> list[list[float]]:
    """Embed ``texts`` in cost-bounded batches so peak memory stays flat.

    Count-bounded chunking alone was not enough. Measured against the live
    Home Assistant index (697 entities) with a 6 GiB cgroup: five batches of
    128 texts at <=178 chars held the process flat at ~1.9 GiB (+70 MiB per
    batch), then the final batch of 57 texts -- carrying two 1,144-char
    entries -- jumped 1,905 to 5,654 MiB in a single call and OOM-killed the
    service. The write loop that followed added exactly zero.

    ``char_budget`` bounds ``count * longest**2`` (the quadratic term is what
    the measurements track): the measured-safe batch scores 128 * 178**2 =
    4.1M while the fatal one scores 57 * 1144**2 = 75M, so a 5M budget admits
    the former and splits the latter to three texts per call. Batches also
    stay capped at ``batch_size``. An invalid budget fails loudly instead of
    silently disabling the ceiling.

    A third bound was needed after ``POST /rag/sync/capabilities`` wedged the
    service: its content embeds pretty-printed JSON schemas, tens of KB per
    text, and the budget's never-drop valve delivers such a text to ONNX as a
    batch of one. Measured live, one capability-schema embed held rag at
    5.08 GiB / 6 GiB and 340-378% CPU for minutes -- long enough to starve
    the 5-second health checks and time out every waiting client -- without
    ever tripping the cgroup. ``max_chars`` therefore clips the *embedding
    view* of a text before batching; stored rows and the FTS index keep the
    full content, exactly as a chunk window does. The clip is logged rather
    than silent so an unexpectedly large source is visible to the operator.
    """
    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")
    if char_budget < 1:
        raise ValueError(f"char_budget must be >= 1, got {char_budget}")
    if max_chars < 1:
        raise ValueError(f"max_chars must be >= 1, got {max_chars}")
    clipped = [
        text if len(text) <= max_chars else text[:max_chars] for text in texts
    ]
    clipped_count = sum(
        1 for short, long in zip(clipped, texts, strict=False) if len(short) < len(long)
    )
    if clipped_count:
        log.warning(
            f"[embed_batched] clipping {clipped_count} text(s) to "
            f"{max_chars} chars for embedding; stored content is unchanged"
        )
    batches = list(_batch_by_cost(clipped, batch_size, char_budget))
    vectors: list[list[float]] = []
    for index, batch in enumerate(batches, start=1):
        batch_started = time.monotonic()
        vectors.extend(embed(batch))
        log.info(
            f"[embed_batched] batch {index}/{len(batches)} "
            f"({len(batch)} texts) in {time.monotonic() - batch_started:.1f}s "
            f"rss={_self_rss_mib()}MiB"
        )
    return vectors


def _self_rss_mib() -> int:
    """Resident set size of this process in MiB, read from /proc/self/statm.

    Phase timings without a memory number cannot separate an ONNX arena that
    keeps its allocations from a write loop that leaks: the OOM diagnosis for
    ``/rag/sync/ha`` needed both on the same log line. ``/proc`` is always
    readable on Linux; a missing file reports -1 rather than failing the
    request it is only observing.
    """
    try:
        with open("/proc/self/statm") as fh:
            pages = int(fh.read().split()[1])
        return (pages * 4096) // 1048576
    except Exception:
        return -1


@asynccontextmanager
async def lifespan(app: FastAPI):
    global conn, adapter, embedder, EMBEDDING_MODEL, EMBEDDING_DIM, BACKEND
    from services import config as cfg
    from services.config import resolve_runtime_config

    # Resolve runtime config (sets cfg.EMBEDDING_MODEL from Identity).
    await resolve_runtime_config()

    EMBEDDING_MODEL = cfg.EMBEDDING_MODEL or DEFAULT_EMBEDDING_MODEL
    log.info(f"Initializing RAG Service. Embedding model: {EMBEDDING_MODEL}")

    os.makedirs(os.path.dirname(db.RAG_DB_PATH) or ".", exist_ok=True)

    conn = db.get_db_connection()
    embedder = _init_embedder(EMBEDDING_MODEL)

    # Detect the true embedding dimension from the live model.
    EMBEDDING_DIM = db.detect_dimension(embedder.embed, fallback=DEFAULT_EMBEDDING_DIM)
    log.info(f"Detected embedding dimension: {EMBEDDING_DIM}")

    db.init_schema(conn, EMBEDDING_DIM)
    _ensure_dimension_consistent(conn)

    adapter = build_adapter(conn)
    try:
        _migrate_legacy_device_rows()
        _rename_legacy_collection_in_text()
    except Exception as e:
        log.warning(f"[home_devices] moving {LEGACY_HOME_DEVICES} rows failed (retried at next start): {e}")
    # Load the reranker in the background: the first meaning search should not
    # wait for it, and the service must not wait for it to start.
    asyncio.get_running_loop().run_in_executor(None, _warm_reranker)

    # Rebuild the vector index ONLY when it is genuinely out of sync: either the
    # vector count no longer matches rag_items (missing/orphaned vectors) or the
    # embedding model actually changed to a DIFFERENT known model. A first run
    # (stored_model is None) does NOT force a re-embed: the legacy migration above
    # preserves the existing same-dimension vectors, so we trust them and just
    # record the model. This keeps existing data intact and startup fast; a real
    # model change is caught by _ensure_dimension_consistent (dim change -> empty
    # table -> count mismatch -> reindex) or by the model comparison below.
    try:
        total_docs = _conn().execute("SELECT COUNT(*) FROM rag_items").fetchone()[0]
        vec_count = adapter.count() if adapter else 0
        stored_model = db.get_stored_model(conn)
        model_changed = stored_model is not None and stored_model != EMBEDDING_MODEL
        if vec_count != total_docs or model_changed:
            reason = "model change" if model_changed else "vector count mismatch"
            log.info(
                f"[RAG] Reindexing vector store ({reason}: stored={stored_model!r} "
                f"current={EMBEDDING_MODEL!r}, vec={vec_count}/{total_docs})"
            )
            reindex_all()
        else:
            log.info(
                f"[RAG] Vector store in sync ({vec_count}/{total_docs} vectors, "
                f"model={EMBEDDING_MODEL!r}); no reindex needed"
            )
        db.set_stored_model(conn, EMBEDDING_MODEL)
    except Exception as e:
        log.warning(f"[RAG] startup reindex check failed (non-fatal): {e}")

    BACKEND = "sqlite-vec" if type(adapter).__name__ == "SqliteVecAdapter" else "numpy"
    log.info("RAG Service Ready.")
    try:
        _seed_protocol_lessons()
    except Exception as e:
        log.warning(f"[RAG] protocol lesson seeding failed (non-fatal): {e}")
    device_task = asyncio.create_task(_device_index_loop())
    yield
    device_task.cancel()
    with suppress(Exception):
        _flush_device_references()
    try:
        _conn().commit()
        _conn().close()
    except Exception:
        pass
    log.info("RAG Service shutting down.")


def _init_embedder(model_name: str):
    from fastembed import TextEmbedding  # pyright: ignore[reportMissingImports]

    return TextEmbedding(model_name=model_name)


def _ensure_dimension_consistent(connection):
    """If the stored dimension differs from the detected one, rebuild vec tables."""
    stored = db.get_stored_dimension(connection)
    if stored is not None and stored != EMBEDDING_DIM:
        log.warning(
            f"Stored embedding dim {stored} != detected {EMBEDDING_DIM}; "
            "rebuilding vec_rag_items (vectors will be re-created on next sync)."
        )
        connection.execute("DROP TABLE IF EXISTS vec_rag_items")
        db.create_vec_table(connection, EMBEDDING_DIM)
        connection.commit()


from services.common.logging_utils import redact_access_log  # noqa: E402

redact_access_log()
app = FastAPI(title="SharedLLM RAG Service", version="2.0.0", lifespan=lifespan)

app.include_router(info_router)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    err_msg = f"RAG Error: {type(exc).__name__}: {exc!s}"
    log.error(f"{err_msg}\n{traceback.format_exc()}")
    return JSONResponse(
        status_code=500,
        # `str(exc)` can name files and SQL, so it stays in the log above.
        content={"status": "ERROR", "message": "Internal RAG Error"}
    )


def require_internal(x_internal_secret: str = Header(...)):
    if not secret_matches(x_internal_secret, INTERNAL_SECRET):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


# Core storage helpers

#: nomic-embed-text is trained with task prefixes; without them a query and a
#: document are embedded as if they were the same kind of text.
_TASK_PREFIX = {"query": "search_query: ", "document": "search_document: "}


def _uses_nomic_prefixes() -> bool:
    return "nomic" in str(EMBEDDING_MODEL or "").lower()


def _with_task_prefix(text: str, kind: str) -> str:
    """Prefix ``text`` for the active model exactly once.

    The ingest paths and the query path must use the matching prefix or the
    vectors live in different subspaces. A caller that already prefixed its
    text is left alone so a retry cannot become ``search_query: search_query:``.
    """
    if not _uses_nomic_prefixes():
        return text
    prefix = _TASK_PREFIX.get(kind, "")
    if not prefix:
        return text
    if str(text).lstrip().startswith(prefix):
        return text
    return prefix + text


def _embed_document(text: str) -> list[float]:
    return embed([_with_task_prefix(text, "document")])[0]


def _embed_query_vector(text: str) -> list[float]:
    return embed([_with_task_prefix(text, "query")])[0]


def _embed_documents(texts: list[str]) -> list[list[float]]:
    return embed([_with_task_prefix(text, "document") for text in texts])


def _add_item(
    collection: str,
    doc_id: str,
    user_id: str,
    content: str,
    metadata: dict,
    created_at: int,
    indexed_at: str,
    vector: list[float] | None = None,
    commit: bool = True,
):
    """Insert/replace a document into rag_items + vector store + FTS5.

    Batch writers pass ``commit=False`` and commit once at the end: a commit
    per item is a disk sync per item, which kept a 700-entity HA sync on the
    event loop for 17 s even after the vector upsert stopped scanning.

    ``vector`` may be precomputed by async callers: embedding is CPU-bound
    ONNX inference, so batch endpoints run it in a worker thread
    (``asyncio.to_thread``) and pass the result here to keep the event loop
    responsive; when omitted, embedding happens synchronously (sync callers
    such as lifespan seeding and tests).
    """
    user_id = user_id.lower()
    # A replace refreshes content, metadata and the lesson columns, but the
    # counters are history. The old statement reset them to 0/NULL on every
    # re-ingest, so re-learning the same rule wiped its applied_count (which
    # the Apply: loop scores against) and each HA sync reset every entity's
    # usage_count to zero five minutes after the previous sync set it.
    existing = _conn().execute(
        "SELECT created_at, usage_count, last_used_at, applied_count"
        " FROM rag_items WHERE id = ?",
        [doc_id],
    ).fetchone()
    created_at = existing["created_at"] if existing else created_at
    usage_count = existing["usage_count"] if existing else 0
    last_used_at = existing["last_used_at"] if existing else None
    applied_count = existing["applied_count"] if existing else 0
    # Carry usage tracking into metadata for backward-visible reuse stats.
    stored_metadata = dict(metadata or {})
    stored_metadata["usage_count"] = usage_count
    _conn().execute(
        "INSERT OR REPLACE INTO rag_items"
        "(id, collection_name, user_id, content, metadata, created_at, indexed_at, "
        " usage_count, last_used_at, rule, root_cause, outcome, confidence, "
        " applied_count, supersedes) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            doc_id,
            collection,
            user_id,
            content,
            json.dumps(stored_metadata),
            created_at,
            indexed_at,
            usage_count,
            last_used_at,
            (metadata or {}).get("rule", ""),
            (metadata or {}).get("root_cause", ""),
            (metadata or {}).get("outcome", "success"),
            float((metadata or {}).get("confidence", 0.5)),
            applied_count,
            json.dumps((metadata or {}).get("supersedes", [])),
        ],
    )
    if vector is None:
        vector = _embed_document(content)
    _adapter().add(doc_id, vector, collection, user_id)

    # BM25 keyword index (best effort).
    try:
        _conn().execute("DELETE FROM rag_fts WHERE id = ?", [doc_id])
        _conn().execute(
            "INSERT INTO rag_fts(id, content, collection_name, user_id) VALUES(?, ?, ?, ?)",
            [doc_id, content, collection, user_id],
        )
    except Exception as e:  # pragma: no cover - FTS5 may be disabled
        log.debug(f"FTS5 index skip for {doc_id}: {e}")

    if commit:
        _conn().commit()


# Protocol curriculum (always-available behavioral lessons)

# Seeded once at startup (idempotent by stable id). These teach the operating
# conventions Raven must follow even when a user prompt is terse and does not
# spell them out: dedicated workspace, cheapest-sufficient tool, answer.md
# artifact, `Apply: [id]` citations, post-mission reflection, source
# verification. They are tagged `protocol` so the gateway can pin them into
# every mission prompt regardless of similarity hits.
_PROTOCOL_LESSONS: list[dict] = [
    {
        "id": "lesson-proto-workspace",
        "topic": "Raven protocol: dedicated workspace per mission",
        "rule": "Every mission runs in its own dedicated workspace. If the mission has no pre-assigned workspace, create one as your very first tool call (WorkspaceCreateRequest, id like 'raven-<project>') and pass that id as workspace_id in every subsequent file/shell/git call. Never operate in the Default Workspace.",
        "tags": ["protocol", "workspace"],
    },
    {
        "id": "lesson-proto-tool-choice",
        "topic": "Raven protocol: cheapest sufficient tool",
        "rule": "Prefer the cheapest sufficient tool for the task: a straightforward factual question only needs WebSearchRequest; do not write code, launch browsers, or invent extra steps.",
        "tags": ["protocol", "web", "search"],
    },
    {
        "id": "lesson-proto-artifact",
        "topic": "Raven protocol: answer.md artifact",
        "rule": "Save the mission deliverable as answer.md in the mission workspace root: state the answer, cite the applied lesson as `Apply: [id]`, and note which lesson you applied.",
        "tags": ["protocol", "workspace"],
    },
    {
        "id": "lesson-proto-citation",
        "topic": "Raven protocol: apply citations",
        "rule": "When you apply a stored lesson, cite it exactly as `Apply: [id]` (copy the id verbatim) in your plan so the system records it as used.",
        "tags": ["protocol", "learning"],
    },
    {
        "id": "lesson-proto-reflect",
        "topic": "Raven protocol: persist lessons after missions",
        "rule": "After each mission, persist one compact learning capturing the rule, outcome, and confidence so future missions can reuse it.",
        "tags": ["protocol", "learning"],
    },
    {
        "id": "lesson-proto-verify",
        "topic": "Raven protocol: verify facts from sources",
        "rule": "For factual claims, verify against authoritative sources (e.g. Wikipedia) with WebSearchRequest instead of answering from memory; when results conflict, prefer the authoritative source.",
        "tags": ["protocol", "web", "search"],
    },
    # --- Scenario playbooks: how to apply the workspace toolchain ---
    # These teach scenario -> tool recipes. The actual available binaries are
    # reported dynamically by the execution container (type="binary"
    # capabilities, see /rag/toolchain), so these lessons reference tool NAMES
    # only and never hardcode versions or paths.
    {
        "id": "lesson-proto-writing",
        "topic": "Raven scenario: writing documents",
        "rule": "Writing tasks: draft in Markdown in the mission workspace (WorkspaceFileWriteRequest, relative_path like 'report.md'); structure with headings, lists, and tables; python-docx is available for .docx output.",
        "tags": ["protocol", "writing", "document"],
    },
    {
        "id": "lesson-proto-programming",
        "topic": "Raven scenario: programming",
        "rule": "Programming tasks: use python3, git, and gh in the workspace shell; write code via WorkspaceFileWriteRequest, run it and its tests via WorkspaceShellRequest; commit with git. Verify code runs before reporting done.",
        "tags": ["protocol", "programming", "git"],
    },
    {
        "id": "lesson-proto-studio",
        "topic": "Workspace protocol: run, preview and make things with the studio",
        "rule": "In a workspace never guess a build or test command: the StudioProfileRequest tool names the stack (python, node-vite, node-next, go, rust, java, dotnet, static) and its exact install/test/build/lint/serve steps; StudioRunRequest {action: install|test|build|lint} runs one in the sandbox and returns the exit code and output (a test step that ran no tests is a failure). StudioPreviewRequest starts the dev server, publishes its port and returns a URL (StudioPreviewStopRequest closes it). For media use StudioImageRequest {prompt, name} or StudioNarrationRequest {text, name}, which write into assets/; for written work StudioDeckRequest {markdown with '---' between slides, title} or StudioGameRequest {title, sprites}. An existing deck or game is never overwritten unless you pass replace: true.",
        "tags": ["protocol", "workspace", "studio", "coding", "media"],
    },
    {
        "id": "lesson-proto-studio-verify",
        "topic": "Workspace protocol: prove the workspace works, and edit 3D models",
        "rule": "Before blaming your own code for a workspace failure call the StudioDiagnoseRequest tool: it runs safe probes (identity, HOME writability, the working directory, /bin/bash, python3, git) and returns each one's output plus a hint naming the cause. StudioVerifyRequest goes further: it writes a one-line program, runs it, deletes it, then runs the project's test step, and returns one verdict -- so 'it works' is proven, not assumed. A broken shell stops the check there, and a run with no tests is not a pass.",
        "tags": ["protocol", "workspace", "studio", "shell", "testing"],
    },
    {
        "id": "lesson-proto-studio-3d",
        "topic": "Workspace protocol: reading and editing STL models",
        "rule": "STL files are read and edited in the gateway, not by guessing: the StlInspectRequest tool {path} returns triangles, size, volume, area, whether the mesh is watertight and every warning (open surface, inside-out winding, no-area facets, unit guess). StlTransformRequest {path, scale (number or [x,y,z]), rotate [x,y,z] degrees, translate [x,y,z], fit (longest side), center, format binary|ascii, out_path} writes a NEW file (default the first free <name>_edited.stl; an existing out_path needs replace: true) and returns the measurements before and after -- quote both. Paths are relative to the workspace and must end in .stl. STL is unitless: say which unit you assumed (mm unless the model is metres across).",
        "tags": ["protocol", "workspace", "studio", "3d", "stl"],
    },
    {
        "id": "lesson-proto-typesetting",
        "topic": "Raven scenario: typesetting",
        "rule": "Typesetting: convert Markdown to PDF/HTML/DOCX with pandoc (e.g. `pandoc report.md -o report.pdf --pdf-engine=pdflatex`); pdflatex is available for LaTeX/math-heavy documents.",
        "tags": ["protocol", "typesetting", "document"],
    },
    {
        "id": "lesson-proto-publishing",
        "topic": "Raven scenario: publishing artifacts",
        "rule": "Publishing: produce the final artifact (PDF/HTML/Markdown) in the workspace root and verify it exists and is valid (`ls -la`, `file out.pdf`) before finishing; name deliverables clearly and mention them in the final answer.",
        "tags": ["protocol", "publishing", "document"],
    },
    {
        "id": "lesson-proto-image-editing",
        "topic": "Raven scenario: image editing",
        "rule": "Image editing: use ImageMagick (`convert`/`magick`) for resize/crop/annotate/format conversion, Pillow (python3) for scripted edits, and ffmpeg for video/audio; verify outputs with `identify`/`ffprobe`.",
        "tags": ["protocol", "image-editing", "media"],
    },
    {
        "id": "lesson-proto-graphics",
        "topic": "Raven scenario: graphics flyers with image generation",
        "rule": "Graphics flyers: to create a true image flyer (not a typeset PDF), call the imagegenerationrequest tool backed by the local alpaca sd-server (POST /v1/images/generations). Prompt it for a vertical poster with a large headline, key bullet points and bright colors about the topic; ask for a PNG. Persist the returned image into the workspace with WorkspaceFileWriteRequest (relative_path like flyer.png, content_base64 = the returned b64) or via the workspace files endpoint, then verify it exists and renders (`ls -la`, `file flyer.png`). Pandoc/LaTeX PDFs and ImageMagick/Pillow compositions exist as alternatives, but when asked for a graphics/image flyer generate an actual image. Keep the prompt concise; include text you want on the flyer in the prompt itself.",
        "tags": ["protocol", "graphics", "image", "media", "publishing"],
    },
    {
        "id": "lesson-proto-resources",
        "topic": "Raven protocol: pulling resources in",
        "rule": "Before starting, check the resources blocks in the prompt. Bring in what you need instead of inventing it: Nextcloud files via StorageFileReadRequest then WorkspaceFileWriteRequest to import into the workspace; home hub entity state via entity requests; previously indexed knowledge via the context blocks. Resources you import may then be processed with the workspace toolchain.",
        "tags": ["protocol", "resources", "nextcloud", "ha"],
    },
    {
        "id": "lesson-proto-git",
        "topic": "Raven scenario: git operations",
        "rule": "Git tasks: clone repositories with `git clone URL` in the workspace shell; for existing repos use `git pull`, `git add`, `git commit -m`, `git push`; for GitHub operations use `gh` (issues, PRs, releases). Verify state with `git status`, `git log --oneline -5`. The workspace README and commit history are always accessible via WorkspaceFileReadRequest and WorkspaceShellRequest.",
        "tags": ["protocol", "git", "programming"],
    },
    {
        "id": "lesson-proto-media",
        "topic": "Raven scenario: media and audio creation",
        "rule": "Media creation: to generate speech use TTSRequest. It converts text to speech locally (Kokoro) and SAVES the audio to the workspace automatically when you include a relative_path (e.g. 'narration.wav') AND workspace_id; it returns the saved path. You can pass raw text, a workspace text file via text_file, OR a workspace PDF/EPUB/DOCX (its text layer is extracted automatically). The engine reads Bible verses (John 3:16 -> 'John chapter 3, verse 16'), years, and small numbers naturally and accepts SSMD markup for pause control (e.g. '...p' paragraph pause, '...500ms' custom). Do NOT pip-install or script external TTS libraries (gTTS/espeak); TTSRequest is the intended tool. StorageTextToAudioRequest converts a Nextcloud text file to audio; ffmpeg is available for audio format conversion and trimming. For image/media generation, use the text-to-image tool if available. Always mention what format the output is in.",
        "tags": ["protocol", "media", "audio", "tts"],
    },
    {
        "id": "lesson-proto-report",
        "topic": "Raven scenario: research reports",
        "rule": "Research reports: gather facts via WebSearchRequest from multiple sources (2-3 searches for balance), synthesize into a structured Markdown report with headings, citations to sources, and a summary; save as report.md (or .pdf with pandoc) in the workspace root. Verify the artifact exists before finishing.",
        "tags": ["protocol", "report", "research", "web"],
    },
    {
        "id": "lesson-proto-orchestrate",
        "topic": "Raven scenario: multi-tool orchestration",
        "rule": "Multi-source tasks that cross domains: query the home hub for current state (temperature, device status), fetch related web information with WebSearchRequest, then synthesize everything into a combined workspace artifact (status report, dashboard JSON, etc.). Pull resources from Nextcloud as needed. Each domain feeds into the next, so show the logical chain in your plan and Apply: citations.",
        "tags": ["protocol", "orchestration", "ha", "web", "resources"],
    },
    {
        "id": "lesson-proto-library",
        "topic": "Raven protocol: answer book questions from the Calibre library",
        "rule": "For questions about books, authors, sermons, commentaries, or theology, search the household's own library FIRST: ContextSearchRequest with collection_name 'calibre_files' returns matching passages (cite them as `Source: Title by Author, chapter`), and CalibreRequest (actions: list, search, get_book, fetch_text, tags, by_tag, audit, tag) finds a book, reads its text, shows what the shelf holds on a topic, audits it for copies and gaps, and (for admins) tags it. Only fall back to WebSearchRequest when the library plainly has nothing on the subject; a web answer about a book this shelf holds is a wrong-source answer.",
        "tags": ["protocol", "library", "calibre", "research"],
    },
    {
        "id": "lesson-proto-scripture",
        "topic": "Raven protocol: answer scripture questions from the Bible service",
        "rule": "Scripture quotes and passages come from BibleRequest FIRST: action 'read' with a ref (and version when the asker names one) returns the real verse text. Cite it as `{reference} ({version})`. For commentary or study-Bible notes use action 'study_notes' (add edition for a named study Bible); for 'what does the Bible say about X' use 'search' then read the best passage; use 'catalogue' to discover installed translations and editions. Never quote scripture from WebSearchRequest or ContextSearchRequest; a paraphrase from the web is a wrong-source answer when the corpus holds the verse.",
        "tags": ["protocol", "scripture", "bible", "quote"],
    },
    {
        "id": "lesson-proto-rag-collections",
        "topic": "Raven protocol: all RAG collections and topic tags",
        "rule": "ContextSearchRequest requires collection_name: calibre_files (books), nextcloud_files (cloud docs, notes, recipes), home_devices (the home's devices from the home hub), system_learnings (past lessons), system_capabilities (tools and CLI binaries), user_facts (remembered user facts and preferences), conversation_memory (intercom voice transcripts), mission_history (past Raven mission outcomes), network_topology (Docker/LAN containers), telemetry_alerts (device and sensor alerts). Pass tags: [topic] to filter Calibre books tagged jarvis:<topic> or Nextcloud files under Resources/<Topic>/.",
        "tags": ["protocol", "rag", "search", "resources"],
    },
    {
        "id": "lesson-proto-audiobooks",
        "topic": "Raven protocol: Audiobookshelf audiobooks and podcasts",
        "rule": "For audiobooks, spoken-word libraries, listening progress, or podcast episodes in Audiobookshelf, use AudiobookshelfRequest (actions: libraries, list, search, get_book, progress, last_played, play, play_podcast_episode, resume, update_progress) instead of guessing titles or searching the web.",
        "tags": ["protocol", "audiobookshelf", "media", "audio"],
    },
    {
        "id": "lesson-proto-home-data",
        "topic": "Raven protocol: household data",
        "rule": "For household schedules and notes use CalendarRequest (action=read for events, list for calendars), NoteRequest, ChoreRequest, and MealPlanRequest. Read the real data rather than inventing schedules.",
        "tags": ["protocol", "home", "personal-data"],
    },
    {
        "id": "lesson-proto-home-hub",
        "topic": "Raven protocol: home hub inspection",
        "rule": "For live home device state and history use EntitySearchRequest, HAConfigRequest, and LogbookRequest, and the hub's lists through TodoListRequest. Read the real state rather than inventing it. (Only while a home hub is set up; without one these tools are not offered.)",
        "tags": ["protocol", "ha", "home"],
    },
    {
        "id": "lesson-proto-observability",
        "topic": "Raven protocol: logs, diagnostics, network, and model telemetry",
        "rule": "For SharedLLM diagnostics use ExecutionLogRequest, DockerLogsRequest, RedisInspectRequest (never shell redis-cli), LLMInfoRequest, AiCapabilitiesRequest (check image/voice support before promising), and NetworkDeviceScanRequest; search mission_history and telemetry_alerts via ContextSearchRequest for past failures or alerts.",
        "tags": ["protocol", "diagnostics", "logs", "network"],
    },
]


def _seed_protocol_lessons(user_id: str = "default") -> int:
    """Idempotently ensure the protocol curriculum exists for the user.

    Skips any lesson whose stable id is already stored, so applied/usage
    counts and user edits are never overwritten. Returns how many lessons
    were newly created.
    """
    created = 0
    now = int(time.time())
    indexed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for lesson in _PROTOCOL_LESSONS:
        if _item_exists(lesson["id"]):
            continue
        _add_item(
            collection="system_learnings",
            doc_id=lesson["id"],
            user_id=user_id,
            content=json.dumps(
                {
                    "id": lesson["id"],
                    "topic": lesson["topic"],
                    "rule": lesson["rule"],
                    "root_cause": "",
                    "outcome": "success",
                    "confidence": 0.9,
                    "tags": lesson["tags"],
                    "summary": lesson["rule"],
                },
                ensure_ascii=False,
            ),
            metadata={
                "id": lesson["id"],
                "topic": lesson["topic"],
                "rule": lesson["rule"],
                "root_cause": "",
                "outcome": "success",
                "confidence": 0.9,
                "tags": lesson["tags"],
                "type": "learning",
                "supersedes": [],
            },
            created_at=now,
            indexed_at=indexed_at,
        )
        created += 1
    if created:
        log.info(f"[RAG] Seeded {created} protocol lessons for user {user_id}")
    return created


def reindex_all(batch_size: int = 256) -> int:
    """Re-embed every document in ``rag_items`` with the CURRENT model and rebuild
    the vector store.

    Stale vectors (indexed with a previous embedding model) silently break
    semantic search: the KNN returns 0 hits even though documents exist. This
    guarantees the index always matches the active model. Embedding runs in
    bounded batches so peak memory stays flat even for thousands of documents
    (a single giant batch once peaked at >11 GiB for ~3.5k docs and OOM-killed
    the host), while still warming the embedder for fast live search.
    """
    rows = _conn().execute(
        "SELECT id, content, collection_name, user_id FROM rag_items"
    ).fetchall()
    if not rows:
        return 0
    for start in range(0, len(rows), max(1, batch_size)):
        chunk = rows[start : start + batch_size]
        vectors = _embed_documents([r["content"] or "" for r in chunk])
        for r, vec in zip(chunk, vectors, strict=False):
            _adapter().add(r["id"], vec, r["collection_name"], r["user_id"])
    _conn().commit()
    log.info(f"[RAG] reindexed {len(rows)} documents into the vector store")
    return len(rows)


def _delete_items(ids: list[str]):
    if not ids:
        return
    placeholders = ",".join("?" * len(ids))
    _conn().execute(f"DELETE FROM rag_items WHERE id IN ({placeholders})", ids)
    _conn().execute(f"DELETE FROM rag_fts WHERE id IN ({placeholders})", ids)
    _conn().execute(f"DELETE FROM vec_store WHERE id IN ({placeholders})", ids)
    # Clean the sqlite-vec virtual table too (numpy fallback uses vec_store above).
    with suppress(Exception):
        _conn().execute(f"DELETE FROM vec_rag_items WHERE id IN ({placeholders})", ids)
    for doc_id in ids:
        with suppress(Exception):
            _adapter().delete(doc_id)
    _conn().commit()


#: An alphanumeric token of at least two characters. Splitting on everything
#: else (quotes, hyphens, colons, punctuation) is deliberate: those are FTS5
#: syntax, not search text, and were what made a natural-language query match
#: zero rows or raise OperationalError.
_FTS_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def _fts_tokens(query: str) -> list[str]:
    tokens = [token.lower() for token in _FTS_TOKEN_RE.findall(str(query or ""))]
    return [token for token in tokens if len(token) >= 2]


def _bm25_query(query: str) -> str:
    """Build a safe FTS5 MATCH expression from natural language.

    Every token is double-quoted, so FTS5 reserved words (AND, OR, NOT, NEAR)
    and punctuation can never be parsed as syntax. Tokens are OR-ed so a
    natural-language question matches any of its meaningful words instead of
    requiring one contiguous phrase. For short queries the contiguous phrase is
    added as an extra alternative; BM25's rank then rewards the phrase match.
    """
    tokens = list(dict.fromkeys(_fts_tokens(query)))
    if not tokens:
        return ""
    quoted = [f'"{token}"' for token in tokens]
    if len(tokens) <= 5:
        phrase = '"' + " ".join(tokens) + '"'
        return f'({" OR ".join(quoted)}) OR {phrase}'
    return " OR ".join(quoted)


def _bm25_phrase_query(query: str) -> str:
    tokens = _fts_tokens(query)
    if not tokens or len(tokens) > 5:
        return ""
    return '"' + " ".join(tokens) + '"'


def _bm25_search(collection: str, user_id: str, query: str, k: int) -> list[tuple[str, float]]:
    """Return ``(doc_id, rank)`` ordered best-first, or [] if FTS5 unavailable."""
    safe = _bm25_query(query)
    if not safe:
        return []
    try:
        rows = _conn().execute(
            "SELECT id, bm25(rag_fts) AS rank FROM rag_fts "
            "WHERE rag_fts MATCH ? AND collection_name = ? "
            "AND (user_id = ? OR user_id = 'default') "
            "ORDER BY rank LIMIT ?",
            [safe, collection, user_id, k],
        ).fetchall()
        return [(r["id"], float(r["rank"])) for r in rows]
    except Exception as e:  # pragma: no cover - FTS5 may be disabled
        log.debug(f"BM25 search unavailable: {e}")
        return []


def _bm25_phrase_hits(collection: str, user_id: str, query: str, k: int) -> set[str]:
    """Docs the query matches as one contiguous phrase.

    Tokenized OR matches are ranked evidence, not proof the query is about the
    document; only a phrase hit is strong enough to survive ``min_similarity``.
    """
    safe = _bm25_phrase_query(query)
    if not safe:
        return set()
    try:
        rows = _conn().execute(
            "SELECT id FROM rag_fts "
            "WHERE rag_fts MATCH ? AND collection_name = ? "
            "AND (user_id = ? OR user_id = 'default') "
            "ORDER BY bm25(rag_fts) LIMIT ?",
            [safe, collection, user_id, k],
        ).fetchall()
        return {r["id"] for r in rows}
    except Exception as e:  # pragma: no cover - FTS5 may be disabled
        log.debug(f"BM25 phrase search unavailable: {e}")
        return set()


def _hybrid_search(
    collection: str,
    user_id: str,
    query: str,
    query_vector: list[float],
    k: int,
    alpha: float,
    use_rrf: bool,
    tags: list[str] | None = None,
    min_similarity: float | None = None,
) -> list[SearchResultItem]:
    user_id = user_id.lower()
    collection = canonical_collection(collection) or collection

    # The indexes narrow only by collection and user, so a topic filter takes
    # a wider pool of candidates and keeps the ones about that topic.
    pool = k * (8 if tags else 2)
    vec_res = _adapter().search(collection, user_id, query_vector, pool)
    bm25_res = _bm25_search(collection, user_id, query, pool)

    K_RRF = 60
    vec_scores: dict[str, float] = {}
    for i, (doc_id, _dist) in enumerate(vec_res):
        vec_scores[doc_id] = vec_scores.get(doc_id, 0.0) + 1.0 / (K_RRF + i + 1)
    # How close each candidate really is: RRF ranks only, and the vector index
    # returns its nearest rows however unrelated they are.
    similarities = _adapter().cosine_similarities(
        query_vector, list(dict.fromkeys([d for d, _ in vec_res] + [d for d, _ in bm25_res]))
    )
    bm25_scores: dict[str, float] = {}
    for i, (doc_id, _rank) in enumerate(bm25_res):
        bm25_scores[doc_id] = bm25_scores.get(doc_id, 0.0) + 1.0 / (K_RRF + i + 1)

    if not use_rrf and (vec_res or bm25_res):
        # Blend by alpha (dense weight) / (1 - alpha) (BM25 weight).
        fused: dict[str, float] = {}
        for doc_id, s in vec_scores.items():
            fused[doc_id] = fused.get(doc_id, 0.0) + alpha * s
        for doc_id, s in bm25_scores.items():
            fused[doc_id] = fused.get(doc_id, 0.0) + (1.0 - alpha) * s
    else:
        fused = {}
        for doc_id in set(vec_scores) | set(bm25_scores):
            fused[doc_id] = vec_scores.get(doc_id, 0.0) + bm25_scores.get(doc_id, 0.0)

    ranked = sorted(fused.items(), key=lambda x: x[1], reverse=True)
    # Only a contiguous phrase match is evidence enough to bypass the floor;
    # a single shared OR-token is not.
    phrase_hits = _bm25_phrase_hits(collection, user_id, query, pool)
    if min_similarity is not None:
        ranked = [
            (doc_id, score) for doc_id, score in ranked
            if doc_id in phrase_hits or similarities.get(doc_id, -1.0) >= min_similarity
        ]
    if not tags:
        ranked = ranked[:k]

    results: list[SearchResultItem] = []
    kept: list[tuple[str, float]] = []
    for doc_id, score in ranked:
        if len(results) >= k:
            break
        row = _conn().execute(
            "SELECT content, metadata, usage_count, last_used_at FROM rag_items WHERE id = ?",
            [doc_id],
        ).fetchone()
        if row:
            meta = json.loads(row["metadata"])
            if not topics.matches(meta, tags):
                continue
            kept.append((doc_id, score))
            # Surface usage tracking in the result metadata so callers (and the
            # UI) can see how often a lesson has been reused.
            meta["usage_count"] = row["usage_count"] or 0
            meta["last_used_at"] = row["last_used_at"]
            # An all-collections search merges hits; say where each came from.
            meta.setdefault("collection_name", collection)
            results.append(
                SearchResultItem(
                    content=row["content"],
                    metadata=meta,
                    score=score,
                    similarity=round(similarities[doc_id], 4) if doc_id in similarities else None,
                    phrase_match=doc_id in phrase_hits,
                )
            )
    # Honest reuse accounting:
    #  - usage_count  = times RETRIEVED (seen by a mission). Bumped here.
    #  - applied_count = times ACTUALLY APPLIED (model cited the lesson id).
    #    Bumped separately via /rag/learning/{id}/applied so the ♻-style
    #    metric reflects real usefulness, not just search frequency.
    if collection == "system_learnings" and results:
        _bump_retrieved([doc_id for doc_id, _ in kept if _item_exists(doc_id)])
    elif collection == HOME_DEVICES and kept:
        # A device that searches keep finding stays in the index (see _sweep_stale_devices).
        _note_device_references([doc_id for doc_id, _ in kept])
    return results


def _item_exists(doc_id: str) -> bool:
    row = _conn().execute("SELECT 1 FROM rag_items WHERE id = ?", [doc_id]).fetchone()
    return row is not None


def _bump_retrieved(doc_ids: list[str]) -> None:
    """Increment ``usage_count``: the lesson was retrieved/seen by a mission."""
    _bump_columns(doc_ids, "usage_count = usage_count + 1")


def _bump_applied(doc_ids: list[str]) -> None:
    """Increment ``applied_count``: the lesson was genuinely applied by a mission.

    Separated from retrieval so we can tell "frequently searched" from
    "frequently useful". Called only when the model cites a lesson id.
    """
    _bump_columns(doc_ids, "applied_count = applied_count + 1")


def _bump_columns(doc_ids: list[str], set_clause: str) -> None:
    """Atomically increment one or more counter columns for the given doc ids."""
    if not doc_ids:
        return
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        _conn().execute("BEGIN")
        for doc_id in doc_ids:
            _conn().execute(
                f"UPDATE rag_items SET {set_clause}, last_used_at = ? WHERE id = ?",
                [now, doc_id],
            )
        _conn().commit()
    except Exception as e:  # pragma: no cover - best effort counters
        log.debug(f"Usage bump failed: {e}")
        with suppress(Exception):
            _conn().rollback()


def _search_all_collections(
    user_id: str, query: str, query_vector: list[float], k: int, alpha: float, use_rrf: bool,
    tags: list[str] | None = None, min_similarity: float | None = None,
) -> list[SearchResultItem]:
    """Search every collection the user (or the shared ``default`` user) owns.

    The caller computes the query embedding once (off the event loop) and it is
    reused across collections. Per-collection hybrid results are merged,
    de-duplicated by content, and re-ranked globally by fused score so a global
    "search everything" query returns the best hits regardless of which
    collection they live in.
    """
    user_id = user_id.lower()

    rows = _conn().execute(
        "SELECT DISTINCT collection_name FROM rag_items "
        "WHERE user_id = ? OR user_id = 'default'",
        [user_id],
    ).fetchall()
    collections = [r["collection_name"] for r in rows]

    merged: list[SearchResultItem] = []
    seen_contents: set[str] = set()
    for coll in collections:
        try:
            res = _hybrid_search(coll, user_id, query, query_vector, k, alpha, use_rrf, tags, min_similarity)
        except Exception as e:  # pragma: no cover - isolated per collection
            log.debug(f"Search failed for collection {coll}: {e}")
            res = []
        for item in res:
            if item.content in seen_contents:
                continue
            seen_contents.add(item.content)
            merged.append(item)

    merged.sort(key=lambda x: (x.score or 0.0), reverse=True)
    return merged[:k]


# Search / Ingest endpoints (legacy contract preserved)

def _cross_encoder_scores(query: str, texts: list[str]) -> list[float]:
    """Score ``texts`` against ``query`` with the loaded cross-encoder."""
    model = _get_reranker()
    return [float(score) for score in model.rerank(query, texts, batch_size=16)]


def _blend_rerank_scores(
    results: list[SearchResultItem], scores: list[float]
) -> list[SearchResultItem]:
    """Order by cross-encoder score, keeping the fused score as a tie-breaker.

    The raw cross-encoder logits have no fixed scale, so they are min-max
    normalized before blending. The small fused-weight term only separates
    near-ties; the cross-encoder remains the deciding signal.
    """
    if len(scores) != len(results):
        return results
    high, low = max(scores), min(scores)
    span = high - low or 1.0
    base_high = max((item.score or 0.0) for item in results) or 1.0
    blended = [
        (0.75 * ((score - low) / span)) + (0.25 * ((item.score or 0.0) / base_high))
        for item, score in zip(results, scores, strict=False)
    ]
    order = sorted(range(len(results)), key=lambda index: blended[index], reverse=True)
    return [results[index] for index in order]


@app.post("/rag/search", response_model=SearchResponse, dependencies=[Depends(require_internal)])
async def search(req: SearchRequest):
    # `collection_name` of "all"/""/None searches across every collection the
    # user (or shared `default`) owns, instead of a single hardcoded one.
    log.info(f"[RAG] search query_len={len(req.query)} coll={req.collection_name} k={req.k}")
    wants_rerank = bool(req.rerank and reranker is not None)
    # Cross-encoder scores are only comparable within the candidate set, so an
    # over-fetch is required: the best final hit may not be in the dense/BM25
    # top-k. The floor keeps a tiny k from starving the reranker.
    fetch_k = max(req.k * 2, 10) if wants_rerank else req.k
    try:
        query_vector = await asyncio.to_thread(_embed_query_vector, req.query)
        # Only ONNX inference moves off the loop; the SQLite connection is
        # thread-affine (check_same_thread=True) and stays on the request thread.
        if req.collection_name in (None, "", "all", "__all__"):
            results = _search_all_collections(
                req.user_id, req.query, query_vector, fetch_k, req.alpha, req.use_rrf,
                req.tags, req.min_similarity,
            )
        else:
            results = _hybrid_search(
                req.collection_name, req.user_id, req.query, query_vector,
                fetch_k, req.alpha, req.use_rrf, req.tags, req.min_similarity,
            )
        if wants_rerank and len(results) > 1:
            texts = [(item.content or "")[:RERANK_MAX_CHARS] for item in results]
            try:
                scores = await asyncio.to_thread(_cross_encoder_scores, req.query, texts)
                results = _blend_rerank_scores(results, scores)
            except Exception as exc:
                # A reranker failure must never fail the search: the hybrid
                # ranking is already valid, just less precise.
                log.warning(f"[RAG] rerank skipped: {type(exc).__name__}: {exc}")
        return SearchResponse(results=results[: req.k])
    except Exception as e:
        # A failed search is not "nothing matched": answering [] here let every caller
        # tell the person their files hold nothing on the subject while the index was down.
        log.error(f"Hybrid search failed: {e}")
        raise HTTPException(status_code=503, detail=f"Search failed: {type(e).__name__}: {e}") from e


#: Limits for /rag/embed: enough for a batch of verses, small enough that one
#: request cannot occupy the embedder for minutes.
EMBED_ENDPOINT_MAX_TEXTS = 128
EMBED_ENDPOINT_MAX_CHARS = 2_000
class EmbedRequest(BaseModel):
    texts: list[str]
    kind: Literal["query", "document"] = "document"


@app.post("/rag/embed", dependencies=[Depends(require_internal)])
async def embed_texts(req: EmbedRequest):
    """Vectors for other services (the Bible's meaning search) from the model
    already loaded here, so no second copy of it is needed anywhere.

    Returns the model name and dimension with the vectors, so a caller can tell
    when stored vectors came from a different model and must be redone.
    """
    if not req.texts:
        raise HTTPException(status_code=400, detail="texts is empty")
    if len(req.texts) > EMBED_ENDPOINT_MAX_TEXTS:
        raise HTTPException(
            status_code=400, detail=f"at most {EMBED_ENDPOINT_MAX_TEXTS} texts per request"
        )
    if any(len(t) > EMBED_ENDPOINT_MAX_CHARS for t in req.texts):
        raise HTTPException(
            status_code=400, detail=f"each text must be at most {EMBED_ENDPOINT_MAX_CHARS} characters"
        )
    if embedder is None:
        raise HTTPException(status_code=503, detail="The embedding model is still loading")
    # embed_batched, never embed(): its cost bound is what keeps a large
    # request from pushing the service out of memory (see embed_batched).
    vectors = await asyncio.to_thread(embed_batched, [_with_task_prefix(text, req.kind) for text in req.texts])
    return {"model": EMBEDDING_MODEL, "dim": len(vectors[0]) if vectors else EMBEDDING_DIM, "vectors": vectors}


def _get_reranker():
    """The cross-encoder, loaded once (on first use, or by the warm-up at start)."""
    global reranker
    if reranker is None:
        with _reranker_lock:
            if reranker is None:
                from fastembed.rerank.cross_encoder import TextCrossEncoder

                reranker = TextCrossEncoder(model_name=RERANK_MODEL)
    return reranker


def _warm_reranker() -> None:
    try:
        _get_reranker()
        log.info("Reranker ready: %s", RERANK_MODEL)
    except Exception as exc:
        log.warning("Reranker could not be loaded at start (%s); it is retried on first use", exc)


class RerankRequest(BaseModel):
    query: str
    texts: list[str]


@app.post("/rag/rerank", dependencies=[Depends(require_internal)])
async def rerank_texts(req: RerankRequest):
    """How well each text answers ``query`` (higher is better), in the texts' order.

    The scores are the cross-encoder's raw logits: comparable within one call,
    not across questions.
    """
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query is empty")
    if not req.texts:
        raise HTTPException(status_code=400, detail="texts is empty")
    if len(req.texts) > RERANK_MAX_TEXTS:
        raise HTTPException(status_code=400, detail=f"at most {RERANK_MAX_TEXTS} texts per request")
    if len(req.query) > RERANK_MAX_CHARS or any(len(t) > RERANK_MAX_CHARS for t in req.texts):
        raise HTTPException(status_code=400, detail=f"each text must be at most {RERANK_MAX_CHARS} characters")

    def score() -> list[float]:
        return [float(x) for x in _get_reranker().rerank(req.query, req.texts, batch_size=16)]

    try:
        scores = await asyncio.to_thread(score)
    except Exception as exc:
        log.warning("Rerank failed: %s", exc)
        raise HTTPException(status_code=503, detail="The reranking model is not available") from exc
    return {"model": RERANK_MODEL, "scores": scores}


@app.post("/rag/ingest", dependencies=[Depends(require_internal)])
async def ingest(req: IngestRequest):
    import uuid

    meta = dict(req.metadata)
    meta["user_id"] = req.user_id.lower()
    doc_id = meta.get("id") or str(uuid.uuid4())
    meta["indexed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    now = int(time.time())
    try:
        _add_item(req.collection_name, doc_id, req.user_id, req.content, meta, now, meta["indexed_at"])
        return {"status": "SUCCESS", "id": doc_id}
    except Exception as e:
        log.error(f"Ingest failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to ingest document") from e


@app.get("/rag/stats", dependencies=[Depends(require_internal)])
async def get_stats(user_id: str = "default"):
    """Return counts and metadata for collections in the format expected by the UI."""
    user_id = user_id.lower()
    log.info(f"Fetching stats for user_id: {user_id}")
    try:
        # calibre_files is crawled from the Knowledge Hub like nextcloud_files,
        # so it belongs in the same counts.
        collections = ["nextcloud_files", "calibre_files", HOME_DEVICES, "system_capabilities", "system_learnings"]
        total_chunks = 0
        coll_chunks_map: dict[str, int] = {}
        coll_docs_map: dict[str, int] = {}
        total_documents = 0
        providers: list[str] = []
        last_indexed = None

        for name in collections:
            # Capabilities and Raven lessons are system-wide (stored under
            # "default"), as /rag/learning lists them, and so is the family's
            # Calibre library (storage indexes it under SHARED_LIBRARY_OWNER).
            target_user = "default" if name in ("system_capabilities", "system_learnings", "calibre_files") else user_id
            rows = _conn().execute(
                "SELECT metadata FROM rag_items WHERE collection_name = ? AND user_id = ?",
                [name, target_user],
            ).fetchall()
            if rows:
                total_chunks += len(rows)
                coll_chunks_map[name] = len(rows)
                providers.append(name.split("_")[0])
                unique_items = set()
                for r in rows:
                    m = json.loads(r["metadata"])
                    item_id = m.get("path") or m.get("friendly_name") or m.get("entity_id")
                    if item_id:
                        unique_items.add(item_id)
                    idx_at = m.get("indexed_at")
                    if idx_at and (last_indexed is None or idx_at > last_indexed):
                        last_indexed = idx_at
                doc_count = len(unique_items) or 1
                total_documents += doc_count
                coll_docs_map[name] = doc_count

        return {
            "status": "SUCCESS",
            "total_chunks": total_chunks,
            "total_documents": total_documents,
            "last_indexed": last_indexed,
            "providers": list(set(providers)),
            "breakdown": {
                name: {
                    "chunks": coll_chunks_map.get(name, 0),
                    "documents": coll_docs_map.get(name, 0),
                }
                for name in collections
            },
        }
    except Exception as e:
        log.error(f"Stats failed: {e}")
        return JSONResponse(status_code=500, content={"status": "ERROR", "message": str(e)})


@app.get("/rag/collection/{collection_name}", dependencies=[Depends(require_internal)])
async def list_collection_documents(
    collection_name: str, user_id: str = "default", limit: int = Query(100, ge=1, le=500)
):
    collection_name = canonical_collection(collection_name) or collection_name
    try:
        target_user = "default" if collection_name == "system_capabilities" else user_id
        rows = _conn().execute(
            "SELECT id, content, metadata FROM rag_items "
            "WHERE collection_name = ? AND user_id = ? LIMIT ?",
            [collection_name, target_user, limit],
        ).fetchall()
        items = [
            {"id": r["id"], "document": r["content"], "metadata": json.loads(r["metadata"])}
            for r in rows
        ]
        return {
            "status": "SUCCESS",
            "collection": collection_name,
            "user_id": target_user,
            "count": len(items),
            "items": items,
        }
    except Exception as e:
        log.error(f"Failed to list collection {collection_name}: {e}")
        # A 200 with {"status": "ERROR"} is not an error to any HTTP client.
        raise HTTPException(status_code=500, detail="Could not list that collection") from e


@app.get("/rag/learning/protocol", dependencies=[Depends(require_internal)])
async def list_protocol_lessons():
    """Every seeded protocol lesson, in curriculum order, with no limit.

    The gateway pins these into every Raven prompt. It used to ask
    ``/rag/learning?tag=protocol&limit=25`` (newest first): with more than 25
    tagged lessons the oldest, most basic ones fell out of every prompt.
    Asking by the seeded ids returns exactly the curriculum, whatever else is
    tagged ``protocol``. A seeded lesson missing from the store is a 500.
    """
    ids = [lesson["id"] for lesson in _PROTOCOL_LESSONS]
    placeholders = ",".join("?" for _ in ids)
    rows = _conn().execute(
        f"SELECT id, content, metadata, created_at, usage_count, applied_count "
        f"FROM rag_items WHERE collection_name = ? AND user_id = ? AND id IN ({placeholders})",
        ["system_learnings", "default", *ids],
    ).fetchall()
    by_id = {r["id"]: r for r in rows}
    missing = [i for i in ids if i not in by_id]
    if missing:
        log.error(f"[RAG] Seeded protocol lessons missing from the store: {missing}")
        raise HTTPException(status_code=500, detail=f"Protocol lessons missing: {', '.join(missing)}")
    items = [
        {
            "id": i,
            "content": by_id[i]["content"],
            "metadata": json.loads(by_id[i]["metadata"]),
            "created_at": by_id[i]["created_at"],
            "usage_count": by_id[i]["usage_count"] or 0,
            "applied_count": by_id[i]["applied_count"] or 0,
        }
        for i in ids
    ]
    return {"status": "SUCCESS", "collection": "system_learnings", "count": len(items), "items": items}


@app.get("/rag/learning", dependencies=[Depends(require_internal)])
async def list_learnings(user_id: str = "default", limit: int = 200, sort: str = "recent", tag: str | None = None):
    """List Raven lessons (system_learnings) with reuse stats.

    ``sort`` may be ``recent`` (newest first) or ``reuse`` (most-reused first).
    ``tag`` filters to lessons whose ``metadata.tags`` includes the tag (e.g.
    ``protocol``), which is how the gateway pins the always-on curriculum.
    """
    try:
        target_user = "default"
        order = "usage_count DESC, created_at DESC" if sort == "reuse" else "created_at DESC"
        where = "collection_name = ? AND user_id = ?"
        params: list = ["system_learnings", target_user]
        if tag:
            where += " AND metadata LIKE ?"
            params.append(f'%"{tag}"%')
        rows = _conn().execute(
            f"SELECT id, content, metadata, created_at, usage_count, last_used_at, "
            f"rule, root_cause, outcome, confidence, applied_count, supersedes "
            f"FROM rag_items WHERE {where} "
            f"ORDER BY {order} LIMIT ?",
            [*params, limit],
        ).fetchall()
        items = [
            {
                "id": r["id"],
                "content": r["content"],
                "metadata": json.loads(r["metadata"]),
                "created_at": r["created_at"],
                "usage_count": r["usage_count"] or 0,
                "last_used_at": r["last_used_at"],
                "rule": r["rule"] or "",
                "root_cause": r["root_cause"] or "",
                "outcome": r["outcome"] or "success",
                "confidence": float(r["confidence"]) if r["confidence"] is not None else 0.5,
                "applied_count": r["applied_count"] or 0,
                "supersedes": json.loads(r["supersedes"]) if r["supersedes"] else [],
            }
            for r in rows
        ]
        return {
            "status": "SUCCESS",
            "collection": "system_learnings",
            "user_id": target_user,
            "count": len(items),
            "items": items,
        }
    except Exception as e:
        log.error(f"Failed to list learnings: {e}")
        return JSONResponse(status_code=500, content={"status": "ERROR", "message": str(e)})


@app.patch("/rag/learning/{doc_id}", dependencies=[Depends(require_internal)])
async def edit_learning(doc_id: str, payload: dict):
    """Edit a Raven lesson's content and/or metadata."""
    try:
        # Lessons only: this id lookup used to edit any row in any collection.
        row = _conn().execute(
            "SELECT content, metadata FROM rag_items WHERE id = ? AND collection_name = 'system_learnings'",
            [doc_id],
        ).fetchone()
        if not row:
            return JSONResponse(status_code=404, content={"status": "ERROR", "message": "Learning not found"})
        meta = json.loads(row["metadata"])
        new_content = payload.get("content", row["content"])
        if payload.get("metadata") is not None:
            meta.update(payload["metadata"])
        if new_content != row["content"]:
            meta["indexed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _conn().execute(
            "UPDATE rag_items SET content = ?, metadata = ? WHERE id = ?",
            [new_content, json.dumps(meta), doc_id],
        )
        # Re-embed on content change so future retrieval stays accurate.
        if new_content != row["content"]:
            try:
                vector = embed([new_content])[0]
                _adapter().add(doc_id, vector, "system_learnings", meta.get("user_id", "default"))
                _conn().execute("DELETE FROM rag_fts WHERE id = ?", [doc_id])
                _conn().execute(
                    "INSERT INTO rag_fts(id, content, collection_name, user_id) VALUES(?, ?, ?, ?)",
                    [doc_id, new_content, "system_learnings", meta.get("user_id", "default").lower()],
                )
            except Exception as e:  # pragma: no cover - embedding best effort
                log.warning(f"Re-embed on edit failed for {doc_id}: {e}")
        _conn().commit()
        return {"status": "SUCCESS", "id": doc_id}
    except Exception as e:
        log.error(f"Failed to edit learning {doc_id}: {e}")
        return JSONResponse(status_code=500, content={"status": "ERROR", "message": str(e)})


@app.delete("/rag/learning/{doc_id}", dependencies=[Depends(require_internal)])
async def delete_learning(doc_id: str):
    """Delete a single Raven lesson (never a row of another collection)."""
    try:
        exists = _conn().execute(
            "SELECT 1 FROM rag_items WHERE id = ? AND collection_name = 'system_learnings'",
            [doc_id],
        ).fetchone()
        if not exists:
            return JSONResponse(status_code=404, content={"status": "ERROR", "message": "Learning not found"})
        _delete_items([doc_id])
        return {"status": "SUCCESS", "id": doc_id}
    except Exception as e:
        log.error(f"Failed to delete learning {doc_id}: {e}")
        return JSONResponse(status_code=500, content={"status": "ERROR", "message": str(e)})


@app.patch("/rag/learning/{doc_id}/applied", dependencies=[Depends(require_internal)])
async def mark_learning_applied(doc_id: str):
    """Record that a mission ACTUALLY APPLIED this lesson (honest reuse signal).

    Distinct from retrieval: the model must cite the lesson id in its plan
    for this to fire. Drives ``applied_count`` (the real usefulness metric).
    """
    try:
        if not _item_exists(doc_id):
            return JSONResponse(
                status_code=404,
                content={"status": "ERROR", "message": "Learning not found"},
            )
        _bump_applied([doc_id])
        return {"status": "SUCCESS", "id": doc_id}
    except Exception as e:
        log.error(f"Failed to mark learning applied {doc_id}: {e}")
        return JSONResponse(status_code=500, content={"status": "ERROR", "message": str(e)})


def _reembed_doc(doc_id: str, new_content: str, meta: dict, vector: list[float] | None = None) -> None:
    """Re-embed a doc after its content changed (best effort, mirrors edit_learning).

    ``vector`` may be precomputed by async callers (embedding is CPU-bound
    ONNX inference; run it in a worker thread there); when omitted, embedding
    happens synchronously.
    """
    try:
        if vector is None:
            vector = embed([new_content])[0]
        _adapter().add(doc_id, vector, "system_learnings", meta.get("user_id", "default"))
        _conn().execute("DELETE FROM rag_fts WHERE id = ?", [doc_id])
        _conn().execute(
            "INSERT INTO rag_fts(id, content, collection_name, user_id) VALUES(?, ?, ?, ?)",
            [doc_id, new_content, "system_learnings", meta.get("user_id", "default").lower()],
        )
    except Exception as e:  # pragma: no cover - embedding best effort
        log.warning(f"Re-embed during dream failed for {doc_id}: {e}")


# --- Priority scoring for RSI self-improvement ---


def _lesson_priority_score(d: dict) -> float:
    """Composite score balancing reuse, confidence, recency, and benchmark performance."""
    age_seconds = max(0, int(time.time()) - int(d.get("created_at") or 0))
    age_days = age_seconds / 86400.0
    recency_factor = max(0.25, 1.0 - (age_days / 90.0) * 0.75)
    meta = d.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    benchmark_score = float(meta.get("benchmark_score", meta.get("score", 0) or 0))
    return (
        (d.get("applied_count") or 0) * 3.0
        + (d.get("usage_count") or 0) * 1.0
        + float(d.get("confidence") or 0.5) * 10.0
        + benchmark_score * 2.0
    ) * recency_factor


@app.post("/rag/dream", dependencies=[Depends(require_internal)])
async def dream_learnings(
    user_id: str = "default",
    compact_at: int = 600,
    summary_len: int = 400,
    body: dict | None = None,
):
    """Dreaming mode: review Raven lessons and consolidate memory.

    Runs three deterministic passes over ``system_learnings`` for the user:
      1. COMPACT: lessons whose JSON content exceeds ``compact_at`` chars get
         their ``summary`` truncated to ``summary_len`` (re-embedded) so the
         mission context budget is not wasted on long prose.
      2. MERGE: lessons sharing the same ``rule`` are consolidated: the
         highest-confidence (tie: newest) lesson survives and absorbs the
         others' applied/usage counts and ids into ``supersedes``.
      3. PRUNE: any lesson referenced by another's ``supersedes`` list is
         deleted (outdated information is removed).
    Returns a dream report of what was compacted/merged/pruned.
    """
    try:
        target_user = user_id.lower()
        rows = _conn().execute(
            "SELECT id, content, metadata, created_at, usage_count, applied_count, "
            "rule, confidence, supersedes "
            "FROM rag_items WHERE collection_name = ? AND user_id = ? "
            "ORDER BY created_at DESC",
            ["system_learnings", target_user],
        ).fetchall()
        docs = [
            {
                "id": r["id"],
                "content": r["content"],
                "metadata": json.loads(r["metadata"]),
                "created_at": r["created_at"],
                "usage_count": r["usage_count"] or 0,
                "applied_count": r["applied_count"] or 0,
                "rule": (r["rule"] or "").strip(),
                "confidence": float(r["confidence"]) if r["confidence"] is not None else 0.5,
                "supersedes": json.loads(r["supersedes"]) if r["supersedes"] else [],
            }
            for r in rows
        ]
        reviewed = len(docs)

        # Pass 0: WEB SEARCH. Enrich lessons with real-world context.
        web_searched: list[str] = []
        seen_rules: set[str] = set()
        for d in docs:
            parsed: dict[str, Any] = {}
            if d["content"].strip().startswith("{"):
                with suppress(Exception):
                    parsed = json.loads(d["content"])
            rule = parsed.get("rule", parsed.get("topic", ""))
            if not rule or rule in seen_rules:
                continue
            seen_rules.add(rule)
            if len(seen_rules) > 10:
                break
            try:
                web_results = _search_web(rule, num_results=3)
            except Exception:
                web_results = []
            if web_results:
                meta = d["metadata"] if isinstance(d.get("metadata"), dict) else {}
                meta["web_context"] = web_results
                _conn().execute(
                    "UPDATE rag_items SET metadata = ? WHERE id = ? AND user_id = ?",
                    [json.dumps(meta), d["id"], target_user],
                )
                web_searched.append(d["id"])
        _conn().commit()

        # Pass 1: COMPACT oversized JSON lessons.
        compacted: list[str] = []
        pending_reembed: list[tuple[str, str, dict]] = []
        for d in docs:
            content = d["content"]
            if not content.strip().startswith("{"):
                continue
            try:
                parsed = json.loads(content)
            except Exception:
                continue
            if len(content) > compact_at and isinstance(parsed.get("summary"), str) and len(parsed["summary"]) > summary_len:
                parsed["summary"] = parsed["summary"][:summary_len]
                new_content = json.dumps(parsed, ensure_ascii=False)
                d["metadata"]["indexed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                _conn().execute(
                    "UPDATE rag_items SET content = ?, metadata = ? WHERE id = ?",
                    [new_content, json.dumps(d["metadata"]), d["id"]],
                )
                pending_reembed.append((d["id"], new_content, d["metadata"]))
                d["content"] = new_content
                compacted.append(d["id"])
        # Re-embedding is CPU-bound ONNX inference; run it in a worker thread
        # so the event loop stays responsive during the compaction pass.
        if pending_reembed:
            try:
                vectors = await asyncio.to_thread(embed_batched, [_with_task_prefix(p[1], "document") for p in pending_reembed])
            except Exception as ex:
                log.warning(f"Dream re-embed failed for {len(pending_reembed)} doc(s): {ex}")
            else:
                for i, (doc_id, new_content, meta) in enumerate(pending_reembed):
                    _reembed_doc(doc_id, new_content, meta, vectors[i])
        _conn().commit()

        # Pass 2: MERGE lessons sharing the same rule.
        merged: dict[str, list[str]] = {}
        by_rule: dict[str, list[dict]] = {}
        for d in docs:
            if d["rule"]:
                by_rule.setdefault(d["rule"].lower(), []).append(d)
        deleted: set[str] = set()
        for group in by_rule.values():
            if len(group) < 2:
                continue
            survivor = max(group, key=_lesson_priority_score)
            others = [d for d in group if d["id"] != survivor["id"]]
            for o in others:
                survivor["usage_count"] += o["usage_count"]
                survivor["applied_count"] += o["applied_count"]
                merged_ids = list(dict.fromkeys(survivor["supersedes"] + [o["id"]]))
                survivor["supersedes"] = merged_ids
                survivor["metadata"]["supersedes"] = merged_ids
            _conn().execute(
                "UPDATE rag_items SET usage_count = ?, applied_count = ?, "
                "metadata = ?, supersedes = ? WHERE id = ?",
                [
                    survivor["usage_count"],
                    survivor["applied_count"],
                    json.dumps(survivor["metadata"]),
                    json.dumps(survivor["supersedes"]),
                    survivor["id"],
                ],
            )
            merged[survivor["id"]] = [o["id"] for o in others]
            deleted.update(o["id"] for o in others)
        _conn().commit()
        if deleted:
            _delete_items(list(deleted))

        # Pass 3: PRUNE lessons referenced by another lesson's supersedes.
        pruned: list[str] = []
        referenced: set[str] = set()
        for d in docs:
            referenced.update(d["supersedes"])
        for doc_id in referenced:
            if doc_id not in deleted and _item_exists(doc_id):
                pruned.append(doc_id)
        if pruned:
            _delete_items(pruned)
        _conn().commit()

        # Pass 4: VALIDATE. Check structural integrity of surviving lessons
        # and generate a validation checklist for RSI (lessons that have been
        # applied/used but never verified against current workspace state are
        # flagged as candidates for the next mission to test).
        degraded: list[str] = []
        validation_checklist: list[dict] = []
        all_remaining: list[dict] = []
        for d in docs:
            if d["id"] in deleted or d["id"] in pruned:
                continue
            all_remaining.append(d)
            parsed: dict[str, Any] = {}
            if d["content"].strip().startswith("{"):
                with suppress(Exception):
                    parsed = json.loads(d["content"])
            critical = ["rule", "root_cause", "outcome"]
            missing = [f for f in critical if not parsed.get(f)]
            if missing:
                degraded.append(d["id"])
                log.warning(f"[Dream] lesson {d['id']} degraded, missing: {missing}")
            else:
                _pri = _lesson_priority_score(d)
                meta = d.get("metadata") if isinstance(d.get("metadata"), dict) else {}
                validations = meta.get("validations") or []
                last_passed = validations[0].get("passed") if validations else None
                # Only lessons that were actually applied and have not yet
                # passed validation belong on the RSI checklist; every other
                # surviving lesson is still eligible for priority injection.
                if (d.get("applied_count") or 0) > 0 and last_passed is not True:
                    validation_checklist.append({
                        "id": d["id"],
                        "rule": parsed.get("rule", ""),
                        "applied_count": d.get("applied_count", 0),
                        "priority": round(_pri, 2),
                    })
        # Sort validation checklist by priority descending; keep top 20
        validation_checklist.sort(key=lambda v: v["priority"], reverse=True)
        validation_checklist = validation_checklist[:20]

        # Priority-ranked lessons for mission prompt injection
        priority_hits: list[dict] = []
        for d in sorted(all_remaining, key=_lesson_priority_score, reverse=True)[:15]:
            _p = _lesson_priority_score(d)
            _c = d["content"]
            _rule = ""
            _out = ""
            _conf = float(d.get("confidence") or 0.5)
            if _c.strip().startswith("{"):
                try:
                    _pc = json.loads(_c)
                    _rule = _pc.get("rule", "")
                    _out = _pc.get("outcome", "")
                    _conf = float(_pc.get("confidence", _conf))
                except Exception:
                    pass
            priority_hits.append({
                "id": d["id"],
                "rule": _rule,
                "outcome": _out,
                "confidence": _conf,
                "applied_count": d.get("applied_count", 0),
                "usage_count": d.get("usage_count", 0),
                "priority": round(_p, 2),
            })

        kept = _conn().execute(
            "SELECT COUNT(*) AS c FROM rag_items WHERE collection_name = ? AND user_id = ?",
            ["system_learnings", target_user],
        ).fetchone()["c"]
        return {
            "status": "SUCCESS",
            "user_id": target_user,
            "reviewed": reviewed,
            "compacted": compacted,
            "merged": merged,
            "pruned": pruned,
            "kept": kept,
            "degraded": degraded,
            "validation_checklist": validation_checklist,
            "priority_hits": priority_hits,
        }
    except Exception as e:
        log.error(f"Dream failed: {e}")
        return JSONResponse(status_code=500, content={"status": "ERROR", "message": str(e)})


@app.post("/rag/dream/validate", dependencies=[Depends(require_internal)])
async def validate_lessons(
    payload: dict,
    user_id: str = "default",
):
    """Report validation results for lessons tested during a mission.

    Payload: {"lesson_id": "lesson-abc", "passed": true, "evidence": "..."}
    Updates the lesson's metadata with the latest validation outcome
    so future dreaming passes can weight it appropriately.
    """
    try:
        target_user = user_id.lower()
        lesson_id = (payload or {}).get("lesson_id", "")
        if not lesson_id:
            return JSONResponse(status_code=400, content={"status": "ERROR", "message": "lesson_id required"})
        passed = bool((payload or {}).get("passed", False))
        evidence = str((payload or {}).get("evidence", ""))[:500]
        conn = _conn()
        row = conn.execute(
            "SELECT metadata FROM rag_items WHERE id = ? AND collection_name = ? AND user_id = ?",
            [lesson_id, "system_learnings", target_user],
        ).fetchone()
        if not row:
            return JSONResponse(status_code=404, content={"status": "ERROR", "message": "lesson not found"})
        meta = json.loads(row["metadata"]) if row["metadata"] else {}
        validations = meta.setdefault("validations", [])
        validations.insert(0, {
            "passed": passed,
            "evidence": evidence,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        meta["validations"] = validations = validations[:5]
        if passed:
            meta.setdefault("validation_success_count", 0)
            meta["validation_success_count"] = meta["validation_success_count"] + 1
        else:
            meta.setdefault("validation_fail_count", 0)
            meta["validation_fail_count"] = meta["validation_fail_count"] + 1
        conn.execute(
            "UPDATE rag_items SET metadata = ? WHERE id = ? AND user_id = ?",
            [json.dumps(meta), lesson_id, target_user],
        )
        conn.commit()
        return {"status": "SUCCESS", "lesson_id": lesson_id, "passed": passed}
    except Exception as e:
        log.error(f"Dream validation failed: {e}")
        return JSONResponse(status_code=500, content={"status": "ERROR", "message": str(e)})


@app.get("/rag/dream/pending", dependencies=[Depends(require_internal)])
async def pending_validations(user_id: str = "default"):
    """Return a compact checklist of lessons to validate on next mission.

    Lessons with applied_count > 0 but no validation record (or last validation failed)
    are candidates. Sorted by priority score. Used by the gateway to inject
    a validation checklist into mission prompts (RSI self-test loop).
    """
    try:
        target_user = user_id.lower()
        rows = _conn().execute(
            "SELECT id, content, metadata, applied_count, usage_count, confidence "
            "FROM rag_items WHERE collection_name = ? AND user_id = ?",
            ["system_learnings", target_user],
        ).fetchall()
        candidates: list[dict] = []
        for r in rows:
            meta = json.loads(r["metadata"]) if r["metadata"] else {}
            validations = meta.get("validations", [])
            last_passed = validations[0].get("passed") if validations else None
            if (r["applied_count"] or 0) > 0 and last_passed is not True:
                parsed: dict[str, Any] = {}
                if r["content"].strip().startswith("{"):
                    with suppress(Exception):
                        parsed = json.loads(r["content"])
                candidates.append({
                    "id": r["id"],
                    "rule": parsed.get("rule", ""),
                    "outcome": parsed.get("outcome", ""),
                    "applied_count": r["applied_count"],
                    "last_validated": validations[0].get("at") if validations else None,
                })
        candidates.sort(key=lambda c: c["applied_count"], reverse=True)
        return {"status": "SUCCESS", "validations_pending": candidates[:10]}
    except Exception as e:
        log.error(f"Dream pending failed: {e}")
        return JSONResponse(status_code=500, content={"status": "ERROR", "message": str(e)})


# --- Alpaca benchmark integration ---

def _fetch_source(source: str) -> Any:
    if source.startswith("http://") or source.startswith("https://"):
        req = urllib.request.Request(source, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    with open(source) as f:
        return json.load(f)

def _list_benchmark_result_files(results_url: str) -> list[str]:
    try:
        api_url = results_url.rstrip("/") + "/api/results"
        data = _fetch_source(api_url)
        results = data.get("results", data if isinstance(data, list) else [])
        filenames = []
        for r in results:
            if r.get("status") != "completed":
                continue
            fn = r.get("filename", "")
            if fn and fn.endswith(".json"):
                filenames.append(fn)
        return sorted(filenames)
    except Exception as e:
        log.warning(f"[benchmark] failed to list result files via API: {e}")
        return []

def _group_tests_by_category(data: dict) -> dict:
    if "tests" in data and isinstance(data["tests"], list):
        grouped: dict = {}
        for t in data["tests"]:
            cat = t.get("category", "unknown")
            grouped.setdefault(cat, []).append(t)
        return grouped
    return data

BENCHMARKS_PATH = os.environ.get(
    "ALPACA_BENCHMARKS_PATH",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "..", "..", "alpaca", "benchmark_tests.json",
    ),
)
BENCHMARK_RESULTS_DIR = os.environ.get(
    "ALPACA_BENCHMARK_RESULTS_DIR",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "..", "..", "alpaca", "data", "llm_benchmarks", "models",
    ),
)
BENCHMARK_CAT_LABELS = {
    "coding": "Python/code execution", "reasoning": "Logical reasoning",
    "instruction": "Instruction following", "creative": "Creative writing",
    "home_automation": "Home automation", "knowledge": "Knowledge/MMLU",
    "mmlu_pro": "MMLU-Pro", "gpqa_diamond": "GPQA-Diamond",
    "hle": "HLE", "math_hard": "Math-Hard", "ifeval": "IFEval",
    "multimodal": "Multimodal", "gamedev": "Game dev", "appdev": "App dev",
    "linux_admin": "Linux admin", "webdev": "Web dev", "database": "Database",
    "cpp": "C++", "java": "Java", "debugging": "Debugging",
}


@app.post("/rag/benchmark/ingest-curriculum", dependencies=[Depends(require_internal)])
async def ingest_benchmark_curriculum(path: str | None = None):
    """Ingest benchmark test categories as Raven curriculum lessons.

    Reads benchmark_tests.json and creates one lesson per category.
    Each lesson encodes the category's rules, test names, and (if available)
    expected answers as ground-truth validation criteria. Lessons tagged
    ``benchmark`` are auto-validated against expected answers when available.
    """
    try:
        _path = path or BENCHMARKS_PATH
        categories = _fetch_source(_path)
        if "tests" in categories and isinstance(categories["tests"], list):
            categories = _group_tests_by_category(categories)
        target_user = "default"
        now = int(time.time())
        now_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        ingested = []
        for cat_name, tests in categories.items():
            if not isinstance(tests, list) or not tests:
                continue
            labels = [t.get("label", t.get("id", "")) for t in tests]
            expected_ids = [t.get("expected") for t in tests if t.get("expected")]
            rule = f"Category {cat_name}: {', '.join(labels[:5])}"
            content = json.dumps({
                "id": f"benchmark-{cat_name}",
                "topic": f"benchmark-{cat_name}",
                "rule": rule,
                "root_cause": f"Validate capabilities in {BENCHMARK_CAT_LABELS.get(cat_name, cat_name)}",
                "outcome": f"{len(tests)} tasks, {len(expected_ids)} with ground truth",
                "confidence": 0.5,
                "expected_answers": expected_ids[:10],
                "test_ids": [t.get("id", "") for t in tests[:20]],
                "summary": f"Benchmark category covering {len(tests)} tasks in {BENCHMARK_CAT_LABELS.get(cat_name, cat_name)}",
            }, ensure_ascii=False)
            doc_id = f"benchmark-{cat_name}"
            meta = {
                "id": f"benchmark-{cat_name}",
                "topic": f"benchmark-{cat_name}",
                "rule": rule,
                "type": "benchmark_curriculum",
                "benchmark_score": 0.0,
                "category": cat_name,
                "task_count": len(tests),
                "ground_truth_count": len(expected_ids),
                "indexed_at": now_ts,
            }
            _add_item("system_learnings", doc_id, target_user, content, meta, now, now_ts)
            ingested.append(doc_id)
        _conn().commit()
        return {
            "status": "SUCCESS",
            "categories_ingested": len(ingested),
            "lesson_ids": ingested,
        }
    except Exception as e:
        log.error(f"Benchmark curriculum ingest failed: {e}")
        return JSONResponse(status_code=500, content={"status": "ERROR", "message": str(e)})


@app.post("/rag/benchmark/ingest-results", dependencies=[Depends(require_internal)])
async def ingest_benchmark_results(models: list[str] | None = None):
    """Ingest model benchmark results to update lesson confidence.

    Reads per-model result files from alpaca data dir. For each model and
    category, updates the matching benchmark lesson's metadata with:
    benchmark_score, success_rate, and validation evidence from the
    highest-scoring model's responses. This feeds directly into
    priority scoring so Raven knows which capabilities are proven.
    """
    try:
        results_dir = BENCHMARK_RESULTS_DIR
        target_user = "default"
        now_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        updates: dict[str, dict] = {}

        if results_dir.startswith("http://") or results_dir.startswith("https://"):
            base = results_dir.rstrip("/")
            if base.endswith("/api/tests"):
                tests_data = _fetch_source(base)
                tests = tests_data.get("tests", []) if isinstance(tests_data, dict) else []
                for test in tests:
                    cat = test.get("category", "unknown")
                    lesson_id = f"benchmark-{cat}"
                    scores_raw = test.get("models_scores", {})
                    if isinstance(scores_raw, dict) and scores_raw:
                        scores = [v for v in scores_raw.values() if isinstance(v, (int, float))]
                        if scores:
                            avg_score = sum(scores) / len(scores)
                            passed = test.get("models_passed", 0)
                            total = test.get("models_tested", len(scores))
                            if total == 0:
                                total = 1
                            best_score = max(scores)
                            if lesson_id not in updates or best_score > updates[lesson_id].get("score", 0):
                                updates[lesson_id] = {
                                    "benchmark_score": round(avg_score, 2),
                                    "success_rate": round(passed / total, 4) if isinstance(passed, (int, float)) else 0,
                                    "model": "multi",
                                    "category": cat,
                                    "tests_passed": passed,
                                    "tests_total": total,
                                }
            elif base.endswith("/api/results"):
                results_data = _fetch_source(base)
                results_list = results_data.get("results", []) if isinstance(results_data, dict) else []
                for r in results_list:
                    models_tested = r.get("models", [])
                    status = r.get("status", "cancelled")
                    score = 100 if status == "completed" else 0
                    for model in models_tested:
                        cat = model.split(":")[-1] if ":" in model else model
                        lesson_id = f"benchmark-{cat.lower()}"
                        if lesson_id not in updates or score > updates[lesson_id].get("score", 0):
                            updates[lesson_id] = {
                                "benchmark_score": score,
                                "success_rate": 1.0 if score > 0 else 0,
                                "model": model,
                                "category": cat,
                                "tests_passed": score,
                                "tests_total": 100,
                            }
        else:
            if not os.path.isdir(results_dir):
                return JSONResponse(
                    status_code=404,
                    content={"status": "ERROR", "message": f"benchmark results dir not found: {results_dir}"},
                )
            model_names = models or sorted(
                f for f in os.listdir(results_dir) if f.endswith(".json")
            )
            for model_file in model_names:
                filepath = os.path.join(results_dir, model_file)
                try:
                    data = _fetch_source(filepath)
                except Exception:
                    continue
                model_name = data.get("model", model_file.replace(".json", ""))
                results = data.get("results", [])
                for entry in results:
                    cats = {k.replace("category_", ""): v for k, v in entry.items() if isinstance(v, dict) and "tests_run" in v}
                    for cat, info in cats.items():
                        lesson_id = f"benchmark-{cat}"
                        score = info.get("score", 0)
                        passed = info.get("tests_passed", 0)
                        total = info.get("tests_run", 1)
                        if total == 0:
                            total = 1
                        if lesson_id not in updates or score > updates[lesson_id].get("score", 0):
                            updates[lesson_id] = {
                                "benchmark_score": score,
                                "success_rate": passed / total,
                                "model": model_name,
                                "category": cat,
                                "tests_passed": passed,
                                "tests_total": total,
                            }

        applied = 0
        for lesson_id, result in updates.items():
            meta = {
                "id": lesson_id,
                "benchmark_score": result["benchmark_score"],
                "success_rate": result["success_rate"],
                "model": result["model"],
                "category": result["category"],
                "type": "benchmark_result",
                "tests_passed": result["tests_passed"],
                "tests_total": result["tests_total"],
                "indexed_at": now_ts,
            }
            try:
                _conn().execute(
                    "UPDATE rag_items SET metadata = ? WHERE id = ? AND user_id = ?",
                    [json.dumps(meta), lesson_id, target_user],
                )
                applied += 1
            except Exception as _e:
                log.warning(f"[benchmark] update failed for {lesson_id}: {_e}")
        _conn().commit()
        return {"status": "SUCCESS", "models_processed": len(updates), "lessons_updated": applied}
    except Exception as e:
        log.error(f"Benchmark results ingest failed: {e}")
        return JSONResponse(status_code=500, content={"status": "ERROR", "message": str(e)})


@app.get("/rag/benchmark/insights", dependencies=[Depends(require_internal)])
async def benchmark_insights(user_id: str = "default"):
    """Return benchmark-driven learning insights.

    Top proven capabilities (high benchmark score), top gaps (low score
    with high task count), and validation candidates (benchmark lessons
    not yet validated). Used by the gateway to inject capability guidance
    into mission prompts.
    """
    try:
        target_user = user_id.lower()
        rows = _conn().execute(
            "SELECT id, content, metadata FROM rag_items "
            "WHERE collection_name = 'system_learnings' AND user_id = ? "
            "AND metadata LIKE '%benchmark%'",
            [target_user],
        ).fetchall()
        proven: list[dict] = []
        gaps: list[dict] = []
        for r in rows:
            meta = json.loads(r["metadata"]) if r["metadata"] else {}
            score = float(meta.get("benchmark_score", meta.get("score", 0) or 0))
            task_count = meta.get("task_count", 0)
            lesson_id = meta.get("id", r["id"])
            cat = meta.get("category", "unknown")
            entry = {"id": lesson_id, "category": cat, "score": score, "task_count": task_count}
            if score >= 70:
                proven.append(entry)
            elif score < 50 and task_count >= 3:
                gaps.append(entry)
        proven.sort(key=lambda x: x["score"], reverse=True)
        gaps.sort(key=lambda x: x["score"])
        return {
            "status": "SUCCESS",
            "proven_capabilities": proven[:10],
            "priority_gaps": gaps[:10],
            "guidance": (
                "Focus missions on priority_gaps before attempting advanced tasks. "
                "Proven capabilities can be assumed as available tools."
                if gaps else "All benchmark capabilities are performing well."
            ),
        }
    except Exception as e:
        log.error(f"Benchmark insights failed: {e}")
        return JSONResponse(status_code=500, content={"status": "ERROR", "message": str(e)})


REDIS_URL = os.environ.get("REDIS_URL", os.environ.get("HOST_REDIS_URL", "redis://127.0.0.1:6379/0"))
_redis_client: redis.Redis | None = None

def _redis() -> redis.Redis | None:
    global _redis_client
    if _redis_client is None:
        try:
            _redis_client = redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=3)
            _redis_client.ping()
        except Exception as e:
            log.warning(f"[redis] connection failed: {e}")
            return None
    return _redis_client

def _search_web(query: str, num_results: int = 3) -> list[dict[str, str]]:
    try:
        url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        import re as _re
        results = []
        for m in _re.finditer(r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html):
            href = m.group(1)
            title = _re.sub(r"<[^>]+>", "", m.group(2))
            results.append({"title": title, "url": href})
            if len(results) >= num_results:
                break
        for m in _re.finditer(r'<a[^>]+href="(http[^"]+)"[^>]*>(.*?)</a>', html):
            href = m.group(1)
            title = _re.sub(r"<[^>]+>", "", m.group(2))
            if not any(r["url"] == href for r in results):
                results.append({"title": title, "url": href})
                if len(results) >= num_results:
                    break
        return results
    except Exception as e:
        log.warning(f"[websearch] {e}")
        return []


def _ingest_redis_data(user_id: str = "default") -> dict:
    client = _redis()
    if client is None:
        return {"status": "ERROR", "message": "Redis not available"}
    target_user = user_id.lower()
    now = int(time.time())
    now_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    ingested = 0
    source_counts: dict[str, int] = {}
    try:
        for key in client.keys("jarvis:talk:last_msg:*"):
            try:
                data = client.get(key)
                if not data:
                    continue
                content = data[:5000] if isinstance(data, str) else str(data)[:5000]
                doc_id = f"redis-chat:{key}"
                meta = {
                    "id": doc_id, "topic": "redis-chat-history",
                    "rule": f"Chat history from {key}",
                    "type": "redis_chat_history",
                    "source": key,
                    "user_id": target_user,
                    "indexed_at": now_ts,
                }
                _add_item("system_learnings", doc_id, target_user, content, meta, now, now_ts)
                ingested += 1
                source_counts["chat_history"] = source_counts.get("chat_history", 0) + 1
            except Exception:
                continue
        total_logs = client.zcard("logs:entries")
        if total_logs > 0:
            logs = client.zrange("logs:entries", 0, min(99, total_logs-1))
            content = "\n".join(str(entry) for entry in logs if entry)[:5000]
            doc_id = "redis-logs:entries"
            meta = {
                "id": doc_id, "topic": "redis-system-logs",
                "rule": "System logs from Redis",
                "type": "redis_logs",
                "source": "logs:entries",
                "user_id": target_user,
                "indexed_at": now_ts,
            }
            _add_item("system_learnings", doc_id, target_user, content, meta, now, now_ts)
            ingested += 1
            source_counts["logs"] = source_counts.get("logs", 0) + 1
        for key in client.keys("raven:job:*"):
            try:
                data = client.get(key)
                if not data:
                    continue
                content = data[:5000] if isinstance(data, str) else str(data)[:5000]
                doc_id = f"redis-job:{key}"
                meta = {
                    "id": doc_id, "topic": "raven-job-data",
                    "rule": f"Job data from {key}",
                    "type": "redis_jobs",
                    "source": key,
                    "user_id": target_user,
                    "indexed_at": now_ts,
                }
                _add_item("system_learnings", doc_id, target_user, content, meta, now, now_ts)
                ingested += 1
                source_counts["jobs"] = source_counts.get("jobs", 0) + 1
            except Exception:
                continue
        _conn().commit()
    except Exception as e:
        log.error(f"[redis] ingest failed: {e}")
    return {"status": "SUCCESS", "ingested": ingested, "sources": source_counts}

@app.post("/rag/ingest/redis", dependencies=[Depends(require_internal)])
async def ingest_redis(payload: dict):
    user_id = payload.get("user_id", "default")
    return _ingest_redis_data(user_id)


@app.get("/rag/indexed-paths", dependencies=[Depends(require_internal)])
async def get_indexed_paths(user_id: str = "default", collection_name: str = "nextcloud_files"):
    """Distinct source paths currently indexed for a user in a collection.

    ``collection_name`` is a parameter because the storage indexer needs this to
    compute which of its own files disappeared upstream. Hardcoding one
    collection made that impossible for any future provider.
    """
    user_id = user_id.lower()
    collection_name = canonical_collection(collection_name) or collection_name
    try:
        rows = _conn().execute(
            "SELECT metadata FROM rag_items WHERE collection_name = ? AND user_id = ?",
            [collection_name, user_id],
        ).fetchall()
        paths = {json.loads(r["metadata"]).get("path") for r in rows if r["metadata"]}
        paths.discard(None)
        return {"status": "SUCCESS", "paths": list(paths)}
    except Exception as e:
        log.error(f"Failed to fetch indexed paths: {e}")
        return JSONResponse(status_code=500, content={"status": "ERROR", "message": str(e)})


@app.get("/rag/owners", dependencies=[Depends(require_internal)])
async def get_collection_owners(collection_name: str):
    """Every ``user_id`` holding rows in one collection, with its row count.

    The storage indexer asks this after a whole-library Calibre crawl: the library
    is shared and indexed under ``default``, and copies an older crawl filed under
    one person's account (which only that person's searches could see) are purged
    once the shared copy is complete.
    """
    collection_name = canonical_collection(collection_name) or collection_name
    rows = _conn().execute(
        "SELECT user_id, COUNT(*) AS n FROM rag_items WHERE collection_name = ? GROUP BY user_id",
        [collection_name],
    ).fetchall()
    return {"status": "SUCCESS", "collection_name": collection_name,
            "owners": {r["user_id"]: int(r["n"]) for r in rows}}


@app.post("/rag/sync/files", dependencies=[Depends(require_internal)])
async def sync_files(payload: dict):
    chunks = payload.get("chunks", [])
    user_id = payload.get("user_id", "default").lower()
    collection_name = canonical_collection(payload.get("collection_name")) or "nextcloud_files"

    if not chunks:
        return {"status": "SUCCESS", "count": 0}

    now_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    now = int(time.time())
    count = 0
    pending: list[tuple[str, str, dict]] = []

    for c in chunks:
        if not isinstance(c, dict):
            continue
        content = c.get("content")
        metadata = c.get("metadata", {})
        if not content:
            continue

        path = metadata.get("path", "unknown")
        chunk_idx = metadata.get("chunk_index", 0)
        path_hash = hashlib.md5(path.encode()).hexdigest()

        cid = (
            f"file:{user_id}:{path_hash}:meta"
            if metadata.get("is_metadata")
            else f"file:{user_id}:{path_hash}:{chunk_idx}"
        )

        meta = {}
        for k, v in metadata.items():
            meta[k] = v if isinstance(v, (str, int, float, bool)) else str(v)
        meta["user_id"] = user_id
        if "indexed_at" not in meta:
            meta["indexed_at"] = now_ts
        if "created_at" not in meta:
            meta["created_at"] = now

        pending.append((cid, content, meta))

    # Embedding is CPU-bound ONNX inference; run it in a worker thread so the
    # event loop stays responsive while file chunks are being indexed.
    try:
        vectors = await asyncio.to_thread(embed_batched, [_with_task_prefix(p[1], "document") for p in pending])
    except Exception as ex:
        log.error(f"File chunk embedding failed for {len(pending)} chunks: {ex}")
        return JSONResponse(
            status_code=500,
            content={"status": "ERROR", "message": f"File chunk embedding failed: {ex}"},
        )
    for i, (cid, content, meta) in enumerate(pending):
        try:
            _add_item(collection_name, cid, user_id, content, meta, now, now_ts, vector=vectors[i], commit=False)
            count += 1
        except Exception as e:
            log.error(f"File chunk sync failed for {cid}: {e}")
    _conn().commit()

    log.info(f"Synced {count} file chunks for user {user_id} into {collection_name}")
    return {"status": "SUCCESS", "count": count}


def _purge_ids(collection_name: str, user_id: str, filter_meta: dict) -> list[str]:
    """Resolve the row ids a purge will delete, applying the metadata filter.

    Shared by both purge forms so they cannot drift: the legacy GET once
    *demanded* a ``filter`` and then ignored it, deleting the caller's whole
    collection while they watched. An empty filter means "the whole
    collection for this user", which is an explicit, scoped operation --
    never a way to smuggle a broad delete through a missing parameter.
    """
    if filter_meta and not isinstance(filter_meta, dict):
        raise HTTPException(status_code=400, detail="filter must be a JSON object of metadata keys")
    collection_name = canonical_collection(collection_name) or collection_name
    sql = "SELECT id FROM rag_items WHERE collection_name = ? AND user_id = ?"
    params: list[Any] = [collection_name, user_id]
    for k, v in (filter_meta or {}).items():
        sql += " AND json_extract(metadata, ?) = ?"
        params.extend([f"$.{k}", v])
    return [r["id"] for r in _conn().execute(sql, params).fetchall()]


@app.post("/rag/purge/{collection_name}", dependencies=[Depends(require_internal)])
async def purge_collection_endpoint(collection_name: str, payload: dict):
    user_id = str(payload.get("user_id", "default")).lower()
    filter_meta = payload.get("filter") or {}
    try:
        ids = _purge_ids(collection_name, user_id, filter_meta)
        _delete_items(ids)
        log.info(f"Purged {len(ids)} entries from {collection_name} for user {user_id}")
        return {"status": "SUCCESS", "message": f"Purged entries from {collection_name}", "removed": len(ids)}
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Purge failed: {e}")
        raise HTTPException(status_code=500, detail="Purge failed") from e


@app.get("/rag/purge/{collection_name}", dependencies=[Depends(require_internal)])
async def purge_rag_collection(
    collection_name: str,
    user_id: str,
    filter: str | None = None,
    x_internal_secret: str | None = Header(default=None),
):
    """Purge entries via query parameters (legacy interface).

    ``filter`` is a JSON object of metadata keys, e.g.
    ``{"path": "/Notes/gone.md"}``. Missing it is refused (403); malformed or
    non-object JSON is a 400 naming the expected shape. Neither may delete
    anything: this route used to require a filter and then purge the entire
    collection regardless of what it said.
    """
    if filter is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    require_internal(x_internal_secret or "")
    try:
        filter_meta = json.loads(filter)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail=f'filter must be a JSON object like {{"path": "/file.md"}}: {exc.msg}',
        ) from exc
    if not isinstance(filter_meta, dict):
        raise HTTPException(
            status_code=400,
            detail=f'filter must be a JSON object like {{"path": "/file.md"}}, got {type(filter_meta).__name__}',
        )
    try:
        user_id = user_id.lower()
        ids = _purge_ids(collection_name, user_id, filter_meta)
        _delete_items(ids)
        log.info(f"Purged {len(ids)} entries from {collection_name} for user {user_id}")
        return {"status": "SUCCESS", "message": f"Collection {collection_name} purged for user {user_id}", "removed": len(ids)}
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Purge failed for {collection_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


def _retention_map() -> dict[str, int]:
    """Parse the ``rag_retention_days`` setting: collection name -> days.

    The seed carries the default policy (telemetry alerts 30 days,
    conversation memory 90 days); blank keeps everything, so nothing is ever
    expired on a guessed schedule. A malformed value fails loudly at the
    point of use naming the setting -- a typo like ``"30d"`` must not
    silently mean "keep forever", which is exactly how a retention policy
    rots.
    """
    from services import config as cfg

    raw = (cfg.RAG_RETENTION_DAYS or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"rag_retention_days is not valid JSON: {exc.msg}",
        ) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=500,
            detail=f"rag_retention_days must be a JSON object of collection->days, got {type(parsed).__name__}",
        )
    out: dict[str, int] = {}
    for name, value in parsed.items():
        try:
            days = int(value)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=500,
                detail=f"rag_retention_days[{name!r}] must be a whole number of days, got {value!r}",
            ) from exc
        if days <= 0:
            raise HTTPException(
                status_code=500,
                detail=f"rag_retention_days[{name!r}] must be at least 1 day, got {days}",
            )
        out[str(name)] = days
    return out


def _maintenance_plan(user_id: str, retention: dict[str, int], now: int, only: set[str] | None = None):
    """Compute what maintenance would remove, without deleting anything.

    Returns ``(collections_report, ids_to_remove)``. A row is removable when
    it duplicates a kept sibling (identical content without ``path``
    metadata -- file chunks are path-identified, so identical text in two
    files is two books, not one duplicated row), is expired against its
    collection's retention, or has empty content. The survivor of a
    duplicate group is the most-used row, then the oldest, so accumulated
    ``usage_count`` stays with the row that has history.
    """
    names = sorted(
        r["collection_name"]
        for r in _conn().execute(
            "SELECT DISTINCT collection_name FROM rag_items WHERE user_id = ?",
            [user_id],
        ).fetchall()
    )
    if only is not None:
        names = [n for n in names if n in only]
    report: dict[str, dict] = {}
    remove: list[str] = []
    for name in names:
        rows = _conn().execute(
            "SELECT id, content, metadata, usage_count, created_at FROM rag_items"
            " WHERE collection_name = ? AND user_id = ?",
            [name, user_id],
        ).fetchall()
        days = retention.get(name)
        cutoff = now - days * 86400 if days else None
        by_content: dict[str, list] = {}
        dup_groups = 0
        dup_ids: list[str] = []
        expired_ids: list[str] = []
        invalid_ids: list[str] = []
        for row in rows:
            content = (row["content"] or "").strip()
            if not content:
                invalid_ids.append(row["id"])
                continue
            try:
                meta = json.loads(row["metadata"] or "{}")
            except json.JSONDecodeError:
                meta = {}
            if cutoff is not None and (row["created_at"] or 0) < cutoff:
                expired_ids.append(row["id"])
            if "path" in meta:
                continue
            by_content.setdefault(content, []).append(row)
        for group in by_content.values():
            if len(group) < 2:
                continue
            dup_groups += 1
            ordered = sorted(
                group, key=lambda r: (-(r["usage_count"] or 0), r["created_at"] or 0)
            )
            dup_ids.extend(r["id"] for r in ordered[1:])
        report[name] = {
            "rows": len(rows),
            "duplicate_groups": dup_groups,
            "duplicate_rows": len(dup_ids),
            "expired_rows": len(expired_ids),
            "invalid_rows": len(invalid_ids),
        }
        remove.extend(dup_ids)
        remove.extend(expired_ids)
        remove.extend(invalid_ids)
    # A row can be both expired and a duplicate; count deletions by row.
    return report, list(dict.fromkeys(remove))


@app.post("/rag/maintenance", dependencies=[Depends(require_internal)])
async def maintenance_endpoint(payload: dict):
    """Report or remove duplicate, expired and invalid rows for one user.

    ``mode`` defaults to ``report``, which computes and returns counts while
    deleting nothing: the report is the admin visibility -- read it first,
    then re-send with ``mode="purge"`` to delete exactly what the report
    listed. ``collections`` narrows the sweep (unknown names are refused,
    not ignored). Expiry follows ``rag_retention_days``; collections the
    setting does not name are never expired.
    """
    user_id = str(payload.get("user_id", "default")).lower()
    mode = str(payload.get("mode", "report")).lower()
    if mode not in ("report", "purge"):
        raise HTTPException(status_code=400, detail="mode must be 'report' or 'purge'")
    requested = payload.get("collections")
    if requested is not None and (
        not isinstance(requested, list)
        or not all(isinstance(c, str) and c for c in requested)
    ):
        raise HTTPException(status_code=400, detail="collections must be a list of collection names")
    if requested is not None:
        requested = [canonical_collection(c) or c for c in requested]
    retention = _retention_map()
    only = None
    if requested is not None:
        present = {
            r["collection_name"]
            for r in _conn().execute(
                "SELECT DISTINCT collection_name FROM rag_items WHERE user_id = ?",
                [user_id],
            ).fetchall()
        }
        unknown = set(requested) - present
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"unknown collections for user {user_id}: {sorted(unknown)}",
            )
        only = set(requested)
    report, remove = _maintenance_plan(user_id, retention, int(time.time()), only=only)
    removed = 0
    if mode == "purge" and remove:
        _delete_items(remove)
        removed = len(remove)
    body: dict = {
        "status": "SUCCESS",
        "mode": mode,
        "user_id": user_id,
        "collections": report,
        "totals": {
            "collections": len(report),
            "removable": len(remove),
        },
        "retention": retention,
    }
    if mode == "purge":
        body["removed"] = removed
    return body


# Home devices (the ``home_devices`` collection)
#
# One row per entity, per user, per home hub: HomeHub and Home Assistant can
# both feed the index, so the row id names its hub and a sync from one hub
# never touches the other hub's rows. A row leaves the index when its hub no
# longer lists the entity (on that user's next sync from that hub), when its
# hub is switched off, or when nothing has refreshed it for
# HOME_DEVICE_STALE_HOURS and no search has returned it in that time (the
# staleness sweep: a user whose sync stopped, or lost access, or was removed).


def _device_item_id(source: str, user_id: str, entity_id: str) -> str:
    """The ``rag_items`` id of one user's copy of one entity from one hub.

    It was ``ha:{entity_id}`` for everyone once. ``id`` is the table's primary
    key, so each user's sync replaced the previous user's rows: no user ever
    found their own copy as "unchanged", all ~700 entities were re-embedded on
    every sync, and only the last user to sync had any devices indexed. It was
    then ``ha:{user}:{entity_id}``, which let the same entity id from two hubs
    overwrite each other; rows in that form are renamed once at startup
    (``_migrate_legacy_device_rows``).
    """
    return f"dev:{source}:{user_id}:{entity_id}"


def _device_sync_status_id(source: str, user_id: str) -> str:
    return f"sync_status:{source}:{user_id}"


def _default_device_source() -> str:
    """The hub a sync came from when the caller did not say (a gateway older than this one).

    Execution reads HomeHub whenever it is on, so that is the answer then.
    """
    from services import config as cfg

    if cfg.homehub_enabled() or not cfg.home_assistant_enabled():
        return SOURCE_HOMEHUB
    return SOURCE_HOME_ASSISTANT


def _enabled_device_sources() -> set[str]:
    from services import config as cfg

    enabled = set()
    if cfg.homehub_enabled():
        enabled.add(SOURCE_HOMEHUB)
    if cfg.home_assistant_enabled():
        enabled.add(SOURCE_HOME_ASSISTANT)
    return enabled


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _move_row(old_id: str, new_id: str, collection: str, metadata: str) -> None:
    """Rename one row and move it to ``collection`` in every table that holds it, keeping its vector."""
    db_conn = _conn()
    db_conn.execute(
        "UPDATE rag_items SET id = ?, collection_name = ?, metadata = ? WHERE id = ?",
        [new_id, collection, metadata, old_id],
    )
    with suppress(Exception):
        db_conn.execute(
            "UPDATE rag_fts SET id = ?, collection_name = ? WHERE id = ?", [new_id, collection, old_id]
        )
    db_conn.execute(
        "UPDATE vec_store SET id = ?, collection_name = ? WHERE id = ?", [new_id, collection, old_id]
    )
    # vec0 tables are rewritten rather than updated: delete and insert the same vector.
    with suppress(Exception):
        row = db_conn.execute(
            "SELECT embedding, user_id FROM vec_rag_items WHERE id = ?", [old_id]
        ).fetchone()
        if row is not None:
            db_conn.execute("DELETE FROM vec_rag_items WHERE id = ?", [old_id])
            db_conn.execute(
                "INSERT INTO vec_rag_items(id, embedding, collection_name, user_id) VALUES (?, ?, ?, ?)",
                [new_id, row["embedding"], collection, row["user_id"]],
            )


def _migrate_legacy_device_rows() -> dict[str, int]:
    """Move rows stored under ``ha_entities`` into ``home_devices``, once.

    Each ``ha:{user}:{entity_id}`` row is renamed to the new id form with the hub
    it most likely came from (``_default_device_source``), and ``sync_status:{user}``
    to ``sync_status:{source}:{user}``. Vectors are kept, so nothing is
    re-embedded. Rows in the oldest ``ha:{entity_id}`` form, and any whose new
    id already exists, are deleted (a sync would have removed them as orphans).
    Running it again finds nothing to do.
    """
    rows = _conn().execute(
        "SELECT id, user_id, metadata FROM rag_items WHERE collection_name = ?",
        [LEGACY_HOME_DEVICES],
    ).fetchall()
    if not rows:
        return {"moved": 0, "deleted": 0}
    source = _default_device_source()
    moved = 0
    doomed: list[str] = []
    for r in rows:
        old_id, user = r["id"], r["user_id"]
        try:
            meta = json.loads(r["metadata"]) if r["metadata"] else {}
        except Exception:
            meta = {}
        if old_id == f"sync_status:{user}":
            new_id = _device_sync_status_id(source, user)
        elif old_id.startswith(f"ha:{user}:"):
            new_id = _device_item_id(source, user, old_id[len(f"ha:{user}:"):])
            meta["type"] = "home_device"
        else:
            doomed.append(old_id)
            continue
        if _item_exists(new_id):
            doomed.append(old_id)
            continue
        meta["source"] = source
        _move_row(old_id, new_id, HOME_DEVICES, json.dumps(meta))
        moved += 1
    _conn().commit()
    if doomed:
        _delete_items(doomed)
    log.info(
        f"[home_devices] moved {moved} rows from {LEGACY_HOME_DEVICES} to {HOME_DEVICES} "
        f"(source {source}), deleted {len(doomed)} rows that a sync would have dropped"
    )
    return {"moved": moved, "deleted": len(doomed)}


def _rename_legacy_collection_in_text() -> int:
    """Say ``home_devices`` where stored lessons and capabilities still say ``ha_entities``.

    Protocol lessons are seeded once and never overwritten (users edit them), so
    the one that lists the collections kept the old name. The old name still
    works (``canonical_collection``), but a lesson should teach the current one.
    Only the word changes; counters and edits stay, and the row is re-embedded.
    """
    rows = _conn().execute(
        "SELECT id, collection_name, user_id, content, metadata, rule FROM rag_items "
        "WHERE collection_name IN ('system_learnings', 'system_capabilities') "
        "AND (content LIKE ? OR metadata LIKE ? OR rule LIKE ?)",
        [f"%{LEGACY_HOME_DEVICES}%"] * 3,
    ).fetchall()
    for r in rows:
        content = r["content"].replace(LEGACY_HOME_DEVICES, HOME_DEVICES)
        _conn().execute(
            "UPDATE rag_items SET content = ?, metadata = ?, rule = ? WHERE id = ?",
            [
                content,
                (r["metadata"] or "").replace(LEGACY_HOME_DEVICES, HOME_DEVICES),
                (r["rule"] or "").replace(LEGACY_HOME_DEVICES, HOME_DEVICES),
                r["id"],
            ],
        )
        with suppress(Exception):
            _conn().execute("UPDATE rag_fts SET content = ? WHERE id = ?", [content, r["id"]])
        _adapter().add(r["id"], embed([content])[0], r["collection_name"], r["user_id"])
    if rows:
        _conn().commit()
        log.info(f"[home_devices] renamed {LEGACY_HOME_DEVICES} to {HOME_DEVICES} in {len(rows)} stored lessons/capabilities")
    return len(rows)


# Search hits on device rows, waiting to be written as last_used_at. A search
# must not wait on a disk sync, so hits are collected here and written in one
# statement by the maintenance loop (and before every staleness sweep).
_pending_device_refs: dict[str, str] = {}


def _note_device_references(doc_ids: list[str]) -> None:
    now = _now_iso()
    for doc_id in doc_ids:
        if doc_id.startswith("dev:"):
            _pending_device_refs[doc_id] = now


def _flush_device_references() -> int:
    """Write the noted search hits: ``last_used_at`` and one more ``usage_count`` each."""
    if not _pending_device_refs:
        return 0
    batch = [(used_at, doc_id) for doc_id, used_at in _pending_device_refs.items()]
    _pending_device_refs.clear()
    try:
        _conn().executemany(
            "UPDATE rag_items SET last_used_at = ?, usage_count = usage_count + 1 WHERE id = ?",
            batch,
        )
        _conn().commit()
    except Exception as e:  # best effort: a lost reference only shortens a row's life
        log.warning(f"[home_devices] could not record {len(batch)} search references: {e}")
        return 0
    return len(batch)


def _sweep_stale_devices(now: int | None = None) -> dict[str, Any]:
    """Delete device rows nobody refreshes or uses any more, and old rows of a hub that is switched off.

    A row is stale when its last sync is older than HOME_DEVICE_STALE_HOURS and
    no search has returned it in that time either. Sync status rows go with the
    last device row of their user and hub. Entity ids that no row holds any more
    are cleared from the gateway's Redis state cache (``ha:state:<entity_id>``)
    and returned.
    """
    from services import config as cfg

    _flush_device_references()
    now = int(time.time()) if now is None else now
    hours = cfg.HOME_DEVICE_STALE_HOURS
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - hours * 3600))
    enabled = _enabled_device_sources()
    rows = _conn().execute(
        "SELECT id, user_id, metadata, indexed_at, last_used_at FROM rag_items WHERE collection_name = ?",
        [HOME_DEVICES],
    ).fetchall()
    stale: list[str] = []
    switched_off: list[str] = []
    kept_pairs: set[tuple[str, str]] = set()
    status_rows: dict[tuple[str, str], str] = {}
    removed_eids: set[str] = set()
    kept_eids: set[str] = set()
    for r in rows:
        try:
            meta = json.loads(r["metadata"]) if r["metadata"] else {}
        except Exception:
            meta = {}
        source = meta.get("source") or SOURCE_HOMEHUB
        if meta.get("type") == "sync_status":
            status_rows[(r["user_id"], source)] = r["id"]
            continue
        eid = meta.get("entity_id") or ""
        if source not in enabled and (r["indexed_at"] or "") < cutoff:
            # A switched-off hub's rows go once they are past the age too, never at once:
            # a container that started without its hub settings must not empty the index.
            switched_off.append(r["id"])
            removed_eids.add(eid)
        elif source not in enabled:
            kept_pairs.add((r["user_id"], source))
            kept_eids.add(eid)
        elif (r["indexed_at"] or "") < cutoff and (r["last_used_at"] or "") < cutoff:
            stale.append(r["id"])
            removed_eids.add(eid)
        else:
            kept_pairs.add((r["user_id"], source))
            kept_eids.add(eid)
    empty_status = [sid for pair, sid in status_rows.items() if pair not in kept_pairs]
    doomed = stale + switched_off + empty_status
    if doomed:
        _delete_items(doomed)
    gone_eids = sorted(e for e in removed_eids - kept_eids if e)
    if gone_eids:
        cache = _redis()
        if cache is not None:
            with suppress(Exception):
                cache.delete(*[f"ha:state:{eid}" for eid in gone_eids])
    result = {
        "stale_hours": hours,
        "removed_stale": len(stale),
        "removed_switched_off": len(switched_off),
        "removed_sync_status": len(empty_status),
        "kept": len(rows) - len(status_rows) - len(stale) - len(switched_off),
        "removed_entity_ids": gone_eids,
    }
    log.info(
        f"[home_devices] sweep: removed {len(stale)} stale rows (not synced or used in {hours} h), "
        f"{len(switched_off)} rows of a switched-off hub, {len(empty_status)} sync status rows; "
        f"kept {result['kept']}"
    )
    return result


async def _device_index_loop() -> None:
    """Record search references every minute and sweep stale device rows at start and then on an interval."""
    from services import config as cfg

    sweep_every = max(60, cfg.HOME_DEVICE_SWEEP_MINUTES * 60)
    next_sweep = 0.0
    while True:
        try:
            if time.monotonic() >= next_sweep:
                _sweep_stale_devices()
                next_sweep = time.monotonic() + sweep_every
            else:
                _flush_device_references()
        except Exception as e:
            log.warning(f"[home_devices] maintenance failed: {e}")
        await asyncio.sleep(60)


@app.post("/rag/devices/used", dependencies=[Depends(require_internal)])
async def devices_used(payload: dict):
    """Count devices a command was sent to as used: every user's row for them, from that hub.

    The gateway reports the entity ids of each command the hub ran (voice, the app,
    a schedule or an automation), batched. The rows' ``last_used_at`` and
    ``usage_count`` are written with the next flush, like a search hit.
    """
    source = payload.get("source") or SOURCE_HOMEHUB
    if source not in DEVICE_SOURCES:
        return JSONResponse(status_code=400, content={"status": "ERROR", "message": f"unknown source {source!r}"})
    entity_ids = [e for e in payload.get("entity_ids") or [] if isinstance(e, str) and _usable_entity_id(e)]
    if not entity_ids:
        return {"status": "SUCCESS", "rows": 0}
    marks = ",".join("?" for _ in entity_ids)
    rows = _conn().execute(
        f"SELECT id FROM rag_items WHERE collection_name = ? AND json_extract(metadata, '$.source') = ?"
        f" AND json_extract(metadata, '$.entity_id') IN ({marks})",
        [HOME_DEVICES, source, *entity_ids],
    ).fetchall()
    _note_device_references([r["id"] for r in rows])
    return {"status": "SUCCESS", "rows": len(rows)}


@app.post("/rag/devices/sweep", dependencies=[Depends(require_internal)])
async def sweep_devices():
    """Run the staleness sweep now (it also runs at start and every HOME_DEVICE_SWEEP_MINUTES)."""
    return {"status": "SUCCESS", **_sweep_stale_devices()}


# Home hubs still carry entities whose id is a serialized user record (see the
# test module for the two live ones). They are refused on every sync, and a
# sync runs every five minutes for every user, so the same warning was written
# ~2,400 times a day and buried everything else in the log. The full warning is
# repeated only when the set of refused ids changes for that user.
_refused_entity_ids: dict[str, tuple[str, ...]] = {}


def _log_refused_entity_ids(user: str, malformed_ids: list[str]) -> None:
    signature = tuple(sorted(set(malformed_ids)))
    if _refused_entity_ids.get(user) == signature:
        log.debug("[device_sync] still refusing %d malformed entity_ids for %s", len(signature), user)
        return
    _refused_entity_ids[user] = signature
    log.warning(
        f"[device_sync] refusing {len(malformed_ids)} malformed entity_ids for "
        f"{user}: {malformed_ids[:3]}"
    )


def _entity_area(attrs: dict) -> str:
    """The room an entity is in, as the hub reports it.

    Execution fills ``area_id`` with the area's name from the hub, which already
    falls back to the device's area when the entity has none of its own
    (HomeHub's ``area_name`` and Home Assistant's both do).
    """
    return str(attrs.get("area_id") or attrs.get("area_name") or attrs.get("area") or "unassigned area")


@app.post("/rag/sync/devices", dependencies=[Depends(require_internal)])
@app.post("/rag/sync/ha", dependencies=[Depends(require_internal)])
async def sync_devices(payload: dict, user_id: str | None = None):
    """Index one user's devices from one home hub (``source``: homehub or home_assistant).

    ``/rag/sync/ha`` is the route's old name, kept for callers built before the rename.
    """
    entities = payload.get("entities", [])
    resolved_user = (user_id or payload.get("user_id", "default")).lower()
    source = str(payload.get("source") or _default_device_source())
    if source not in DEVICE_SOURCES:
        return JSONResponse(
            status_code=400,
            content={"status": "ERROR", "message": f"source must be one of {list(DEVICE_SOURCES)}"},
        )
    now = int(time.time())
    now_ts = _now_iso()

    if not isinstance(entities, list):
        return JSONResponse(
            status_code=400,
            content={"status": "ERROR", "message": "entities must be a list"},
        )
    malformed_ids = [
        str(e.get("entity_id"))[:160]
        for e in entities
        if isinstance(e, dict)
        and e.get("entity_id")
        and not _usable_entity_id(e.get("entity_id"))
    ]
    if malformed_ids:
        _log_refused_entity_ids(resolved_user, malformed_ids)
    entities = [
        e for e in entities if isinstance(e, dict) and _usable_entity_id(e.get("entity_id", ""))
    ]

    own_prefix = _device_item_id(source, resolved_user, "")
    try:
        existing_rows = _conn().execute(
            "SELECT id, content, usage_count FROM rag_items"
            " WHERE collection_name = ? AND user_id = ? AND substr(id, 1, ?) = ?",
            [HOME_DEVICES, resolved_user, len(own_prefix), own_prefix],
        ).fetchall()
        existing = {r["id"]: (r["content"], r["usage_count"]) for r in existing_rows}
    except Exception:
        existing = {}
    existing_ids = set(existing)

    new_count = 0
    pending: list[tuple[str, str, dict, int]] = []
    # Entities whose embedded text is unchanged keep their vector: the gateway
    # calls this every 5 min per user, and re-embedding ~700 unchanged entities
    # each time kept the RAG container at ~350% CPU around the clock. Only
    # their metadata (device_* fields, timestamps) is refreshed.
    unchanged: list[tuple[str, dict]] = []
    for e in entities:
        eid = e.get("entity_id", "")
        if not eid:
            continue
        attrs = e.get("attributes") or {}
        fname = attrs.get("friendly_name", eid)
        area = _entity_area(attrs)
        device_class = attrs.get("device_class", "")
        supported = attrs.get("supported_features", 0)

        dev_ip = attrs.get("_device_ip", "")
        dev_mac = attrs.get("_device_mac", "")
        dev_hostname = attrs.get("_device_hostname", "")
        dev_method = attrs.get("_device_discovery_method", "")
        dev_last_verified = attrs.get("_device_last_verified", 0)
        dev_metadata = attrs.get("_device_metadata", {})

        content = f"Device: {fname} (ID: {eid}) | Area: {area} | Type: {eid.split('.')[0]}"
        if device_class:
            content += f" | Device Class: {device_class}"
        if dev_ip:
            content += f" | IP: {dev_ip}"
        if dev_mac:
            content += f" | MAC: {dev_mac}"
        if dev_hostname:
            content += f" | Hostname: {dev_hostname}"

        cid = _device_item_id(source, resolved_user, eid)
        if cid not in existing_ids:
            new_count += 1

        meta = {
            "entity_id": eid,
            "friendly_name": fname,
            "area": area,
            "device_class": device_class,
            "supported_features": str(supported),
            "user_id": resolved_user,
            "type": "home_device",
            "source": source,
            "domain": eid.split(".")[0],
            "updated_at": now,
            "created_at": now,
            "indexed_at": now_ts,
            "device_ip": dev_ip,
            "device_mac": dev_mac,
            "device_hostname": dev_hostname,
            "device_discovery_method": dev_method,
            "device_last_verified": str(dev_last_verified) if dev_last_verified else "",
            "device_metadata": json.dumps(dev_metadata) if dev_metadata else "",
        }
        if cid in existing and existing[cid][0] == content:
            unchanged.append((cid, {**meta, "usage_count": existing[cid][1] or 0}))
        else:
            pending.append((cid, content, meta, now))

    # Orphan cleanup: this user's rows from this hub that the hub no longer lists.
    incoming_eids = {e.get("entity_id") for e in entities if e.get("entity_id")}
    incoming_ids = {_device_item_id(source, resolved_user, eid) for eid in incoming_eids}
    stale_rows = sorted(existing_ids - incoming_ids)
    if stale_rows:
        _delete_items(stale_rows)
        log.info(f"[device_sync] removed {len(stale_rows)} {source} rows for {resolved_user}")
    # Report entity ids (the gateway clears its ``ha:state:{entity_id}`` cache with them).
    orphaned_entities = sorted({oid[len(own_prefix):] for oid in stale_rows} - incoming_eids)

    pending.append(
        (
            _device_sync_status_id(source, resolved_user),
            f"Last home hub sync ({source}) for {resolved_user} at {now}. Total: {len(entities)}, "
            f"New: {new_count}, Removed: {len(orphaned_entities)}",
            {
                "type": "sync_status",
                "source": source,
                "user_id": resolved_user,
                "timestamp": now,
                "count": len(entities),
                "new_count": new_count,
                "removed_count": len(orphaned_entities),
                "indexed_at": now_ts,
            },
            now,
        )
    )

    # Embedding is CPU-bound ONNX inference; run it in a worker thread so the
    # event loop stays responsive (health checks, searches) while devices
    # are being (re-)indexed.
    try:
        log.info(
            f"[device_sync] embedding {len(pending)} texts for {resolved_user} ({source}) "
            f"in batches of {EMBED_BATCH_SIZE} (rss={_self_rss_mib()}MiB)"
        )
        embed_started = time.monotonic()
        vectors = await asyncio.to_thread(embed_batched, [_with_task_prefix(p[1], "document") for p in pending])
        log.info(
            f"[device_sync] embedded {len(pending)} texts in "
            f"{time.monotonic() - embed_started:.1f}s (rss={_self_rss_mib()}MiB)"
        )
    except Exception as ex:
        log.error(f"[device_sync] embedding {len(pending)} entities failed: {ex}")
        return JSONResponse(
            status_code=500,
            content={"status": "ERROR", "message": f"Device embedding failed: {ex}"},
        )
    write_started = time.monotonic()
    for i, (cid, content, meta, created_at) in enumerate(pending):
        try:
            _add_item(HOME_DEVICES, cid, resolved_user, content, meta, created_at, now_ts, vector=vectors[i], commit=False)
        except Exception as ex:
            log.error(f"Device sync failed for {cid}: {ex}")
    if unchanged:
        _conn().executemany(
            "UPDATE rag_items SET metadata = ?, indexed_at = ? WHERE id = ?",
            [(json.dumps(meta), now_ts, cid) for cid, meta in unchanged],
        )
    _conn().commit()
    log.info(
        f"[device_sync] wrote {len(pending)} items, refreshed {len(unchanged)} unchanged in "
        f"{time.monotonic() - write_started:.1f}s (rss={_self_rss_mib()}MiB)"
    )

    return {
        "status": "SUCCESS",
        "source": source,
        "count": len(entities),
        "refused_count": len(malformed_ids),
        "new_count": new_count,
        "removed_count": len(orphaned_entities),
        "orphaned_entity_ids": orphaned_entities,
    }


#: The route's old name.
sync_ha = sync_devices


@app.get("/rag/devices/status", dependencies=[Depends(require_internal)])
@app.get("/rag/ha/status", dependencies=[Depends(require_internal)])
async def get_device_sync_status(user_id: str = "default"):
    """The user's latest device sync (``data``), and the latest from each hub (``sources``)."""
    rows = _conn().execute(
        "SELECT metadata FROM rag_items WHERE collection_name = ? AND user_id = ? AND id LIKE 'sync_status:%'",
        [HOME_DEVICES, user_id.lower()],
    ).fetchall()
    statuses = [json.loads(r["metadata"]) for r in rows if r["metadata"]]
    if not statuses:
        return {"status": "ERROR", "message": "No sync history found"}
    latest = max(statuses, key=lambda m: m.get("timestamp") or 0)
    return {
        "status": "SUCCESS",
        "data": latest,
        "sources": {m.get("source") or SOURCE_HOMEHUB: m for m in statuses},
    }


@app.get("/rag/devices/new", dependencies=[Depends(require_internal)])
@app.get("/rag/ha/new", dependencies=[Depends(require_internal)])
async def get_new_devices(user_id: str = "default", limit: int = 10):
    user_id = user_id.lower()
    last_24h = int(time.time()) - 86400
    rows = _conn().execute(
        "SELECT metadata FROM rag_items "
        "WHERE collection_name = ? AND user_id = ? AND created_at > ? "
        "AND id NOT LIKE 'sync_status:%' LIMIT ?",
        [HOME_DEVICES, user_id, last_24h, limit],
    ).fetchall()
    return {"status": "SUCCESS", "devices": [json.loads(r["metadata"]) for r in rows]}


@app.post("/rag/sync/capabilities", dependencies=[Depends(require_internal)])
async def sync_capabilities(payload: dict):
    capabilities = payload.get("capabilities", [])
    now_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    now = int(time.time())
    count = 0
    pending: list[tuple[str, str, dict]] = []
    for cap in capabilities:
        name = cap.get("name")
        description = cap.get("description", "")
        schema = cap.get("schema", "")
        type_ = cap.get("type", "tool")
        if not name:
            continue
        cid = f"cap:{type_}:{name}"
        content = f"Capability: {name} | Description: {description} | Schema/Usage: {schema}"
        meta = {
            "name": name,
            "type": type_,
            "user_id": "default",
            "description": description[:200],
            "indexed_at": now_ts,
        }
        if type_ == "binary":
            # Workspace toolchain inventory entries carry their version and
            # scenario tags so the gateway can render a truthful
            # [WORKSPACE TOOLCHAIN] block (what Raven can actually run).
            meta["version"] = (cap.get("version") or "")[:60]
            meta["tags"] = list(cap.get("tags") or [])
        pending.append((cid, content, meta))

    # Embedding is CPU-bound ONNX inference; run it in a worker thread so the
    # event loop stays responsive while capabilities are being indexed.
    try:
        vectors = await asyncio.to_thread(embed_batched, [_with_task_prefix(p[1], "document") for p in pending])
    except Exception as ex:
        log.error(f"Capability embedding failed for {len(pending)} entries: {ex}")
        return JSONResponse(
            status_code=500,
            content={"status": "ERROR", "message": f"Capability embedding failed: {ex}"},
        )
    for i, (cid, content, meta) in enumerate(pending):
        try:
            _add_item("system_capabilities", cid, "default", content, meta, now, now_ts, vector=vectors[i], commit=False)
            count += 1
        except Exception as e:
            log.error(f"Capability sync failed for {cid}: {e}")
    _conn().commit()

    # Binary inventory converges to the probing container's actual tool set:
    # drop binary rows this probe did not report so stale entries (e.g. from a
    # previously deployed service) are never advertised in [WORKSPACE TOOLCHAIN].
    if any(cid.startswith("cap:binary:") for cid, _, _ in pending):
        incoming = {cid for cid, _, _ in pending if cid.startswith("cap:binary:")}
        stale = [
            r["id"]
            for r in _conn()
            .execute(
                "SELECT id FROM rag_items "
                "WHERE collection_name = ? AND user_id = ? AND metadata LIKE ?",
                ["system_capabilities", "default", '%"binary"%'],
            )
            .fetchall()
            if r["id"] not in incoming
        ]
        if stale:
            _delete_items(stale)
            log.warning(f"Capability sync pruned {len(stale)} stale binary entries")
    return {"status": "SUCCESS", "count": count}


@app.get("/rag/toolchain", dependencies=[Depends(require_internal)])
async def get_toolchain(user_id: str = "default"):
    """Compact inventory of the workspace container's CLI tools.

    Returns the type="binary" capabilities the execution container reported
    at startup (name, version, scenario tags, short description) so the
    gateway can inject a truthful [WORKSPACE TOOLCHAIN] block into every
    mission prompt, so Raven always knows exactly which tools it can run.
    """
    try:
        rows = _conn().execute(
            "SELECT metadata FROM rag_items "
            "WHERE collection_name = ? AND user_id = ? AND metadata LIKE ? "
            "ORDER BY metadata",
            ["system_capabilities", "default", '%"binary"%'],
        ).fetchall()
        tools = []
        for r in rows:
            try:
                meta = json.loads(r["metadata"])
            except Exception:
                continue
            tools.append({
                "name": meta.get("name", ""),
                "version": meta.get("version", ""),
                "tags": meta.get("tags", []),
                "description": meta.get("description", ""),
            })
        return {"status": "SUCCESS", "count": len(tools), "tools": tools}
    except Exception as e:
        log.error(f"Failed to list toolchain: {e}")
        return JSONResponse(status_code=500, content={"status": "ERROR", "message": str(e)})


@app.post("/rag/calibre/tags", dependencies=[Depends(require_internal)])
async def set_calibre_book_tags(body: dict):
    """A book's tags changed in Calibre: patch them into every row of it.

    The text of the book is unchanged, so nothing is re-embedded; only the
    ``tags`` metadata that topics (``topics.py``) filter on is rewritten.
    """
    try:
        book_id = int(body.get("calibre_id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="calibre_id is required") from None
    tags = str(body.get("tags") or "")
    cur = _conn().execute(
        "UPDATE rag_items SET metadata = json_set(metadata, '$.tags', ?) "
        "WHERE collection_name = 'calibre_files' AND json_extract(metadata, '$.calibre_id') = ?",
        [tags, book_id],
    )
    _conn().commit()
    return {"status": "SUCCESS", "rows": cur.rowcount}


@app.get("/rag/resources/tagged", dependencies=[Depends(require_internal)])
async def list_tagged_resources(tag: str = Query(..., min_length=1, max_length=64), user_id: str = "default", limit: int = 50):
    """The books and documents about one topic (``topics.py``), once each.

    The family's Calibre library is shared, so its books are listed whoever
    indexed them; any other file is listed only for its owner (or the shared
    ``default`` user). Contents are not returned, only what each item is.
    """
    want = tag.strip().lower().removeprefix("jarvis:")
    like = f"%{want}%"
    rows = _conn().execute(
        "SELECT collection_name, user_id, metadata FROM rag_items "
        "WHERE (metadata LIKE ? COLLATE NOCASE) "
        "AND (collection_name = 'calibre_files' OR user_id = ? OR user_id = 'default') "
        "AND json_extract(metadata, '$.is_metadata') = 1 LIMIT 5000",
        [like, user_id.lower()],
    ).fetchall()
    books: dict[str, dict] = {}
    documents: dict[str, dict] = {}
    for row in rows:
        try:
            meta = json.loads(row["metadata"])
        except Exception:
            continue
        if not topics.matches(meta, [want]):
            continue
        if row["collection_name"] == "calibre_files":
            key = str(meta.get("calibre_id") or meta.get("path"))
            books.setdefault(key, {
                "title": meta.get("title") or meta.get("name"),
                "author": meta.get("author") or meta.get("authors"),
                "calibre_id": meta.get("calibre_id"),
                "tags": meta.get("tags"),
                "path": meta.get("path"),
            })
        else:
            path = str(meta.get("path") or "")
            documents.setdefault(path, {"name": meta.get("name") or path.rsplit("/", 1)[-1], "path": path, "collection": row["collection_name"]})
    ordered = sorted(books.values(), key=lambda b: str(b.get("title") or "").lower())
    return {"topic": want, "books": ordered[:limit], "documents": list(documents.values())[:limit]}


@app.get("/rag/resources/nextcloud", dependencies=[Depends(require_internal)])
async def list_nextcloud_resources(user_id: str = "default", limit: int = 15):
    """Inventory of the most recently indexed Nextcloud files.

    Lets the gateway render a compact [NEXTCLOUD RESOURCES] block so Raven
    knows which documents it can pull into its workspace (via
    StorageFileReadRequest -> WorkspaceFileWriteRequest). Only the paths,
    friendly names and timestamps are returned, not the contents.
    """
    try:
        target_user = "default"
        rows = _conn().execute(
            "SELECT id, content, metadata, created_at, indexed_at FROM rag_items "
            "WHERE collection_name = 'nextcloud_files' AND user_id = ? "
            "ORDER BY indexed_at DESC LIMIT ?",
            [target_user, limit],
        ).fetchall()
        files = []
        for r in rows:
            try:
                meta = json.loads(r["metadata"])
            except Exception:
                meta = {}
            path = meta.get("path") or ""
            files.append({
                "id": r["id"],
                "path": path,
                "name": meta.get("friendly_name") or meta.get("name") or path.rsplit("/", 1)[-1] or r["id"],
                "indexed_at": meta.get("indexed_at") or r["indexed_at"],
            })
        return {"status": "SUCCESS", "count": len(files), "files": files}
    except Exception as e:
        log.error(f"Failed to list nextcloud resources: {e}")
        return JSONResponse(status_code=500, content={"status": "ERROR", "message": str(e)})


@app.get("/rag/resources/ha", dependencies=[Depends(require_internal)])
async def list_ha_resources(user_id: str = "default", limit: int = 15):
    """Compact snapshot of the most recently indexed home devices.

    Lets the gateway render a small snapshot block listing the entities Raven
    can read state from or control, without dumping the hub's whole catalog
    into the prompt.
    """
    try:
        target_user = "default"
        rows = _conn().execute(
            "SELECT id, content, metadata, indexed_at FROM rag_items "
            "WHERE collection_name = ? AND user_id = ? AND id NOT LIKE 'sync_status:%' "
            "ORDER BY indexed_at DESC LIMIT ?",
            [HOME_DEVICES, target_user, limit],
        ).fetchall()
        entities = []
        for r in rows:
            try:
                meta = json.loads(r["metadata"])
            except Exception:
                meta = {}
            entities.append({
                "id": r["id"],
                "entity_id": meta.get("entity_id") or r["id"],
                "friendly_name": meta.get("friendly_name") or "",
                "state": meta.get("state") or "",
                "indexed_at": meta.get("indexed_at") or r["indexed_at"],
            })
        return {"status": "SUCCESS", "count": len(entities), "entities": entities}
    except Exception as e:
        log.error(f"Failed to list HA resources: {e}")
        return JSONResponse(status_code=500, content={"status": "ERROR", "message": str(e)})


# Section 6: New relational-vector collections + sync endpoints

@app.post("/rag/sync/missions", dependencies=[Depends(require_internal)])
async def sync_missions(payload: dict):
    """Ingest a completed agent-loop mission (post-mortem) for self-repair recall."""
    missions = payload.get("missions") or ([payload] if payload.get("mission_id") else [])
    if not missions:
        return {"status": "SUCCESS", "count": 0}

    now_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    user_id = payload.get("user_id", "default").lower()
    count = 0
    for m in missions:
        mission_id = m.get("mission_id") or m.get("id")
        if not mission_id:
            continue
        task = m.get("task_description", "")
        final_status = m.get("final_status", "UNKNOWN")
        error_summary = m.get("error_summary") or ""
        steps = m.get("steps") or m.get("steps_json") or []
        steps_json = json.dumps(steps) if not isinstance(steps, str) else steps
        content = f"Task: {task}\nStatus: {final_status}\nError: {error_summary}".strip()
        created_at = int(m.get("created_at", time.time()))

        _conn().execute(
            "INSERT OR REPLACE INTO mission_history"
            "(mission_id, task_description, final_status, error_summary, steps_json, "
            "content, user_id, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            [mission_id, task, final_status, error_summary, steps_json, content, user_id, created_at],
        )
        _add_item("mission_history", mission_id, user_id, content, {
            "mission_id": mission_id,
            "final_status": final_status,
            "user_id": user_id,
            "indexed_at": now_ts,
        }, created_at, now_ts)
        count += 1

    return {"status": "SUCCESS", "count": count}


@app.post("/rag/sync/conversations", dependencies=[Depends(require_internal)])
async def sync_conversations(payload: dict):
    """Ingest intercom voice transcripts for conversational memory."""
    utterances = payload.get("utterances") or []
    if not utterances:
        return {"status": "SUCCESS", "count": 0}

    now_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    user_id = payload.get("user_id", "default").lower()
    count = 0
    for u in utterances:
        utterance_id = u.get("utterance_id") or u.get("id") or str(hashlib.md5(
            f"{u.get('speaker','?')}:{u.get('text_content','')}:{u.get('timestamp',0)}".encode()
        ).hexdigest())
        speaker = u.get("speaker", "unknown")
        text_content = u.get("text_content", "")
        room_id = u.get("room_id", "unknown")
        timestamp = int(u.get("timestamp", time.time()))
        content = f"{speaker} in {room_id}: {text_content}"
        _conn().execute(
            "INSERT OR REPLACE INTO conversation_memory"
            "(utterance_id, speaker, text_content, room_id, content, user_id, timestamp) "
            "VALUES(?, ?, ?, ?, ?, ?, ?)",
            [utterance_id, speaker, text_content, room_id, content, user_id, timestamp],
        )
        _add_item("conversation_memory", utterance_id, user_id, content, {
            "speaker": speaker,
            "room_id": room_id,
            "user_id": user_id,
            "indexed_at": now_ts,
        }, timestamp, now_ts)
        count += 1

    return {"status": "SUCCESS", "count": count}


@app.post("/rag/sync/network", dependencies=[Depends(require_internal)])
async def sync_network(payload: dict):
    """Ingest Docker DNS/service-discovery topology for semantic resolution."""
    containers = payload.get("containers") or []
    if not containers:
        # Allow single-container payloads too.
        if payload.get("container_name"):
            containers = [payload]
        else:
            return {"status": "SUCCESS", "count": 0}

    now_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    user_id = payload.get("user_id", "default").lower()
    count = 0
    for c in containers:
        container_name = c.get("container_name") or c.get("name")
        if not container_name:
            continue
        ip_address = c.get("ip_address", "")
        exposed_ports = json.dumps(c.get("exposed_ports", c.get("ports", [])))
        discovered_services = json.dumps(c.get("discovered_services", c.get("services", [])))
        network_name = c.get("network_name", "")
        content = (
            f"Container {container_name} on network {network_name} at {ip_address} "
            f"exposes ports {exposed_ports} and services {discovered_services}"
        )
        _conn().execute(
            "INSERT OR REPLACE INTO network_topology"
            "(container_name, ip_address, exposed_ports, discovered_services, "
            "network_name, content, user_id) VALUES(?, ?, ?, ?, ?, ?, ?)",
            [container_name, ip_address, exposed_ports, discovered_services, network_name, content, user_id],
        )
        _add_item("network_topology", container_name, user_id, content, {
            "container_name": container_name,
            "ip_address": ip_address,
            "network_name": network_name,
            "user_id": user_id,
            "indexed_at": now_ts,
        }, int(time.time()), now_ts)
        count += 1

    return {"status": "SUCCESS", "count": count}


@app.post("/rag/sync/telemetry_alerts", dependencies=[Depends(require_internal)])
async def sync_telemetry_alerts(payload: dict):
    """Ingest processed semantic telemetry alerts so Jarvis can recall them."""
    alerts = payload.get("alerts") or ([payload] if payload.get("entity_id") else [])
    if not alerts:
        return {"status": "SUCCESS", "count": 0}

    now_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    user_id = payload.get("user_id", "default").lower()
    count = 0
    for a in alerts:
        entity_id = a.get("entity_id", "unknown")
        alert_type = a.get("alert_type", "generic")
        severity = a.get("severity", "info")
        text = a.get("content") or a.get("text") or a.get("alert_text") or (
            f"Telemetry Alert: {entity_id} {alert_type} ({severity})"
        )
        alert_id = a.get("alert_id") or f"alert:{entity_id}:{int(time.time()*1000)}"
        created_at = int(a.get("created_at", time.time()))
        _conn().execute(
            "INSERT OR REPLACE INTO telemetry_alerts"
            "(alert_id, entity_id, alert_type, severity, content, user_id, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?)",
            [alert_id, entity_id, alert_type, severity, text, user_id, created_at],
        )
        _add_item("telemetry_alerts", alert_id, user_id, text, {
            "entity_id": entity_id,
            "alert_type": alert_type,
            "severity": severity,
            "user_id": user_id,
            "indexed_at": now_ts,
        }, created_at, now_ts)
        count += 1

    return {"status": "SUCCESS", "count": count}


@app.get("/rag/mission/{mission_id}", dependencies=[Depends(require_internal)])
async def get_mission(mission_id: str):
    row = _conn().execute(
        "SELECT mission_id, task_description, final_status, error_summary, steps_json, created_at "
        "FROM mission_history WHERE mission_id = ?",
        [mission_id],
    ).fetchone()
    if not row:
        return {"status": "ERROR", "message": "Mission not found"}
    return {"status": "SUCCESS", "mission": dict(row)}


START_TIME = time.time()


# ---------------------------------------------------------------------------
# Self-Improvement Memory (SIM)
#
# System SIM is shared by every user and harness; User SIM is isolated to the
# exact caller. The gateway is the only caller: it forwards a caller's identity
# only after authentication, and it never crosses a user boundary. These
# endpoints are therefore internal-secret protected like every other RAG route.
# ---------------------------------------------------------------------------


class SimRecordRequest(BaseModel):
    scope: Literal["system", "user"] = "user"
    user_id: str = "default"
    harness: Literal["raven", "workspace", "satellite"]
    category: str = "general"
    signature: str = ""
    rationale: str = ""
    edit_or_rule: str = ""
    predicted_outcome: str = ""
    observed_outcome: str = ""
    outcome_status: Literal["pending", "improved", "neutral", "regressed"] = "pending"
    requires_admin: bool = False
    tags: list[str] = Field(default_factory=list)
    model: str = ""
    model_family: str = ""
    domain: str = ""
    round: int = 0


class SimQueryRequest(BaseModel):
    user_id: str = "default"
    harness: Literal["raven", "workspace", "satellite"] = "satellite"
    query: str = ""
    model: str = ""
    model_family: str = ""
    is_admin: bool = False
    k: int = 8


class SimOutcomeRequest(BaseModel):
    problem_id: str | None = None
    attempt_id: str | None = None
    observed_outcome: str = ""
    outcome_status: Literal["improved", "neutral", "regressed"]
    model: str = ""
    model_family: str = ""
    harness: str = ""
    domain: str = ""
    round: int = 0


@app.post("/rag/sim/record", dependencies=[Depends(require_internal)])
async def sim_record(req: SimRecordRequest):
    try:
        problem = sim_store.record(
            _conn(),
            **req.model_dump(),
            embed_fn=_embed_documents if embedder is not None else None,
        )
        return {"status": "SUCCESS", "problem": problem}
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/rag/sim/query", dependencies=[Depends(require_internal)])
async def sim_query(req: SimQueryRequest):
    try:
        query_embedding = None
        if embedder is not None and req.query.strip():
            # ONNX inference is CPU-bound; the ledger query itself is SQLite
            # and stays on the request thread (the connection is thread-affine).
            query_embedding = await asyncio.to_thread(_embed_query_vector, req.query)
        results = sim_store.query(
            _conn(),
            user_id=req.user_id,
            harness=req.harness,
            query=req.query,
            model=req.model,
            model_family=req.model_family,
            is_admin=req.is_admin,
            k=req.k,
            embed_fn=_embed_documents if embedder is not None else None,
            query_embedding=query_embedding,
        )
        return {"status": "SUCCESS", "count": len(results), "results": results}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/rag/sim/outcome", dependencies=[Depends(require_internal)])
async def sim_outcome(req: SimOutcomeRequest):
    try:
        problem = sim_store.record_outcome(_conn(), **req.model_dump())
        return {"status": "SUCCESS", "problem": problem}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/rag/sim/problems", dependencies=[Depends(require_internal)])
async def sim_problems(
    scope: Literal["system", "user"] | None = None,
    user_id: str | None = None,
    harness: Literal["raven", "workspace", "satellite"] | None = None,
    status: str | None = None,
    include_retired: bool = False,
    category: str | None = None,
    is_admin: bool = False,
    limit: int = Query(default=200, ge=1, le=1000),
):
    try:
        problems = sim_store.list_problems(
            _conn(),
            scope=scope,
            user_id=user_id,
            harness=harness,
            status=status,
            include_retired=include_retired,
            category=category,
            is_admin=is_admin,
            limit=limit,
        )
        return {"status": "SUCCESS", "count": len(problems), "problems": problems}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/rag/sim/history", dependencies=[Depends(require_internal)])
async def sim_history(
    user_id: str = "default",
    harness: Literal["raven", "workspace", "satellite"] | None = None,
    include_retired: bool = True,
    is_admin: bool = False,
    limit: int = Query(default=500, ge=1, le=2000),
):
    """Full history for proposer agents, including retired/regressed attempts."""
    try:
        return {
            "status": "SUCCESS",
            **sim_store.history(
                _conn(), user_id=user_id, harness=harness,
                include_retired=include_retired, is_admin=is_admin, limit=limit,
            ),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/rag/sim/consolidate", dependencies=[Depends(require_internal)])
async def sim_consolidate():
    try:
        report = sim_store.consolidate(_conn())
        return {"status": "SUCCESS", **report}
    except Exception as exc:  # pragma: no cover - defensive
        log.error(f"SIM consolidation failed: {exc}")
        raise HTTPException(status_code=500, detail="SIM consolidation failed") from exc



@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "rag",
        "git_sha": os.getenv("GIT_SHA", "unknown"),
        "start_time": START_TIME,
        "backend": BACKEND,
        "embedding_dim": EMBEDDING_DIM,
    }

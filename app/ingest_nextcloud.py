import os
import time
import json
import requests
import xml.etree.ElementTree as ET
import logging
import sys
import io
import tempfile
import shutil
from urllib.parse import urljoin, urlparse
from typing import Optional
import hashlib
import urllib3

# --- Text Extraction Libraries ---
try:
    from pypdf import PdfReader
    from docx import Document as DocxDocument
    from ebooklib import epub
    import mobi
    import html2text
except ImportError as e:
    print(
        f"ERROR: Missing required ingestion library: {e}. Please update requirements.txt and rebuild."
    )
    sys.exit(1)
# ---------------------------------

# Suppress the InsecureRequestWarning from 'requests' when using verify=False
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# LangChain and Chroma Imports
from langchain_core.documents import Document
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma

# --- Configuration for Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("NextcloudIngest")
# ---------------------------------

# Load .env for local runs
if os.getenv("DOCKER_ENV") != "1" and os.path.exists(".env"):
    from dotenv import load_dotenv

    load_dotenv(".env")

# --- Environment Variables ---
NEXTCLOUD_URL = os.getenv("NEXTCLOUD_URL")
NEXTCLOUD_USER = os.getenv("NEXTCLOUD_USER")
NEXTCLOUD_PASS = os.getenv("NEXTCLOUD_PASS")
CHROMA_DIR = os.getenv("CHROMA_PERSIST_DIR", "/data/chroma_db")
INGESTION_TEMP_DIR = os.getenv("INGESTION_TEMP_DIR", "/data/nextcloud_temp")

# --- Ingestion Configuration ---
PERSIST_FREQUENCY = 50
WHITELIST_EXT = [".txt", ".pdf", ".docx", ".epub", ".md", ".mobi"]
BLACKLIST_DIRS = ["Music", "Videos", "Photos", "Audio"]
BLACKLIST_FULL_PATHS = ["Books/Audio"]
# -----------------------------

if not NEXTCLOUD_URL or not NEXTCLOUD_USER or not NEXTCLOUD_PASS:
    logger.error("Nextcloud settings missing (NEXTCLOUD_URL, USER, or PASS)")
    sys.exit(1)

NAMESPACES = {"d": "DAV:"}
INGESTED_COUNT = 0


# ----------------------
# Text Extraction Helper Functions
# ----------------------
def extract_text_from_epub(file_path: str) -> Optional[str]:
    try:
        book = epub.read_epub(file_path)
        all_text = []
        for item in book.get_items():
            if item.get_type() == epub.ITEM_DOCUMENT:
                html_content = item.get_content()
                plain_text = html2text.html2text(html_content.decode("utf-8"))
                all_text.append(plain_text)
        return "\n\n".join(all_text)
    except Exception as e:
        logger.error("Failed to extract text from EPUB at %s: %s", file_path, e)
        return None


def extract_text_from_mobi(file_path: str) -> Optional[str]:
    tempdir = None
    try:
        tempdir, extracted_path = mobi.extract(file_path)
        if extracted_path:
            with open(extracted_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            return html2text.html2text(content)
        return None
    except Exception as e:
        logger.error("Failed to extract text from MOBI at %s: %s", file_path, e)
        return None
    finally:
        if tempdir and os.path.exists(tempdir):
            shutil.rmtree(tempdir)


def extract_text_content(file_path: str) -> Optional[str]:
    # Robustly strip, lower, and clean the extension from the file path
    ext = os.path.splitext(file_path)[1].lower().strip()

    # Check for empty extension and assume failure for now
    if not ext:
        # This should no longer be hit with the new temp file naming logic
        logger.warning(
            "File extension is empty or non-existent for extraction at %s.", file_path
        )
        return None

    if ext in [".txt", ".md"]:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()
        except:
            with open(file_path, "r", encoding="latin-1", errors="ignore") as f:
                return f.read()

    elif ext == ".pdf":
        try:
            reader = PdfReader(file_path)
            text = ""
            for page in reader.pages:
                text += page.extract_text() or ""
            return text
        except Exception as e:
            logger.error("Failed to extract text from PDF %s: %s", file_path, e)
            return None

    elif ext == ".docx":
        try:
            document = DocxDocument(file_path)
            return "\n".join([paragraph.text for paragraph in document.paragraphs])
        except Exception as e:
            logger.error("Failed to extract text from DOCX %s: %s", file_path, e)
            return None

    elif ext == ".epub":
        return extract_text_from_epub(file_path)

    elif ext == ".mobi":
        return extract_text_from_mobi(file_path)

    else:
        logger.warning("Unknown or unhandled extension %s for extraction.", ext)
        return None


# ----------------------
# File Listing (WebDAV)
# ----------------------


def list_files_webdav(base_url: str, all_found_files, seen=None):
    if seen is None:
        seen = set()

    try:
        # Use verify=False for metadata check
        resp = requests.request(
            "PROPFIND",
            base_url,
            auth=(NEXTCLOUD_USER, NEXTCLOUD_PASS),
            headers={"Depth": "1"},
            verify=False,
            timeout=(10, 20),
        )
        resp.raise_for_status()

        root = ET.fromstring(resp.content)
        current_level_files = []

        for r in root.findall("d:response", NAMESPACES):
            href = r.find("d:href", NAMESPACES)
            propstat = r.find("d:propstat", NAMESPACES)
            if href is None or propstat is None:
                continue

            full_path = href.text.strip("/")
            user_prefix = f"remote.php/dav/files/{NEXTCLOUD_USER}/"

            if full_path.startswith(user_prefix):
                relative_path = full_path[len(user_prefix) :].strip("/")
            else:
                relative_path = full_path.strip("/")

            current_relative_dir = (
                urlparse(base_url)
                .path.strip("/")
                .replace(user_prefix.strip("/"), "", 1)
                .strip("/")
            )

            if relative_path == "" or relative_path == current_relative_dir:
                continue

            is_collection = (
                propstat.find("d:prop/d:resourcetype/d:collection", NAMESPACES)
                is not None
            )

            if is_collection:
                dir_name = relative_path.split("/")[-1]

                if relative_path in BLACKLIST_FULL_PATHS:
                    logger.info("Skipping specific blacklisted path: %s", relative_path)
                    continue

                if dir_name in BLACKLIST_DIRS:
                    logger.info("Skipping globally blacklisted directory: %s", dir_name)
                    continue

                subdir_url = urljoin(
                    NEXTCLOUD_URL,
                    f"/remote.php/dav/files/{NEXTCLOUD_USER}/{relative_path}/",
                )
                logger.info(
                    "Entering directory: %s (WebDAV URL: %s)", relative_path, subdir_url
                )

                list_files_webdav(subdir_url, all_found_files, seen=seen)

            else:
                file_name = relative_path.split("/")[-1]
                if any(file_name.lower().endswith(ext) for ext in WHITELIST_EXT):
                    logger.info("Found file: %s", relative_path)
                    current_level_files.append(relative_path)

        all_found_files.extend(current_level_files)
        return all_found_files

    except Exception as e:
        logger.error("Error during PROPFIND/list_files_webdav at %s: %s", base_url, e)
        return all_found_files


# ----------------------
# Main ingestion function
# ----------------------
def ingest_nextcloud_files():
    """
    Fetches files, converts them via disk-streaming, and ingests them into Chroma one-by-one.
    Includes idempotence check, memory safety, and proper cleanup.
    """
    global INGESTED_COUNT
    base_url = urljoin(NEXTCLOUD_URL, f"/remote.php/dav/files/{NEXTCLOUD_USER}/")

    logger.info(
        "Starting Nextcloud ingestion process (PRODUCTION MODE: SSL VERIFICATION DISABLED)."
    )

    # --- CRITICAL: Ensure temp directory exists and is writable ---
    try:
        if not os.path.exists(INGESTION_TEMP_DIR):
            os.makedirs(INGESTION_TEMP_DIR)
            logger.info("Created temporary ingestion directory: %s", INGESTION_TEMP_DIR)
    except Exception as e:
        logger.error(
            "CRITICAL ERROR: Failed to create temporary directory %s. Check Docker volume permissions: %s",
            INGESTION_TEMP_DIR,
            e,
        )
        sys.exit(1)

    # 1. Initialize ChromaDB
    logger.info("Initializing Chroma store and embeddings...")
    embeddings = HuggingFaceEmbeddings(
        model_name="sentence-transformers/all-MiniLM-L6-v2"
    )
    vectordb = Chroma(
        collection_name="nextcloud_docs",
        embedding_function=embeddings,
        persist_directory=CHROMA_DIR,
    )

    # 2. File Discovery
    logger.info("Starting file discovery...")
    all_files_to_ingest = []
    list_files_webdav(base_url, all_files_to_ingest)
    files = all_files_to_ingest

    if not files:
        logger.warning("No files found for ingestion.")
        return

    logger.info(
        "Finished file discovery. Found a total of %d files to check/ingest.",
        len(files),
    )

    # 3. Process Files One-by-One
    for i, relative_path in enumerate(files, start=1):
        file_url = urljoin(
            NEXTCLOUD_URL, f"/remote.php/dav/files/{NEXTCLOUD_USER}/{relative_path}"
        )
        ext = os.path.splitext(relative_path)[1].lower()
        temp_file_path = None

        try:
            # --- Idempotence Check ---
            doc_id = hashlib.sha256(relative_path.encode()).hexdigest()

            existing_docs = vectordb._collection.get(ids=[doc_id], include=[])
            if existing_docs["ids"]:
                logger.info(
                    "[%d/%d] Skipping: %s - Already ingested.",
                    i,
                    len(files),
                    relative_path,
                )
                continue

            logger.info(
                "[%d/%d] Ingesting: %s (Type: %s)",
                i,
                len(files),
                relative_path,
                ext.upper().strip("."),
            )

            # --- Stream Download to Disk (Memory Safety) ---

            # CRITICAL FIX: Generate a temp file name that includes the unique ID and the file's original extension.
            # This ensures extraction libraries can identify the file type correctly.
            unique_id_prefix = hashlib.sha256(os.urandom(16)).hexdigest()[:8]
            file_name_with_ext = os.path.basename(relative_path)
            temp_file_path = os.path.join(
                INGESTION_TEMP_DIR, f"{unique_id_prefix}_{file_name_with_ext}"
            )

            # Stream the download directly to the path with the extension
            # SSL FIX: Added verify=False
            with requests.get(
                file_url,
                auth=(NEXTCLOUD_USER, NEXTCLOUD_PASS),
                stream=True,
                verify=False,
                timeout=(30, 180),
            ) as r:
                r.raise_for_status()
                with open(temp_file_path, "wb") as f:
                    # Chunk the file to disk, avoiding memory load
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)

            # Re-check file size after download: If it's still 0, raise an error immediately
            file_size = os.path.getsize(temp_file_path)
            if file_size == 0:
                raise IOError(
                    f"Downloaded file {relative_path} is 0 bytes. Check Nextcloud permissions/WebDAV status."
                )

            logger.info("DEBUG: Download successful. File size: %s bytes.", file_size)

            # --- Conversion/Extraction from Disk ---
            # This now works because temp_file_path has the correct extension!
            text_content = extract_text_content(temp_file_path)

            if not text_content or not text_content.strip():
                logger.warning(
                    "Extracted content empty or failed for file: %s", relative_path
                )
                continue

            # --- Ingestion to ChromaDB ---
            doc = Document(
                page_content=text_content,
                metadata={
                    "source": "nextcloud",
                    "path": relative_path,
                    "type": ext.strip("."),
                },
            )

            logger.info(
                "DEBUG: Prepared document with ID %s. Content length: %d. Attempting add to Chroma...",
                doc_id,
                len(doc.page_content),
            )

            vectordb.add_documents(documents=[doc], ids=[doc_id])
            INGESTED_COUNT += 1
            logger.info(
                "SUCCESS: Document for %s added and indexed. Total added in this run: %d",
                relative_path,
                INGESTED_COUNT,
            )

            # --- Persistence/Cleanup Frequency ---
            if INGESTED_COUNT % PERSIST_FREQUENCY == 0:
                vectordb.persist()
                logger.info("Persistence checkpoint reached. Saved data to disk.")

        except requests.exceptions.Timeout:
            logger.error("Timeout fetching %s", relative_path)
        except requests.exceptions.HTTPError as e:
            logger.error("HTTP %s fetching %s", e.response.status_code, relative_path)
        except IOError as e:
            # Handle the specific 0-byte file check error
            logger.error("IO Error: %s", e)
        except Exception as e:
            # Log the full traceback for any other unexpected error
            logger.error(
                "Unexpected error processing %s: %s", relative_path, e, exc_info=True
            )
        finally:
            # --- CRITICAL CLEANUP ---
            if temp_file_path and os.path.exists(temp_file_path):
                os.remove(temp_file_path)  # Delete the downloaded file immediately

    # --- Final Persistence ---
    vectordb.persist()
    logger.info(
        "Nextcloud ingestion complete. Total Documents added in this run: %d. Final document count: %d",
        INGESTED_COUNT,
        vectordb._collection.count(),
    )


# ----------------------
# CLI execution
# ----------------------
if __name__ == "__main__":
    ingest_nextcloud_files()

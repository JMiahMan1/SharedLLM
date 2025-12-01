# app/ingest_nextcloud.py
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
from typing import Optional, List, Tuple, Dict, Set
import hashlib
import urllib3

# --- Text Extraction Libraries ---
try:
    from pypdf import PdfReader
    from docx import Document as DocxDocument
    from ebooklib import epub
    import ebooklib
    import mobi
    import html2text
    import openpyxl
except ImportError as e:
    print(f"ERROR: Missing required ingestion library: {e}")
    sys.exit(1)

# Suppress SSL Warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# LangChain and Chroma Imports
try:
    from langchain_core.documents import Document
    from langchain_huggingface import HuggingFaceEmbeddings
    from langchain_chroma import Chroma
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError as e:
    print(f"CRITICAL: Missing AI dependencies: {e}")
    sys.exit(1)

# --- Configuration ---
if os.getenv("DOCKER_ENV") != "1" and os.path.exists(".env"):
    from dotenv import load_dotenv
    load_dotenv(".env")

NEXTCLOUD_URL = os.getenv("NEXTCLOUD_URL")
NEXTCLOUD_USER = os.getenv("NEXTCLOUD_USER")
NEXTCLOUD_PASS = os.getenv("NEXTCLOUD_PASS")
CHROMA_DIR = os.getenv("CHROMA_PERSIST_DIR", "/data/chroma_db")
INGESTION_TEMP_DIR = os.getenv("INGESTION_TEMP_DIR", "/data/nextcloud_temp")
LOG_FILE_PATH = "/data/nextcloud_ingest.log"

# Logic Configuration
PERSIST_FREQUENCY = 20  

# File Categorization
TEXT_EXTS = [".txt", ".pdf", ".docx", ".epub", ".md", ".mobi", ".csv", ".xlsx", ".json"]

MEDIA_EXTS = [".mp3", ".m4b", ".mp4", ".mkv", ".avi", ".flac", ".wav", ".mov", ".webm", ".ogg"]

# RESTORED Access to Backup Folders
BLACKLIST_DIRS = [
    "Music", "Videos", "Photos", "Audio", "Thumbnails", "Preview", "Cache", "AppData", "Templates"
]
BLACKLIST_FULL_PATHS = ["Books/Audio"]

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE_PATH)
    ],
    force=True
)
logger = logging.getLogger("NextcloudSync")

if not NEXTCLOUD_URL or not NEXTCLOUD_USER or not NEXTCLOUD_PASS:
    logger.error("Nextcloud settings missing (NEXTCLOUD_URL, USER, or PASS)")
    sys.exit(1)

NAMESPACES = {"d": "DAV:"}

# ----------------------
# Helper Functions
# ----------------------

def get_file_category(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    if ext in TEXT_EXTS: return "text"
    if ext in MEDIA_EXTS: return "media"
    return "unknown"

def extract_text_from_epub(file_path: str) -> Optional[str]:
    try:
        book = epub.read_epub(file_path)
        all_text = []
        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_DOCUMENT:
                html_content = item.get_content()
                plain_text = html2text.html2text(html_content.decode("utf-8"))
                all_text.append(plain_text)
        return "\n\n".join(all_text)
    except Exception as e:
        logger.error(f"Failed to extract text from EPUB at {file_path}: {e}")
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
        logger.error(f"Failed to extract text from MOBI at {file_path}: {e}")
        return None
    finally:
        if tempdir and os.path.exists(tempdir):
            shutil.rmtree(tempdir)

def extract_text_from_xlsx(file_path: str) -> Optional[str]:
    try:
        wb = openpyxl.load_workbook(file_path, data_only=True)
        all_text = []
        for sheet_name in wb.sheetnames:
            sheet = wb[sheet_name]
            all_text.append(f"--- Sheet: {sheet_name} ---")
            for row in sheet.iter_rows(values_only=True):
                # Join non-None cells with spaces
                row_text = " ".join([str(cell) for cell in row if cell is not None])
                if row_text.strip():
                    all_text.append(row_text)
        return "\n".join(all_text)
    except Exception as e:
        logger.error(f"Failed to extract text from XLSX at {file_path}: {e}")
        return None

def extract_text_content(file_path: str) -> Optional[str]:
    ext = os.path.splitext(file_path)[1].lower().strip()
    if not ext: return None

    try:
        if ext in [".txt", ".md", ".csv", ".json"]:
            try:
                with open(file_path, "r", encoding="utf-8") as f: return f.read()
            except UnicodeDecodeError:
                with open(file_path, "r", encoding="latin-1", errors="ignore") as f: return f.read()
        elif ext == ".pdf":
            reader = PdfReader(file_path)
            return "\n".join([page.extract_text() or "" for page in reader.pages])
        elif ext == ".docx":
            document = DocxDocument(file_path)
            return "\n".join([paragraph.text for paragraph in document.paragraphs])
        elif ext == ".xlsx":
            return extract_text_from_xlsx(file_path)
        elif ext == ".epub":
            return extract_text_from_epub(file_path)
        elif ext == ".mobi":
            return extract_text_from_mobi(file_path)
            
    except Exception as e:
        logger.error(f"Extraction error on {file_path}: {e}")
    
    return None

def list_files_webdav_iterative(start_url: str, found_files: Dict[str, Dict]):
    """
    Iteratively scans Nextcloud and populates found_files dict.
    Includes RETRY logic for timeouts on large folders.
    """
    stack = [start_url]
    visited = set()

    logger.info(f"Starting Iterative WebDAV scan from: {start_url}")

    while stack:
        current_url = stack.pop()
        if current_url in visited: continue
        visited.add(current_url)

        # --- RETRY LOOP FOR TIMEOUTS ---
        success = False
        content = None
        
        for attempt in range(3):
            try:
                # Increased timeout to 600s (10 minutes) for massive directories
                resp = requests.request(
                    "PROPFIND", current_url, 
                    auth=(NEXTCLOUD_USER, NEXTCLOUD_PASS), 
                    headers={"Depth": "1"}, 
                    verify=False, timeout=3600
                )
                
                if resp.status_code == 404:
                    logger.warning(f"Path not found: {current_url}")
                    success = True # Handled, stop retrying
                    break
                
                if resp.status_code == 207:
                    content = resp.content
                    success = True
                    break
                
                logger.warning(f"WebDAV error {resp.status_code} at {current_url} (Attempt {attempt+1}/3)")
                time.sleep(5)

            except requests.exceptions.ReadTimeout:
                logger.warning(f"Read Timeout at {current_url} (Attempt {attempt+1}/3). Retrying...")
                time.sleep(10)
            except Exception as e:
                logger.error(f"WebDAV Connection Error at {current_url}: {e}")
                time.sleep(5)

        if not success or not content:
            logger.error(f"Skipping {current_url} after repeated failures.")
            continue
        # -------------------------------

        try:
            root = ET.fromstring(content)
            
            for r in root.findall("d:response", NAMESPACES):
                href = r.find("d:href", NAMESPACES).text
                prop = r.find("d:propstat/d:prop", NAMESPACES)
                if not href or not prop: continue
                
                etag_node = prop.find("d:getetag", NAMESPACES)
                etag = etag_node.text.strip('"') if etag_node is not None else "unknown"
                
                # Decode and normalize path
                href_decoded = requests.utils.unquote(href)
                prefix = f"/remote.php/dav/files/{NEXTCLOUD_USER}/"
                if prefix not in href_decoded: continue
                
                rel_path = href_decoded.split(prefix, 1)[1]
                item_url = urljoin(NEXTCLOUD_URL, href)
                
                # Skip self
                if item_url.rstrip('/') == current_url.rstrip('/'): continue

                is_collection = prop.find("d:resourcetype/d:collection", NAMESPACES) is not None
                
                if is_collection:
                    dir_name = rel_path.strip("/").split("/")[-1]
                    if rel_path.strip("/") in BLACKLIST_FULL_PATHS: continue
                    if dir_name in BLACKLIST_DIRS: continue
                    
                    if not item_url.endswith("/"): item_url += "/"
                    stack.append(item_url)
                else:
                    cat = get_file_category(rel_path)
                    if cat != "unknown":
                        found_files[rel_path] = {"etag": etag, "category": cat}
                        
        except Exception as e:
            logger.error(f"XML Parsing Error at {current_url}: {e}")

# ----------------------
# Synchronization Logic
# ----------------------
def get_db_state(vectordb) -> Dict[str, str]:
    """
    Fetches all documents from Chroma to build a current state map.
    """
    logger.info("Fetching current database state (this may take a moment)...")
    try:
        results = vectordb.get(include=["metadatas"])
        state = {}
        for meta in results["metadatas"]:
            if meta and "source" in meta:
                etag = meta.get("etag", "unknown")
                state[meta["source"]] = etag
        return state
    except Exception as e:
        logger.error(f"Failed to fetch DB state: {e}")
        return {}

def sync_nextcloud_files():
    if not os.path.exists(INGESTION_TEMP_DIR): os.makedirs(INGESTION_TEMP_DIR)

    logger.info(f"--- Starting Rsync-Style Nextcloud Ingestion ---")
    logger.info(f"Logs: {LOG_FILE_PATH}")

    # 1. Initialize DB
    embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
    vectordb = Chroma(
        collection_name="nextcloud_docs", 
        embedding_function=embeddings, 
        persist_directory=CHROMA_DIR
    )

    # 2. Build State Maps
    db_state = get_db_state(vectordb)
    logger.info(f"Database currently holds chunks for {len(set(db_state.keys()))} unique files.")

    nc_state = {}
    root_url = urljoin(NEXTCLOUD_URL, f"/remote.php/dav/files/{NEXTCLOUD_USER}/")
    logger.info("Scanning Nextcloud file tree (Iterative)...")
    
    list_files_webdav_iterative(root_url, nc_state)
    
    logger.info(f"Nextcloud scan complete. Found {len(nc_state)} candidate files.")

    # 3. Calculate Diff
    to_delete = []
    to_ingest = [] 

    # Detect Deletions
    for path in db_state:
        if path not in nc_state:
            to_delete.append(path)

    # Detect Adds and Updates
    for path, info in nc_state.items():
        nc_etag = info["etag"]
        db_etag = db_state.get(path)

        if db_etag is None:
            to_ingest.append((path, info["category"], nc_etag))
        elif db_etag != nc_etag:
            logger.info(f"File changed: {path}")
            to_delete.append(path)
            to_ingest.append((path, info["category"], nc_etag))
    
    to_delete = list(set(to_delete))

    logger.info(f"Sync Plan: {len(to_delete)} to delete, {len(to_ingest)} to ingest/update.")

    # 4. Execute Deletions
    if to_delete:
        logger.info(f"Processing {len(to_delete)} deletions...")
        for path in to_delete:
            try:
                vectordb._collection.delete(where={"source": path})
                logger.info(f"Deleted data for: {path}")
            except Exception as e:
                logger.error(f"Failed to delete {path}: {e}")

    # 5. Execute Ingestion
    if not to_ingest:
        logger.info("No new or modified files to ingest. Sync complete.")
        return

    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1200, chunk_overlap=200, length_function=len)
    
    processed_count = 0
    
    for i, (rel_path, category, etag) in enumerate(to_ingest, start=1):
        doc_id_base = hashlib.sha256(rel_path.encode()).hexdigest()
        
        # --- MEDIA PATH (Metadata Only) ---
        if category == "media":
            try:
                fname = os.path.basename(rel_path)
                logger.info(f"[{i}/{len(to_ingest)}] Indexing Media: {fname}")
                
                meta_doc = Document(
                    page_content=f"Media File: {fname}\nPath: {rel_path}\nType: {category}\nSource: Nextcloud",
                    metadata={
                        "source": rel_path, 
                        "filename": fname,
                        "type": "media",
                        "category": "audio_video",
                        "etag": etag,
                        "chunk": 0
                    }
                )
                vectordb.add_documents([meta_doc])
                processed_count += 1
            except Exception as e:
                logger.error(f"Failed media index {rel_path}: {e}")

        # --- TEXT PATH (Download & Chunk) ---
        elif category == "text":
            logger.info(f"[{i}/{len(to_ingest)}] Processing Text: {rel_path}")
            
            encoded_path = requests.utils.quote(rel_path)
            file_url = urljoin(NEXTCLOUD_URL, f"/remote.php/dav/files/{NEXTCLOUD_USER}/{encoded_path}")
            temp_filename = f"{doc_id_base}_{os.path.basename(rel_path)}"
            temp_path = os.path.join(INGESTION_TEMP_DIR, temp_filename)
            
            try:
                # 120s timeout -> Increased to 600s for large downloads
                with requests.get(file_url, auth=(NEXTCLOUD_USER, NEXTCLOUD_PASS), stream=True, verify=False, timeout=3600) as r:
                    r.raise_for_status()
                    with open(temp_path, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            f.write(chunk)
                
                content = extract_text_content(temp_path)
                
                if content and len(content.strip()) > 50:
                    chunks = text_splitter.split_text(content)
                    docs_to_add = []
                    for idx, chunk_text in enumerate(chunks):
                        docs_to_add.append(Document(
                            page_content=chunk_text,
                            metadata={
                                "source": rel_path,
                                "filename": os.path.basename(rel_path),
                                "type": "document",
                                "chunk_index": idx,
                                "total_chunks": len(chunks),
                                "etag": etag
                            }
                        ))
                    
                    if docs_to_add:
                        vectordb.add_documents(docs_to_add)
                        processed_count += 1
                        logger.info(f"-> Ingested {len(chunks)} chunks.")
                else:
                    logger.warning(f"Skipping empty/unreadable text: {rel_path}")

            except Exception as e:
                logger.error(f"Error processing {rel_path}: {e}")
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)

        # --- Periodic Persist ---
        if processed_count % PERSIST_FREQUENCY == 0:
            try:
                if hasattr(vectordb, 'persist'): 
                    vectordb.persist()
                    logger.info(f"Checkpoint saved. ({processed_count} items processed)")
            except: pass

    # Final Save
    try:
        if hasattr(vectordb, 'persist'): vectordb.persist()
    except: pass
    
    logger.info(f"Sync Run Complete. Updated/Added: {processed_count}. Deleted: {len(to_delete)}.")

if __name__ == "__main__":
    sync_nextcloud_files()

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
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- Text Extraction Libraries ---
try:
    import fitz  # PyMuPDF (Handles PDF & EPUB fast)
    from docx import Document as DocxDocument
    import mobi
    import html2text
    import openpyxl
    import pandas as pd # Added for Data Analyst Schema Extraction
except ImportError as e:
    print(f"ERROR: Missing required ingestion library: {e}")
    sys.exit(1)

# Optional: Check for cryptography
try:
    import cryptography
except ImportError:
    pass 

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
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "qwen2.5:latest")

CHROMA_DIR = os.getenv("CHROMA_PERSIST_DIR", "/data/chroma_db")
INGESTION_TEMP_DIR = os.getenv("INGESTION_TEMP_DIR", "/data/nextcloud_temp")
LOG_FILE_PATH = "/data/nextcloud_ingest.log"
TOC_CACHE_FILE = "/data/nextcloud_toc_cache.json"

# Logic Configuration
PERSIST_FREQUENCY = 50
MAX_WORKERS = 8  # 8 Parallel Threads
TOC_CACHE_TTL = 86400 # 24 Hours
MIN_TEXT_LENGTH = 10 # Lowered from 50 to 10 to catch short notes/readmes

# File Categorization
SPREADSHEET_EXTS = [".csv", ".xlsx", ".json", ".xls"]
BOOK_EXTS = [".epub", ".mobi", ".pdf"] 
TEXT_EXTS = [".txt", ".md", ".docx"]
MEDIA_EXTS = [".mp3", ".m4b", ".mp4", ".mkv", ".avi", ".flac", ".wav", ".mov", ".webm", ".ogg"]

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
# AI Helper Functions
# ----------------------

def generate_summary(text: str) -> str:
    """Generates a high-level summary using the local LLM."""
    if not OLLAMA_URL: return ""
    trunc_text = text[:10000] # Safe limit
    prompt = f"Summarize the following document in 3-4 sentences. Capture the main topic, key themes, and purpose.\n\nDocument:\n{trunc_text}\n\nSummary:"
    
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": DEFAULT_MODEL, "prompt": prompt, "stream": False, "options": {"temperature": 0.0}},
            timeout=60
        )
        if resp.status_code == 200:
            return resp.json().get("response", "").strip()
    except Exception as e:
        logger.warning(f"Summary generation failed: {e}")
    return ""

def extract_spreadsheet_schema(file_path: str, filename: str) -> str:
    """
    Reads a spreadsheet and creates a rich text description of its structure.
    """
    try:
        df = None
        ext = os.path.splitext(filename)[1].lower()
        
        # Load Data
        if ext == ".csv":
            df = pd.read_csv(file_path, nrows=5) # Peek
            # Count rows efficiently
            total_rows = sum(1 for _ in open(file_path, 'r', encoding='utf-8', errors='ignore')) - 1
        elif ext in [".xlsx", ".xls"]:
            df = pd.read_excel(file_path, nrows=5)
            total_rows = "Unknown (Excel)" 
        elif ext == ".json":
            df = pd.read_json(file_path)
            total_rows = len(df)
            df = df.head(5)

        if df is None: return ""

        # Build Description
        columns = ", ".join(df.columns.tolist())
        sample = df.to_string(index=False, max_rows=3)
        
        description = (
            f"DATA FILE: {filename}\n"
            f"TYPE: Spreadsheet/Structured Data\n"
            f"COLUMNS: {columns}\n"
            f"TOTAL ROWS: ~{total_rows}\n"
            f"SAMPLE DATA:\n{sample}\n"
            f"USAGE: Use this file for budget, finance, or data analysis queries. "
            f"File available in Nextcloud at original path."
        )
        return description

    except Exception as e:
        logger.warning(f"Failed to extract schema for {filename}: {e}")
        return f"DATA FILE: {filename}. Could not parse structure automatically."

# ----------------------
# Helper Functions
# ----------------------

def get_file_category(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    if ext in SPREADSHEET_EXTS: return "spreadsheet"
    if ext in BOOK_EXTS: return "book"
    if ext in TEXT_EXTS: return "text"
    if ext in MEDIA_EXTS: return "media"
    return "unknown"

def extract_text_from_mobi(file_path: str) -> Optional[str]:
    """Legacy helper for MOBI files."""
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
    # Fallback for text extraction if schema fails or if simpler text needed
    try:
        wb = openpyxl.load_workbook(file_path, data_only=True)
        all_text = []
        for sheet_name in wb.sheetnames:
            sheet = wb[sheet_name]
            all_text.append(f"--- Sheet: {sheet_name} ---")
            for row in sheet.iter_rows(values_only=True):
                row_text = " ".join([str(cell) for cell in row if cell is not None])
                if row_text.strip():
                    all_text.append(row_text)
        return "\n".join(all_text)
    except Exception as e:
        logger.error(f"Failed to extract text from XLSX at {file_path}: {e}")
        return None

def extract_text_content(file_path: str) -> Optional[str]:
    """
    Universal extractor using FAST PyMuPDF for PDF/EPUB and fallbacks for others.
    """
    ext = os.path.splitext(file_path)[1].lower().strip()
    if not ext: return None

    try:
        # --- FAST PATH: PyMuPDF (PDF & EPUB) ---
        if ext in [".pdf", ".epub"]:
            text = ""
            with fitz.open(file_path) as doc:
                for page in doc:
                    text += page.get_text()
            return text

        # --- STANDARD PATH: Other Formats ---
        elif ext in [".txt", ".md", ".csv", ".json"]:
            try:
                with open(file_path, "r", encoding="utf-8") as f: return f.read()
            except UnicodeDecodeError:
                with open(file_path, "r", encoding="latin-1", errors="ignore") as f: return f.read()
        
        # --- IMPROVED DOCX EXTRACTION (Tables + Paragraphs) ---
        elif ext == ".docx":
            document = DocxDocument(file_path)
            content_pieces = []
            
            # 1. Paragraphs
            for paragraph in document.paragraphs:
                if paragraph.text.strip():
                    content_pieces.append(paragraph.text)
            
            # 2. Tables (Crucial for forms/curriculum)
            for table in document.tables:
                for row in table.rows:
                    row_text = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
                    if row_text:
                        content_pieces.append(row_text)
            
            return "\n".join(content_pieces)
            
        elif ext == ".xlsx":
            return extract_text_from_xlsx(file_path)
            
        elif ext == ".mobi":
            return extract_text_from_mobi(file_path)
            
    except Exception as e:
        err_str = str(e).lower()
        if "cryptography" in err_str and "required for aes" in err_str:
             logger.warning(f"Missing 'cryptography' library for PDF: {file_path}")
        else:
             logger.error(f"Extraction error on {file_path}: {e}")
    
    return None

# ----------------------
# Parallel Scanning Logic
# ----------------------

def scan_single_folder(current_url: str) -> Tuple[List[str], Dict[str, Dict]]:
    """
    Worker function to scan a single folder. 
    Returns: (list_of_subdirs, dict_of_files_found)
    """
    subdirs = []
    files_found = {}
    
    propfind_data = """<?xml version="1.0" encoding="utf-8" ?>
    <d:propfind xmlns:d="DAV:">
      <d:prop>
        <d:getetag/>
        <d:resourcetype/>
        <d:getcontentlength/>
      </d:prop>
    </d:propfind>"""

    for attempt in range(3):
        try:
            resp = requests.request(
                "PROPFIND", current_url, 
                auth=(NEXTCLOUD_USER, NEXTCLOUD_PASS), 
                headers={"Depth": "1", "Content-Type": "application/xml"}, 
                data=propfind_data,
                verify=False, timeout=900
            )
            
            if resp.status_code == 404: return [], {}
            
            if resp.status_code == 207:
                root = ET.fromstring(resp.content)
                for r in root.findall("d:response", NAMESPACES):
                    href = r.find("d:href", NAMESPACES).text
                    prop = r.find("d:propstat/d:prop", NAMESPACES)
                    if not href or not prop: continue
                    
                    etag = prop.find("d:getetag", NAMESPACES).text.strip('"') if prop.find("d:getetag", NAMESPACES) is not None else "unknown"
                    
                    size = "0"
                    size_node = prop.find("d:getcontentlength", NAMESPACES)
                    if size_node is not None:
                        size = size_node.text

                    href_decoded = requests.utils.unquote(href)
                    prefix = f"/remote.php/dav/files/{NEXTCLOUD_USER}/"
                    
                    if prefix not in href_decoded: continue
                    rel_path = href_decoded.split(prefix, 1)[1]
                    item_url = urljoin(NEXTCLOUD_URL, href)
                    
                    if item_url.rstrip('/') == current_url.rstrip('/'): continue

                    is_collection = prop.find("d:resourcetype/d:collection", NAMESPACES) is not None
                    
                    if is_collection:
                        dir_name = rel_path.strip("/").split("/")[-1]
                        if rel_path.strip("/") not in BLACKLIST_FULL_PATHS and dir_name not in BLACKLIST_DIRS:
                            if not item_url.endswith("/"): item_url += "/"
                            subdirs.append(item_url)
                    else:
                        cat = get_file_category(rel_path)
                        if cat != "unknown":
                            files_found[rel_path] = {
                                "etag": etag, 
                                "category": cat,
                                "size": size
                            }
                
                return subdirs, files_found
            
            time.sleep(5)
        except Exception as e:
            logger.warning(f"Scan error {current_url}: {e}")
            time.sleep(5)
            
    return [], {}

def list_files_parallel(start_url: str, nc_state: Dict[str, Dict]):
    """
    Manages a thread pool to scan directories in parallel.
    """
    logger.info(f"Starting Parallel WebDAV scan ({MAX_WORKERS} threads)...")
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_url = {executor.submit(scan_single_folder, start_url): start_url}
        
        while future_to_url:
            done = [f for f in future_to_url if f.done()]
            
            for f in done:
                url = future_to_url.pop(f)
                try:
                    subdirs, files = f.result()
                    nc_state.update(files)
                    for sub in subdirs:
                        future_to_url[executor.submit(scan_single_folder, sub)] = sub
                except Exception as e:
                    logger.error(f"Thread error scanning {url}: {e}")
            
            if not done:
                time.sleep(0.1)

# ----------------------
# TOC Caching Logic
# ----------------------
def load_toc_cache() -> Optional[Dict[str, Dict]]:
    if not os.path.exists(TOC_CACHE_FILE): return None
    try:
        with open(TOC_CACHE_FILE, 'r') as f:
            data = json.load(f)
        
        if time.time() - data.get('timestamp', 0) > TOC_CACHE_TTL:
            logger.info("TOC Cache expired.")
            return None
            
        files = data.get('files', {})
        logger.info(f"Loaded TOC Cache ({len(files)} files) from {time.ctime(data['timestamp'])}")
        return files
    except Exception as e:
        logger.error(f"Failed to load TOC cache: {e}")
        return None

def save_toc_cache(files: Dict[str, Dict]):
    try:
        data = {"timestamp": time.time(), "files": files}
        with open(TOC_CACHE_FILE, 'w') as f:
            json.dump(data, f)
        logger.info("Saved TOC Cache to disk.")
    except Exception as e:
        logger.error(f"Failed to save TOC cache: {e}")

# ----------------------
# Synchronization Logic
# ----------------------
def get_db_state(vectordb) -> Dict[str, Dict]:
    logger.info("Fetching current database state...")
    try:
        results = vectordb.get(include=["metadatas"])
        state = {}
        for meta in results["metadatas"]:
            if meta and "source" in meta:
                # Capture Type for Retrofitting Check
                state[meta["source"]] = {
                    "etag": meta.get("etag", "unknown"),
                    "type": meta.get("type", "unknown")
                }
        return state
    except Exception as e:
        logger.error(f"Failed to fetch DB state: {e}")
        return {}

def sync_nextcloud_files():
    if not os.path.exists(INGESTION_TEMP_DIR): os.makedirs(INGESTION_TEMP_DIR)
    
    logger.info(f"--- Starting Parallel Nextcloud Ingestion (Smart & Optimized) ---")
    logger.info(f"Logs: {LOG_FILE_PATH}")

    embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
    vectordb = Chroma(
        collection_name="nextcloud_docs", 
        embedding_function=embeddings, 
        persist_directory=CHROMA_DIR
    )

    # 1. Load DB State
    db_state = get_db_state(vectordb)
    logger.info(f"Database holds {len(db_state)} files.")

    # 2. Get Nextcloud State (Try Cache First)
    nc_state = load_toc_cache()
    
    if not nc_state:
        nc_state = {}
        root_url = urljoin(NEXTCLOUD_URL, f"/remote.php/dav/files/{NEXTCLOUD_USER}/")
        logger.info("Cache missing/expired. Starting parallel scan...")
        list_files_parallel(root_url, nc_state)
        save_toc_cache(nc_state)
        logger.info(f"Nextcloud scan complete. Found {len(nc_state)} files.")
    
    # 3. Diff & Retrofit Logic
    to_ingest = []
    
    # Identify deleted files
    files_to_remove = [path for path in db_state if path not in nc_state]
    if files_to_remove:
        logger.info(f"Cleaning up {len(files_to_remove)} deleted files from DB...")
        for path in files_to_remove:
            try: vectordb._collection.delete(where={"source": path})
            except: pass

    # Identify New, Modified, or Legacy files
    for path, info in nc_state.items():
        db_record = db_state.get(path)
        should_process = False
        
        if not db_record:
            should_process = True
        elif db_record["etag"] != info["etag"]:
            should_process = True
        else:
            # --- RETROFIT CHECK ---
            current_type = db_record["type"]
            expected_category = info["category"]
            
            # If Spreadsheet is "document", upgrade it to "spreadsheet_metadata"
            if expected_category == "spreadsheet" and current_type != "spreadsheet_metadata":
                should_process = True
            # If Book is "document", upgrade it to "book_summary"
            elif expected_category == "book" and current_type != "book_summary":
                should_process = True

        if should_process:
            to_ingest.append((path, info["category"], info["etag"], info.get("size", "0")))

    logger.info(f"Sync Plan: {len(to_ingest)} files to ingest/update.")

    if not to_ingest:
        logger.info("Database is up to date. Sync complete.")
    else:
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=1200, chunk_overlap=200, length_function=len)
        processed_count = 0
        
        for i, (rel_path, category, etag, size) in enumerate(to_ingest, start=1):
            
            # Atomic overwrite protection
            try:
                vectordb._collection.delete(where={"source": rel_path})
            except: pass

            doc_id_base = hashlib.sha256(rel_path.encode()).hexdigest()
            fname = os.path.basename(rel_path)
            
            # Metadata Payload
            common_metadata = {
                "source": rel_path,
                "filename": fname,
                "etag": etag,
                "size": int(size),
                "source_server": NEXTCLOUD_URL,
                "source_user": NEXTCLOUD_USER
            }

            # --- ROUTE 1: SPREADSHEETS (No Persistence) ---
            if category == "spreadsheet":
                logger.info(f"Processing SPREADSHEET Schema: {fname}")
                temp_path = os.path.join(INGESTION_TEMP_DIR, f"{doc_id_base}_{fname}")
                encoded_path = requests.utils.quote(rel_path)
                file_url = urljoin(NEXTCLOUD_URL, f"/remote.php/dav/files/{NEXTCLOUD_USER}/{encoded_path}")
                
                try:
                    with requests.get(file_url, auth=(NEXTCLOUD_USER, NEXTCLOUD_PASS), stream=True, verify=False, timeout=600) as r:
                        r.raise_for_status()
                        with open(temp_path, 'wb') as f:
                            for chunk in r.iter_content(chunk_size=8192): f.write(chunk)
                    
                    schema = extract_spreadsheet_schema(temp_path, fname)
                    vectordb.add_documents([Document(
                        page_content=schema, 
                        metadata={**common_metadata, "type": "spreadsheet_metadata", "is_remote": True}
                    )])
                    processed_count += 1
                except Exception as e:
                    logger.error(f"Error processing spreadsheet {fname}: {e}")
                finally:
                    if os.path.exists(temp_path): os.remove(temp_path)
                
                continue

            # --- ROUTE 2: MEDIA ---
            if category == "media":
                try:
                    meta_doc = Document(
                        page_content=f"Media File: {fname}\nPath: {rel_path}\nSize: {size} bytes",
                        metadata={**common_metadata, "type": "media", "category": "audio_video", "chunk": 0}
                    )
                    vectordb.add_documents([meta_doc])
                    processed_count += 1
                except Exception: pass
                continue

            # --- ROUTE 3: TEXT & BOOKS ---
            if category == "text" or category == "book":
                logger.info(f"[{i}/{len(to_ingest)}] Processing: {rel_path} ({size} bytes)")
                encoded_path = requests.utils.quote(rel_path)
                file_url = urljoin(NEXTCLOUD_URL, f"/remote.php/dav/files/{NEXTCLOUD_USER}/{encoded_path}")
                temp_path = os.path.join(INGESTION_TEMP_DIR, f"{doc_id_base}_{fname}")
                
                try:
                    with requests.get(file_url, auth=(NEXTCLOUD_USER, NEXTCLOUD_PASS), stream=True, verify=False, timeout=900) as r:
                        r.raise_for_status()
                        with open(temp_path, 'wb') as f:
                            for chunk in r.iter_content(chunk_size=8192): f.write(chunk)
                    
                    content = extract_text_content(temp_path)
                    
                    # FIX: Lowered check to MIN_TEXT_LENGTH (10)
                    if content and len(content.strip()) > MIN_TEXT_LENGTH:
                        docs_to_add = []
                        
                        # Book Summary Logic
                        if category == "book":
                            logger.info(f"Generating Summary for: {fname}")
                            summary = generate_summary(content)
                            if summary:
                                docs_to_add.append(Document(
                                    page_content=f"SUMMARY for '{fname}':\n{summary}", 
                                    metadata={**common_metadata, "type": "book_summary"}
                                ))
                        
                        # Standard Chunking
                        chunks = text_splitter.split_text(content)
                        for ix, c in enumerate(chunks):
                            docs_to_add.append(Document(
                                page_content=c, 
                                metadata={**common_metadata, "type": category, "chunk_index": ix, "total_chunks": len(chunks)}
                            ))
                        
                        vectordb.add_documents(docs_to_add)
                        processed_count += 1
                        logger.info(f"-> Ingested {len(chunks)} chunks.")
                    else:
                        logger.warning(f"Skipping empty/unreadable text (Content len: {len(content.strip()) if content else 0}): {rel_path}")
                
                except Exception as e:
                    logger.error(f"Error processing {rel_path}: {e}")
                finally:
                    if os.path.exists(temp_path): os.remove(temp_path)
            
            # --- Periodic Persist ---
            if processed_count % PERSIST_FREQUENCY == 0:
                 if hasattr(vectordb, 'persist'): 
                     vectordb.persist()
                     logger.info(f"Checkpoint saved. ({processed_count} items processed)")

        # Final Save (Files)
        try:
            if hasattr(vectordb, 'persist'): vectordb.persist()
        except: pass

    # --- 4. SYSTEM SUMMARY DOCUMENT ---
    try:
        vectordb._collection.delete(where={"source": "system_nextcloud_summary"})
    except: pass
    
    total_files = vectordb._collection.count()
    summary_doc = Document(
        page_content=f"Nextcloud System Summary:\nTotal Documents: {total_files}\nLast Sync: {time.ctime()}",
        metadata={
            "source": "system_nextcloud_summary",
            "type": "system_info",
            "etag": str(time.time()),
            "filename": "SYSTEM_SUMMARY",
            "source_server": NEXTCLOUD_URL,
            "source_user": NEXTCLOUD_USER
        }
    )
    vectordb.add_documents([summary_doc])
    
    if hasattr(vectordb, 'persist'): vectordb.persist()
    logger.info(f"Sync finished. Processed {processed_count if 'processed_count' in locals() else 0} new/modified files.")

if __name__ == "__main__":
    sync_nextcloud_files()

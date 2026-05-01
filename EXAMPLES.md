# SharedLLM Librarian Examples

This guide provides real-world scenarios for using the Librarian's deep indexing and knowledge retrieval capabilities.

## 1. Initial Knowledge Ingestion
Trigger a full scan of your library to build the semantic index.

**Command:** *"Hey Librarian, index my library."*

**Technical Flow:**
1.  **Gateway**: Classifies intent as `index_storage`.
2.  **Storage**: Scans NextCloud recursively.
3.  **Indexer**: Classification -> Extraction -> Chunking.
4.  **RAG**: Stores chunks with `session_id`.
5.  **Cleanup**: Purges old data.

---

## 2. Deep Content Retrieval
Ask questions about what's *inside* your files.

**Scenario**: You have a file `Project_Plan.md` with the text: *"The launch date is June 1st."*

**Command:** *"When is the project launch date?"*

**What happens:**
*   The Librarian retrieves the "June 1st" snippet from RAG.
*   The LLM answers: *"According to your Project Plan, the launch date is June 1st."*

---

## 3. Fast Knowledge Updates (Delta Sync)
Add a new file and update without re-processing everything.

**Scenario**: You just uploaded `Meeting_Notes.txt`.

**Command:** *"Update my library."*

**Why it's fast:**
The `CheckpointManager` sees that your existing 500 files haven't changed and skips them instantly, only processing the new `Meeting_Notes.txt`.

---

## 4. Resource Prioritization in Action
See how the system manages heavy loads.

**Scenario**:
1.  You start a massive re-index.
2.  While it's running, you ask a complex question: *"Who was the 3rd President?"*

**Mechanism**:
*   The Gateway detects a "Slow Path" (LLM) request.
*   It immediately hits `/index/pause` on the Storage service.
*   The Indexer yields CPU/GPU resources to the LLM.
*   Once the answer is delivered, the Gateway hits `/index/resume`.
*   Indexing continues in the background.

---

## 5. Automated Data Hygiene
Removing a file from NextCloud automatically removes it from the Librarian's memory.

**Scenario**:
1.  You delete `Confidential_Doc.pdf` from NextCloud.
2.  You run *"Index my library"*.
3.  The system identifies that the document is no longer in the "Live" session.
4.  The Librarian's memory is purged of all snippets related to that file.

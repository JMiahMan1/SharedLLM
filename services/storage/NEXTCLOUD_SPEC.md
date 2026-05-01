# NextCloud Client Specification

This document defines the mandatory capabilities and interface requirements for the `NextCloudClient` within the SharedLLM Storage Service.

## 1. Core Responsibilities
The client acts as the low-level bridge between the provider abstraction and the NextCloud WebDAV API. It must handle authentication, recursive listing, and direct content streaming.

## 2. Mandatory Capabilities

### A. Authentication
*   **Mechanism**: HTTP Basic Authentication.
*   **Requirements**: Must accept a `host`, `username`, and `password`.
*   **Protocol**: Must support both `http` and `https`.

### B. Path Management & Normalization
*   **Base DAV Route**: All requests must be relative to the user's specific DAV directory:
    `/remote.php/dav/files/{username}/`
*   **Safety**: Must detect and strip double-prefixes (e.g., if a file list returns a path already containing the DAV route).

### C. Resource Discovery
*   **Listing**: Support non-recursive and recursive listing of directories.
*   **Metadata**: Extract the following for every entry:
    *   Name & Full Path
    *   Size (bytes)
    *   Mtime (ISO or epoch)
    *   Content-Type (MIME)
*   **Directory Detection**: Must reliably identify folders using `content-type` (`httpd/unix-directory`) OR the presence of a trailing slash in the DAV response.

### D. Content Extraction
*   **Direct Stream**: For indexing, text content must be fetched via direct `GET` requests to avoid the overhead of saving temporary files.
*   **Timeouts**: Must implement a minimum 15-second timeout for retrieval to handle large documents or slow network conditions.

## 3. Interface Alignment
The client must output data compatible with the `StorageEntry` Pydantic model:
```python
class StorageEntry(BaseModel):
    path: str
    name: str
    is_dir: bool
    size: int | None
    mtime: str | None
    content_type: str | None
```

## 4. Known Constraints
*   **Library Dependency**: Currently uses `easywebdav` for `PROPFIND` requests. If replaced, the replacement must handle XML parsing of WebDAV responses.
*   **Direct GET**: Uses `requests` for content retrieval to leverage standard HTTP streaming/timeout features.

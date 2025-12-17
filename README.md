# SharedLLM - Unified RAG Middleware AI

A central intelligence layer that unifies smart home control, personal cloud services, and knowledge systems into a single conversational AI interface.

## Purpose

This project implements a Unified RAG Middleware AI, serving as the central intelligence layer between:

### Smart Home
- **Home Assistant** - Device control, sensors, timers, alarms
- **Music Assistant** 
  - Music playback (play, pause, stop, next, previous)
  - Playlist selection
  - Artist/album/track search
  - Radio station listing
  - Podcasts (using Audio Bookshelf provider in Music Assistant)
  - Audiobooks (using Audio Bookshelf provider in Music Assistant)

### Personal Cloud
- **Nextcloud Calendar** - Create, read, update, delete events
- **Nextcloud Notes** - Create, read, append, delete notes
- **Nextcloud Files** - Document ingestion and RAG search
- **Nextcloud Media** - Ebooks, PDFs, MP3 metadata, audiobooks
- **Audio Bookshelf** - Used as provider in Music Assistant for podcasts and audiobooks

### Knowledge Systems
- **Vector RAG DB** (ChromaDB) - Long-term knowledge storage
- **Whoogle/SearXNG** - Search engine integration
- **Local LLMs** (Ollama) - Preferred LLM backend
- **Cloud LLMs** (OpenAI/compatible) - Fallback option

### Unified Persona Across All Interfaces

All interfaces share the same memory, context, and personality:
- Home Assistant Assist
- OpenWebUI
- REST API clients
- Future CLI / mobile clients

## Current Code Structure

```
app/
  data/
    system_prompt.txt      # Unified personality prompt
    phrasebook.json        # Intent training phrases
    alarm_keywords.json    # Alarm sound mappings
  ha_ingest.py             # Home Assistant data ingestion
  ingest_nextcloud.py      # Nextcloud document ingestion
  intent_engine.py         # Vector-based intent classification
  logic/
    pipeline.py            # Main request processing pipeline
    media_ops.py           # Media/device control
    music_assistant_ops.py  # Music Assistant integration
    calendar_ops.py         # Nextcloud calendar operations
    timer_ops.py           # Timer/alarm operations
    note_ops.py            # Nextcloud notes operations
    web_search.py          # Web search tool
    execution/             # Tool execution system
      registry.py          # Tool registry
      handlers.py          # Tool handlers
      fast_path.py         # Fast path executor
    intents/
      classifier.py        # Intent classification (regex + vector)
    discovery/             # Device discovery and grouping
      device_grouper.py
      integration_helper.py
  main.py                  # FastAPI entry point
  settings.py              # Configuration
test/                      # Test suite
tools/                     # Diagnostic and testing tools
docker-compose.yml
Dockerfile
requirements.txt
```

## Implemented Features

### ✅ Core Infrastructure
- RAG retrieval from ChromaDB
- Multi-backend LLM support (Ollama/OpenAI)
- Streaming responses (OpenAI-compatible)
- Shared memory via Redis (chat history, context)
- Intent classification (regex overrides + vector matching)
- Multi-intent command parsing ("turn off lights and play music")

### ✅ Home Assistant Integration
- Device control (turn on/off, toggle)
- Device state queries
- Volume control (set, up, down, mute)
- Media playback control (play, pause, stop, next, previous)
- App launching (Android TV/Chromecast)
- Navigation control (up, down, left, right, back, home, select)
- Device grouping and batch operations
- Smart device resolution with capability routing

### ✅ Music Assistant Integration
- Music search (artist, album, track)
- Playlist listing
- Radio station listing
- Music playback via Music Assistant players
- Integration with Audio Bookshelf for podcasts/audiobooks

### ✅ Timers & Alarms
- Create timers ("remind me in 10 minutes")
- Create alarms ("wake me up at 7am")
- List active timers/alarms
- Cancel/delete timers
- Natural language time parsing
- Redis-backed persistence
- Background scheduler for alarm triggering

### ✅ Nextcloud Calendar
- Create events
- Read/list events
- Update/reschedule events
- Delete events
- Natural language time parsing
- Calendar target selection

### ✅ Nextcloud Notes
- Create notes
- Read notes
- Append to notes/lists
- Delete notes

### ✅ Nextcloud Files
- Document ingestion (PDFs, text files)
- RAG search across documents
- Metadata extraction

### ✅ Web Search
- Whoogle/SearXNG integration
- Contextual search results

## Planned Features

### 🔄 Music Assistant - Expanded
- **Podcasts**
  - Search for podcasts
  - Continue last played episode
  - Play specific episode
  - Jump to time offset
  - List subscriptions / add new subscriptions

- **Audiobooks**
  - Resume automatically from last bookmark
  - Access chapter-level metadata
  - Voice commands: "Continue my audiobook", "Skip to chapter 5", "Play the last 10 minutes again"

- **Unified Media Intelligence**
  - LLM decides content type (music/podcast/audiobook) and routes correctly

### 🔄 Nextcloud Contacts
- Read/search contacts
- Add/update contacts
- Fuzzy matching by name

### 🔄 Nextcloud Talk (Messaging)
- Send messages to users
- Messaging groups/channels
- Reply in threads
- Optional attachments (text-first)

### 🔄 Git / Code Infrastructure
- Clone/pull repositories
- Index file tree
- Convert code into RAG chunks
- Symbol/function search
- Dependency and import graph
- Ask questions about specific functions

### 🔄 Performance Enhancements
- Redis + LRU caching (across services)
- Embedding fingerprinting to avoid duplicate RAG work
- Async ingestion (Nextcloud, Git)
- Non-blocking HA+Music Assistant calls
- Batch vector queries
- Background workers for large tasks
- Reranking for RAG accuracy

## Architecture Highlights

### Request Processing Pipeline
1. **Decompose** - Split compound commands ("and"/"then")
2. **Contextualize** - Resolve pronouns, refine query
3. **Classify Intent** - Regex → Vector matching
4. **Orchestrate** - LLM decides: tool_call vs CONVERSE
5. **Execute** - Tool execution via ActionDispatcher
6. **Respond** - Generate response with context

### Intent Classification
- **Regex Overrides** - Deterministic pattern matching (highest priority)
- **Vector Matching** - Semantic similarity via sentence transformers
- **Confidence Thresholds** - Action intents: 0.45, High confidence: 0.85

### Device Resolution
- Vector similarity search in ChromaDB
- Intent-based routing (music → Music Assistant, power → hardware)
- Device grouping for batch operations
- Capability-aware selection

### Tool Execution
- Central registry system (`ActionDispatcher`)
- Fast path for simple commands (bypasses LLM)
- Parallel execution for multi-intent commands

## Configuration

Key environment variables:
- `OLLAMA_URL` - LLM endpoint (default: `http://localhost:11434`)
- `HA_URL` - Home Assistant URL
- `HA_TOKEN` - Home Assistant API token
- `NEXTCLOUD_URL` - Nextcloud instance URL
- `NEXTCLOUD_USER` / `NEXTCLOUD_PASS` - Nextcloud credentials
- `AUDIOBOOKSHELF_URL` / `AUDIOBOOKSHELF_USER` / `AUDIOBOOKSHELF_PASS` - AudioBookShelf credentials
- `CHROMA_PERSIST_DIR` - ChromaDB storage path (default: `/data/chroma_db`)
- `REDIS_URL` - Redis connection string (default: `redis://redis:6379/0`)
- `DEFAULT_MODEL` - LLM model (default: `qwen2.5:latest`)
- `WHOOGLE_URL` - Search engine URL

### Multi-User Support

The system supports multiple users with isolated data and credentials:

**Default User (Shared Data):**
- Uses the main environment variables above
- Provides shared knowledge base and device access
- All users can access this shared data

**User-Specific Configuration:**
Users can have their own credentials using environment variables with the format:
- `USER_{USERNAME}_{SETTING}` (recommended)
- Or `{USERNAME}_{SETTING}` (alternative)

Example:
```bash
# User John with custom credentials
USER_JOHN_DISPLAY_NAME=John Doe
USER_JOHN_NEXTCLOUD_USER=john@cloud.example.com
USER_JOHN_NEXTCLOUD_PASS=johns_password
USER_JOHN_HA_TOKEN=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...
USER_JOHN_AUDIOBOOKSHELF_USER=john
USER_JOHN_AUDIOBOOKSHELF_PASS=johns_book_password

# User Jane
USER_JANE_DISPLAY_NAME=Jane Smith
USER_JANE_NEXTCLOUD_USER=jane@cloud.example.com
USER_JANE_HA_TOKEN=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...
```

**User-Specific Data Isolation:**
- Conversation history
- Device preferences (last used device)
- Personal settings and context
- All stored separately per user in Redis/ChromaDB

**API Usage:**
Set the `X-RAG-User` header to specify which user context to use:
```bash
curl -H "X-RAG-User: john" -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "Play some music"}]}' \
  http://localhost:11435/api/chat
```

## Deployment

```bash
# Deploy to remote server
./deploy_remote.sh

# Or use Docker Compose
docker compose up -d
```

## Testing

- **Unit Tests**: `test/unit/` - Capability detection, routing, grouping
- **Integration Tests**: `test/integration_tests.py`, `test/live_test.py`
- **Diagnostic Tools**: `tools/test_volume.py`, `tools/test_connectivity.py`

## End Goal

A single self-hosted AI layer that:
- Controls the smart home
- Plays music, podcasts, and audiobooks
- Manages Nextcloud calendar, contacts, and messaging
- Searches and summarizes personal documents
- Understands Git repos and code
- Uses RAG intelligently
- Shares memory across all interfaces
- Responds quickly
- Never breaks when upgraded
- Offers similar functionality to Google Home or Alexa

## Unified Persona

All interfaces use `/app/data/system_prompt.txt` for consistent personality:
- Witty, helpful, grounded in Biblical Christian worldview
- Context-aware brevity (efficient for commands, conversational for inquiries)
- Quality humor (clever Dad jokes, sparingly)
- Scripture and wisdom when appropriate
- Honest about limitations

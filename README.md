# SharedLLM - Unified RAG Middleware AI

A central intelligence layer that unifies smart home control, personal cloud
services, and knowledge systems into a single conversational AI interface.

## Purpose

This project implements a Unified RAG Middleware AI, serving as the central
intelligence layer between:

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
- **Audio Bookshelf** - Used as provider in Music Assistant for podcasts and
  audiobooks

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

## Documentation

- [Architecture Overview](docs/architecture.md)
- [Integrations Guide](docs/integrations.md)
- [Roadmap](docs/roadmap.md)
- [API Reference](docs/api_reference.md)

## Current Code Structure

```text
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
    music_assistant_ops.py # Music Assistant integration
    calendar_ops.py        # Nextcloud calendar operations
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
- **Multi-intent command parsing** ("turn off lights and play music")
- **Conversation Context** - Robust handling of follow-up questions using
  history.

### ✅ Home Assistant Integration

- Device control (turn on/off, toggle)
- Device state queries
- Volume control (set, up, down, mute)
- Media playback control (play, pause, stop, next, previous)
- **Android TV Support** - Button commands and App launching
- Navigation control (up, down, left, right, back, home, select)
- Device grouping and batch operations
- Smart device resolution with capability routing
- **Area-based targeting** ("Turn off lights in the Office")

### ✅ Music Assistant Integration

- Music search (artist, album, track)
- Playlist listing
- Radio station listing
- Unified `music_list` tool for browsing
- Music playback via Music Assistant players
- Integration with Audio Bookshelf for podcasts/audiobooks

### ✅ Timers & Alarms

- Create timers ("remind me in 10 minutes")
- Create alarms ("wake me up at 7am")
- **Absolute Alarms** - Reliable parsing of specific times.
- List active timers/alarms
- Pause/Resume/Delete timers
- Natural language time parsing
- Redis-backed persistence

### ✅ Nextcloud Calendar & Notes

- **Calendar**: Create, List, Update, and Delete events.
- **Notes**: Create, Read, Append (with checkboxes), Update, and Delete.
- **List Management**: "Check off" items in notes via `note_check_off`.

### ✅ Documentation & Testing

- **100% Test Coverage** - Automated suite for all core tool handlers.
- **Unified Test Runner** - `MasterRunner` with console and JSON reporting.

## Testing

The system includes a comprehensive automated test suite located in `app/tests/`.

### Automated Verification

- **Run All Tests**: `python3 -m app.tests.runner --url [API_URL]`
- **REST API**: Trigger tests via `POST /api/admin/run_tests` (returns JSON
  report).

### Coverage Areas

- **Media**: Volume, Transport, Library Browsing.
- **Productivity**: Calendar/Note CRUD and list management.
- **Hardware**: Lights (Color/Brightness), Android TV.
- **Pipeline**: Compound commands, context persistence.
- **Search**: Web Query, Music Assistant Library.

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

# Future Roadmap & TODOs

This document outlines the planned features and architectural improvements for
the SharedLLM project.

## 🧠 Database Scalability & Vector Strategy

### Is ChromaDB the best fit?

ChromaDB is excellent for **Semantic Discovery** (finding "the reading lamp"
when you say "my evening light"), but it is **not suitable for Real-Time State**
(tracking if the light is ON or OFF).

### Proposed Hybrid Architecture (The "Best Solution")

To update individual entries without major CPU/IO overhead, we should adopt a
strict separation of concerns:

1. **Static Discovery (ChromaDB)**
    * Stores: Device Name, ID, Room, Capabilities, Integration Type.
    * Update Frequency: Low (Only when adding new devices or renaming them).
    * Optimization: Instead of `refresh_db()` (full wipe), implement
      `patch_device(entity_id)` which computes the embedding for *one* item and
      upserts it.
    * Why: Embeddings are expensive to calculate. We shouldn't re-calculate them
      just because a light turned on.

2. **Dynamic State (Redis / Live API)**
    * Stores: State (on/off), Volume, Brightness, Current Song.
    * Update Frequency: High (Real-time).
    * Strategy:
        * **Current**: We use Live API calls (`get_ha_context` -> `requests.get`)
          which is perfectly accurate.
        * **Future**: Home Assistant WebSocket stream -> Redis Cache. This enables
          0ms latency state lookups without hammering the HA API.

## 🚀 Priority 1: Enriched Media Intelligence

* [x] **YouTube Video Search Fallback**
  * **Goal**: Allow "Play [Video Name]" to automatically find a URL if one isn't
    provided.
  * **Tech**: Integrated `duckduckgo-search` and `yt-dlp` to fetch and stream
    results.
  * **Status**: *Completed*.
* [ ] **Multi-Room Audio Groups**
  * **Goal**: "Play music in the whole house" or "Move music to the Kitchen".
  * **Tech**: Leverage Music Assistant's native grouping and valid
    `media_player.join` services in HA.
* [ ] **Smart Podcast/Audiobook Routing**
  * **Goal**: Distinguish between "Play Harry Potter" (Audiobook) and "Play Harry
    Potter Soundtrack" (Music).
  * **Tech**: Use LLM to classify efficient "content_type" before routing.

## 🛠 Priority 2: System Hardening

* [ ] **Unit Test Coverage**
  * Add comprehensive tests for the `IntentEngine` regex patterns.
  * Mock Home Assistant API for offline testing.
* [ ] **User-Specific Context**
  * Enhance `Redis` storage to remember *which* user asked for what (e.g.,
    "Resume *my* podcast", not the generic last one).

## 🧠 Priority 3: Advanced Intelligence

* [ ] **Git Repository Ingestion**
  * **Goal**: "How does the `factory.py` file work?"
  * **Tech**: Clone repo -> AST Parse -> Chunk -> Vectorize. allow the Agent to
    read its own source code.
* [ ] **Vision Capabilities**
  * **Goal**: "What's on the camera?"
  * **Tech**: Integrate Frigate/HA camera snapshots with `Llava` or `GPT-4o`.

## 📋 Backlog / Good to Have

* **Shopping List Analytics**: Log historical check-offs to answer "How often
  do I buy [Item]?" or "What's my most common purchase?".
* **Scene Capture & Restore**: "Save the current lights and music as 'Movie
  Night'" to create on-the-fly HA scenes.
* **Interactive Diagnostics**: A `check_device` tool to ping, check power, and
  query HA logs when a device is non-responsive.
* **Multi-Modal Notifications**: Enhance `ha_notify` to include snapshots from HA
  cameras or links to Nextcloud files.
* **Weather & Commute Briefing**: Generate a daily briefing tool that compiles
  Calendar events, local weather, and traffic data.
* **Nextcloud Talk Integration**: Send notifications/messages via NC Talk.
* **Email Summary**: Daily briefing generated from key emails.
* **Personality Tuning**: Configurable personality profiles via
  `system_prompt.txt` hot-reloading.

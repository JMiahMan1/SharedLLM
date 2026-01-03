# Future Roadmap & TODOs

This document outlines the planned features and architectural improvements for the SharedLLM project.

## 🚀 Priority 1: Enriched Media Intelligence

* [ ] **YouTube Video Search Fallback**
  * **Goal**: Allow "Play [Video Name]" to automatically find a URL if one
    isn't provided.
  * **Tech**: Integrate `duckduckgo-search` or `yt-dlp` to fetch top results.
  * **Status**: *Planned*.
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

* **Nextcloud Talk Integration**: Send notifications/messages via NC Talk.
* **Email Summary**: Daily briefing generated from key emails.
* **Personality Tuning**: Configurable personality profiles via
  `system_prompt.txt` hot-reloading.

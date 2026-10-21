# Pending Tasks

The one backlog for work that is **not done yet**. Verified against the code
on 2026-10-21. When something here ships, delete it; history lives in git.

## Media player

- Background playback stops when the app is swiped away from recents (the
  WebView is destroyed). Surviving that needs a native audio player.
- The remaining phases of `docs/MEDIA_OVERHAUL.md`.

## Bible study

- Saved study turns are ingested into RAG with citable `bible-turn-*` ids.
  RAG ingest of promoted notes and plan reflections remains.
- Synced-scroll comparison and the reference-chip popover remain.

# Pending Tasks

The one backlog for work that is **not done yet**. Verified against the code
on 2026-10-20. When something here ships, delete it; history lives in git.

## Media player

- Background playback stops when the app is swiped away from recents (the
  WebView is destroyed). Surviving that needs a native audio player.
- The remaining phases of `docs/MEDIA_OVERHAUL.md`.

## Bible study

- Study-memory search is shipped over saved questions and answers. RAG ingest
  of promoted notes and study threads with stable citable ids remains.
- Workspace export: write the study journal, memory sets and quiz results to an
  opt-in workspace.
- In-chat Bible trivia: the Talk game API exists, but `FAMILY_GAMES_ENABLED` is
  off and the question source is the generic trivia set, not the Bible corpus.
- Synced-scroll comparison and the reference-chip popover remain.

# Notes

Notes are plain markdown files in Nextcloud, exposed through the execution
service. The UI is a Google Keep-style surface: a quick-capture bar, a masonry
grid of colour-coded cards with checklists, search, and a full editor.

## Where it lives

| Piece | Path |
|---|---|
| Keep-style page (route `/notes`) | `services/ui/src/pages/Notes.tsx` |
| Pin/colour/search/header helpers | `services/ui/src/lib/notesMeta.ts` |
| Save / rename (shared with Recipes) | `services/ui/src/lib/noteSave.ts` |
| API client | `services/ui/src/services/api.ts` (`*Note` methods) |
| Gateway proxies | `services/gateway/main.py` (`/api/communication/notes/*`) |
| Handler | `services/execution/handlers/note.py` |
| Schema | `services/execution/schemas.py` (`NoteRequest`) |

Recipes (`pages/Recipes.tsx`, `Recipes/` folder) use the same actions. The
dashboard `QuickNotesWidget` shows recent titles. Communication links here; its
old second editor (whose Save called `append`) is gone.

## Actions

`POST /execute/note` accepts:

| action | behaviour |
|---|---|
| `create` | write a new note (`# title` + `Category:` header + body) |
| `write` | **full replace**, used by the editor's Save; returns `detail.path` |
| `append` | quick-capture only: adds `- [ ] <content>` to the end |
| `read` | return the file body in `message` |
| `delete` | remove the file |
| `check_off` | toggle one checklist item (`- [ ] x` ↔ `- [x] x`) by text |
| `list` | list markdown files under the configured directories |
| `sync_rag` | read every note (execution); the gateway then indexes them (below) |

The stored header is the server's: `create`/`write` strip any leading
`# title` + `Category:` header(s) from the content before adding one
(`note_body`), and the editor shows the note without it (`noteBody`). Before
that, every save of an opened note stacked another header on top.

> **Important:** `append` is not a save. Before `write` existed, the editor's
> Save called `append`, which appended the whole editor body as a checklist
> line on every save. `test_note_actions.py` asserts that `write` performs a
> single `PUT` so this cannot regress.

## UI behaviour

- **Quick capture**: the "Take a note…" bar opens an empty editor; the title
  falls back to the first line of the body, then `Untitled note`.
- **Folders**: the page lists `Notes/` recursively; the editor's Folder field
  is a sub-folder inside it (one level). Changing a note's title or folder
  writes the new file, then deletes the old one once the new one is saved.
- **Teach Jarvis** (`POST /api/communication/notes/sync_rag`): execution reads
  the notes, the gateway chunks them like the storage indexer and writes them
  to RAG's `nextcloud_files` collection (the one chat searches) under the
  caller, replacing each note's old passages by path. Only counts come back.
  Deleted notes stay indexed until the next storage re-index.
- **Cards**: markdown checklists are parsed and rendered as real checkboxes;
  toggling one calls `check_off` and re-reads the note. Non-checklist notes
  show a text preview with the stored `# title` / `Category:` header stripped.
- **Search**: filters on title and (for the first 24 notes) body text.
- **Pin and colour**: view preferences, stored per device in
  `jarvis_notes_meta_v1` (localStorage) and never sent to the LLM. Note
  *content* stays in Nextcloud. On a touch screen the pin is always shown and
  colour is set in the editor; hover reveals both on a desktop. Delete asks
  first.
- **Previews**: only the first 24 notes are fetched for previews so a large
  notebook does not turn one page load into hundreds of WebDAV reads.
- **Failures**: a failed list shows the backend message (e.g. "Nextcloud
  credentials missing") instead of an empty-looking notebook.

## Tests

- `services/ui/src/test/Notes.test.tsx`: list/preview/checklist, save uses
  `write`, opens without the header, rename moves the file, pin persistence,
  search, failure surfacing
- `services/ui/src/pages/Recipes.test.tsx`: edit in place, rename, delete
- `services/gateway/tests/test_notes_rag_sync.py`: indexing writes to RAG
- `services/ui/src/test/notesMeta.test.ts`: checklist parsing, previews,
  sorting, pin/colour storage
- `services/execution/tests/test_note_actions.py`: `write` replaces (single
  PUT) without stacking headers, `check_off` toggles and reports missing items

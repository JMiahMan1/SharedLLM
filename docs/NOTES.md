# Notes

Notes are plain markdown files in Nextcloud, exposed through the execution
service. The UI is a Google Keep-style surface: a quick-capture bar, a masonry
grid of colour-coded cards with checklists, search, and a full editor.

## Where it lives

| Piece | Path |
|---|---|
| Keep-style page (route `/notes`) | `services/ui/src/pages/Notes.tsx` |
| Pin/colour/search helpers | `services/ui/src/lib/notesMeta.ts` |
| API client | `services/ui/src/services/api.ts` (`*Note` methods) |
| Gateway proxies | `services/gateway/main.py` (`/api/communication/notes/*`) |
| Handler | `services/execution/handlers/note.py` |
| Schema | `services/execution/schemas.py` (`NoteRequest`) |

A second, older surface lives in `pages/Communication.tsx` (list + CodeMirror
editor) and the dashboard `QuickNotesWidget` shows recent titles.

## Actions

`POST /execute/note` accepts:

| action | behaviour |
|---|---|
| `create` | write a new note (`# title` + `Category:` header + body) |
| `write` | **full replace** — this is what the editor's Save uses |
| `append` | quick-capture only: adds `- [ ] <content>` to the end |
| `read` | return the file body in `message` |
| `delete` | remove the file |
| `check_off` | toggle one checklist item (`- [ ] x` ↔ `- [x] x`) by text |
| `list` | list markdown files under the configured directories |
| `sync_rag` | index notes into Jarvis memory (RAG) |

> **Important:** `append` is not a save. Before `write` existed, the editor's
> Save called `append`, which appended the whole editor body as a checklist
> line on every save. `test_note_actions.py` asserts that `write` performs a
> single `PUT` so this cannot regress.

## UI behaviour

- **Quick capture** — the "Take a note…" bar opens an empty editor; the title
  falls back to the first line of the body, then `Untitled note`.
- **Cards** — markdown checklists are parsed and rendered as real checkboxes;
  toggling one calls `check_off` and re-reads the note. Non-checklist notes
  show a text preview with the stored `# title` / `Category:` header stripped.
- **Search** — filters on title and (for the first 24 notes) body text.
- **Pin and colour** — view preferences, stored per device in
  `jarvis_notes_meta_v1` (localStorage) and never sent to the LLM. Note
  *content* stays in Nextcloud.
- **Previews** — only the first 24 notes are fetched for previews so a large
  notebook does not turn one page load into hundreds of WebDAV reads.
- **Failures** — a failed list shows the backend message (e.g. "Nextcloud
  credentials missing") instead of an empty-looking notebook.

## Tests

- `services/ui/src/test/Notes.test.tsx` — list/preview/checklist, save uses
  `write`, pin persistence, search, failure surfacing
- `services/ui/src/test/notesMeta.test.ts` — checklist parsing, previews,
  sorting, pin/colour storage
- `services/execution/tests/test_note_actions.py` — `write` replaces (single
  PUT), `check_off` toggles and reports missing items

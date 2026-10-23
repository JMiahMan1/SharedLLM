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
dashboard `QuickNotesWidget` shows recent titles. The Announce panel's old
second editor (whose Save called `append`) is gone, and its notes shortcut went
with it — Notes lives here and in the nav.

## Actions

`POST /execute/note` accepts:

| action | behaviour |
|---|---|
| `create` | write a new note (`# title` + `Category:` header + body) |
| `write` | **full replace**, used by the editor's Save; returns `detail.path` |
| `append` | quick-capture only: adds `- [ ] <content>` to the end |
| `read` | return the file body in `message` |
| `delete` | remove the file |
| `move` | move a note with one WebDAV `MOVE` (archive, trash, restore or rename) |
| `duplicate` | copy a note to the first free `<title> (copy).md` in its folder |
| `empty_trash` | permanently delete every note under `Notes/Trash` |
| `meta_read` / `meta_write` | read or replace the shared index `Notes/.jarvis-notes.json` (pin, colour, archive origin) |
| `check_off` | toggle one checklist item (`- [ ] x` ↔ `- [x] x`) by text |
| `list` | list markdown files under the configured directories, newest first; with `previews: true` the newest 24 also carry their text in `content` |
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
- **Instant shelf**: each visit's list and index are kept in localStorage per
  account, so the page paints the last known notes immediately and revalidates
  in the background. Opening a note whose text came with the list shows that
  text at once and reconciles with the stored file a moment later, unless you
  have already started editing.
- **Prompt moves**: archive, trash and restore move the card on the shelf the
  moment you tap, then reconcile with the server in the background. Every note
  call allows a full minute, because WebDAV can be slow while previews are
  still loading on a phone.
- **Folders**: the page lists `Notes/` recursively; the editor's Folder field
  is a sub-folder inside it (one level). Changing a note's title or folder
  writes the new file, then deletes the old one once the new one is saved.
- **Teach Jarvis** (`POST /api/communication/notes/sync_rag`): execution reads
  the notes, the gateway chunks them like the storage indexer and writes them
  to RAG's `nextcloud_files` collection (the one chat searches) under the
  caller, replacing each note's old passages by path. Only counts come back.
  Deleted notes stay indexed until the next storage re-index.
- **Cards**: markdown checklists are parsed and rendered as real checkboxes;
  toggling one calls `check_off` and reloads the list. Non-checklist notes
  show a text preview with the stored `# title` / `Category:` header stripped.
- **Search**: filters on title and (for the first 24 notes) body text.
- **Pin, colour and location**: stored in a shared index,
  `Notes/.jarvis-notes.json`, so they follow the family's notes instead of one
  browser. The page reads it with the list and writes it back after a pin,
  colour, archive, trash or restore; a one-time migration lifts any
  `jarvis_notes_meta_v1` entries that are still only in localStorage. The
  index is a hidden dotfile inside `Notes/`, not a note: markdown files stay
  the only source of note content, and Nextcloud Notes ignores it.
- **Archive and Trash**: card actions move a note with one WebDAV `MOVE` into
  `Notes/Archive/` or `Notes/Trash/`; the index remembers the path it came
  from so **Restore** puts it back. Trash has an "Empty trash" action that
  confirms before deleting permanently. Both folders are excluded from the
  main grid. On a touch screen the pin is always shown and colour is set in
  the editor; hover reveals both on a desktop. Delete asks first.
- **Previews**: the page asks `list` for `previews: true` and gets the newest
  24 notes' text in the same reply. Execution reads them four at a time and
  keeps each note's text keyed by its file URL, modified time and size, so a
  later list only reads notes that changed; `create`, `write`, `append`,
  `check_off` and `delete` forget the note they touch. The page used to send
  one `read` per card: 24 reads at once filled execution's 10 pooled
  connections to Nextcloud, the rest failed with "Connection timeout" after
  10s, and Talk and the editor's own read queued behind them for minutes.
- **Opening a note**: the sheet opens at once with "Opening note…" and fills
  from a fresh `read`, never from the preview, so a save cannot write back a
  copy older than the file. If the read fails the sheet closes with the
  reason; it used to open empty, and saving that emptied the note.
- **Typing**: the editor keeps its own draft, so a keystroke re-renders the
  sheet only. It used to re-render the page and every card, and each card
  and sort comparison parsed the stored pin/colour map from localStorage.
- **WebDAV cost**: Nextcloud spends one to several seconds on every listing,
  so `list` makes one whole-tree PROPFIND (`Depth: infinity`, about 4 s for
  187 notes) instead of one per folder (nine in a row took over 20 s). If the
  server refuses infinite depth it lists folders four at a time. Listing never
  creates the folder (a missing one has no notes); writes still do, with a
  `Depth: 0` check. Listings ask only for type, modified time and size. The
  list request may take up to 60s on the client.
- **Failures**: a failed list shows the backend message (e.g. "Nextcloud
  credentials missing") instead of an empty-looking notebook.

## Android home screen widget

- `NotesWidget` shows the newest four notes from the Nextcloud folder
  (`POST /api/communication/notes/list`, previews included), skipping
  `Notes/Archive` and `Notes/Trash`. It strips the stored `# Title` /
  `Category:` header, renders checklist items as ☐/☑ and joins the first
  blocks into one line.
- It refreshes with the shared five-minute widget alarm and the refresh icon.
  Tapping a row opens `/notes?note=<path>`; the add icon opens
  `/notes?new=1` for quick capture. Both use the `jarvis_open_path` deep link,
  and the page drops the query once consumed so closing the editor stays put.
- `SingleNoteWidget` is the other way round: pick one note in its configure
  screen and it shows that note alone. Checklist rows are their own tap
  targets, so a box can be ticked straight from the home screen through
  `/api/communication/notes/check_off` without opening the app; the note on
  disk is the same markdown Nextcloud sees. A note without checkboxes shows
  its first lines and opens in the app when tapped. Archives and Trash are
  never offered in the picker, and moving or trashing the note leaves the
  widget on its last state with an error line until it is re-picked.

## Keep parity

Shipped: pin and colour in the shared index, archive, trash with restore and
empty trash, duplicate, move without copy, checklists, search, bulk
multi-select (archive, trash, restore, delete forever), a Keep-style markdown
preview in cards and the editor, labels as inline
`#tags` (with per-label chips, a label filter, an editor "Add label" button and
`tag:`/`label:`, `colour:`/`color:` and `type:` search operators), the instant shelf (the last visit's list is cached
per account and revalidated in the background, and a note whose text came with
the list opens at once), prompt archive/trash/restore (cards move on tap, roll
back on failure, and note calls allow a minute), dashboard and home-screen
capture.

Still open: reminders (time and place), attachments/images/drawings/audio,
Nextcloud sharing, list/grid toggle, autosave and drafts, a formatting
toolbar, keyboard shortcuts, per-item checklist editing and reordering, undo
toasts, versions, offline.

## Tests

- `services/ui/src/test/Notes.test.tsx`: list/preview/checklist, previews
  arrive with the list (no per-card reads), opening reads the note fresh, a
  failed read opens no editor, typing does not reload the list, save uses
  `write`, opens without the header, rename moves the file, pin writes the
  shared index, archive and trash move and restore, duplicate and empty trash
  call their routes, search, failure surfacing
- `services/ui/src/pages/Recipes.test.tsx`: edit in place, rename, delete
- `services/gateway/tests/test_notes_rag_sync.py`: indexing writes to RAG;
  the list route passes `previews` through
- `services/ui/src/test/notesMeta.test.ts`: checklist parsing, previews,
  sorting, pin/colour storage
- `services/execution/tests/test_note_actions.py`: `write` replaces (single
  PUT) without stacking headers, `check_off` toggles and reports missing items,
  previews are read once and reused, a save forgets the cached text, reads run
  at most four at a time, an unreadable note does not fail the list, a
  timeout is named in the error, the folder check is `Depth: 0`
- `services/execution/tests/test_http_client_session_retire.py`: a pooled
  session that ages out keeps serving the requests already on it

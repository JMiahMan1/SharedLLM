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
| `write` | **full replace**, used by the editor's Save and autosave; returns `detail.path` and the new `detail.etag`. With `if_match` and `path` the PUT carries `If-Match`, and a note changed since (Nextcloud's 412) comes back as a FAILURE with `detail.conflict: true` and nothing written |
| `append` | quick-capture only: adds `- [ ] <content>` to the end |
| `read` | return the file body in `message` and its version in `detail.etag` (Nextcloud's `ETag`/`OC-ETag`) |
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

- **Checklist items in the editor**: each checklist row has its own drag grip
  and delete button in the right gutter (an overlay in `RichNoteEditor`
  placed from the rows' measured positions, since a contentEditable cannot
  hold controls). Drag the grip to move the item; the grip is the one place a
  touch drags instead of scrolling (`touch-action: none`), so it works on a
  phone too. With the grip focused, ↑/↓ moves the item and focus follows it;
  while typing, **Alt+↑/↓** moves the line the caret is on (any line, not only
  checklist rows). The text of an item is edited in place as before. Moves and
  deletes only reorder or drop `- [ ]` lines, so the file stays plain
  markdown.
- **Visual editor (Keep-style)**: a note opens as formatted text, not
  markdown. People tick real checkboxes (tap the box; Enter adds an unticked
  row, Enter on an empty row ends the list), and a toolbar gives bold, italic,
  strikethrough, heading, bulleted/numbered lists, text colour, highlight
  colour and text size. `lib/noteRichText.ts` converts to and from the stored
  markdown, so the file stays Nextcloud Notes compatible: bold `**`, italic
  `*`, strikethrough `~~`, `#`..`###`, dash and numbered lists and `- [ ]` /
  `- [x]` checklists are plain markdown. Colour, highlight and size have no
  markdown form, so they are stored as an inline
  `<span style="color:#dc2626;background-color:#fde047;font-size:1.25em">`
  (only those three properties are ever read or written); Nextcloud clients
  that do not render HTML show the tag around that text and nothing else
  changes. Card previews render the spans (`lib/remarkNoteSpans.ts`); the
  Android widgets and `check_off` ignore the tag, so ticking a styled row
  from the home screen still works.
- **Safe fallback**: a note is only opened visually when converting it to HTML
  and back reproduces it exactly (`richEditable`). Notes with tables, code
  fences, quotes, rules, images, links, nested lists, star bullets, `####`
  headings or other HTML open as text with a short notice, so the visual
  editor can never flatten them. Labels stay inline `#tags` in either mode.
- **Quick capture**: the "Take a note…" bar opens an empty editor; the title
  falls back to the first line of the body, then `Untitled note`.
- **Instant shelf**: each visit's list and index are kept in localStorage per
  account, so the page paints the last known notes immediately and revalidates
  in the background. Opening a note whose text came with the list shows that
  text at once and reconciles with the stored file a moment later. If you
  have already started editing, your typing stays and the stored version is
  adopted only when its text is what you started from; otherwise the next save
  reports a conflict (below). This reconcile used to be dropped on the floor:
  the editor seeded its state once, so it kept showing the list's copy.
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
- **Grid or list**: the header's layout button switches the shelf between
  Keep's masonry grid and one full-width column. The choice is a per-device
  convenience in localStorage (`jarvis_notes_layout_v1`, read and written in
  try/catch, so a private window just starts on the grid each visit); it is
  not in the shared index because one person's phone wanting a list says
  nothing about another's tablet.
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
- **Undo**: archive, trash and restore (one card, the editor's "Move to
  trash", or a bulk selection) show a toast with **Undo** for eight seconds
  (`lib/undoToast.tsx`). The toast appears at once, since the card has
  already moved; Undo waits for that move to land and then moves the note back
  with the same `move` route Restore uses, so a collision still gets a
  numbered name rather than overwriting. A note put back into Archive or Trash
  keeps the origin it had there; one put back on the shelf drops it. If the
  move itself fails the offer is withdrawn. Delete forever and Empty trash are
  not undoable here and keep their confirmation (Nextcloud's own Deleted
  files bin keeps the file when its trash bin app is enabled, the default).
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
- **Autosave**: an open note that keeps its title and folder saves itself
  1.5 s after typing pauses, in place, with the version it was opened at
  (`read`'s `detail.etag`) as `If-Match`; each save hands its new version to
  the next, and saves run one at a time so an autosave and a Save tap cannot
  race into a false conflict. A new note, or a rename or folder move, waits
  for Save or close, so typing a title does not leave a file per keystroke.
  Closing the sheet (Close, the ×, Escape, the backdrop) saves pending changes
  first, as in Keep; an untouched note closes without a write. A failed
  autosave shows "Not saved: <reason>" under the editor and is retried after
  the next change, not in a loop. The shared index is written only when a save
  changes a path, pin or colour.
- **Never a silent overwrite**: a save is refused when the note changed
  elsewhere since it was opened, either by Nextcloud's 412 on `If-Match` or,
  when there is no version (or the save renames the file, which `If-Match`
  cannot cover), by reading the stored text first and comparing it with what
  was opened. The editor then offers **Keep both** (yours is saved as
  "<title> (my copy)", numbered past existing titles in that folder),
  **Use the stored version** (drops yours and reopens the note) or
  **Overwrite with mine**. Autosave pauses until you choose.
- **Keyboard shortcuts** (desktop): on the shelf **C** takes a note (Keep's
  key; N is the media "next track") and **/** jumps to the notes search
  (Escape leaves it). In an open note **Ctrl/Cmd+S** saves and
  **Ctrl/Cmd+Enter** saves and closes, from inside the fields too, since a
  modifier chord is never typing; **Esc** closes (and saves) through the
  sheet. Single-key shortcuts never fire in a field, a contentEditable editor,
  with a modifier held, or while a note is open. The shelf listener sits on
  the document so its **/** wins over the app-wide media shortcuts' global
  search on the window; the media help (**?**) lists the Notes keys.
  `lib/keyboard.ts` holds the shared "is the person typing" check.
- **Drafts on the device**: every change the server does not have yet is
  kept in localStorage per account (`jarvis_notes_drafts_v1:<scope>`,
  `lib/noteDrafts.ts`, newest 20, written in try/catch) and forgotten once a
  save holds the same text. If the app closes mid-edit, the next visit shows
  an "Unsaved …" offer above the shelf with **Restore** and **Discard**, and
  opening that note shows the kept changes with a **Discard them** link.
  Drafts keep the version they started from, so saving a restored draft still
  detects a change made elsewhere since. Trashing a note from the editor drops
  its draft (Undo brings back the stored note).
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
capture, a grid/list layout toggle remembered per device, undo toasts for
archive/trash/restore, autosave with ETag conflict detection and on-device
drafts that survive a closed app, desktop keyboard shortcuts, per-item
checklist controls (drag, arrow-key and Alt+arrow reordering, delete).

Still open: reminders (time and place), attachments/images/drawings/audio,
Nextcloud sharing, versions, offline.

## Tests

- `services/ui/src/test/Notes.test.tsx`: list/preview/checklist, previews
  arrive with the list (no per-card reads), opening reads the note fresh, a
  failed read opens no editor, typing does not reload the list, save uses
  `write`, opens without the header, rename moves the file, pin writes the
  shared index, archive and trash move and restore, duplicate and empty trash
  call their routes, search, failure surfacing, the grid/list toggle is
  remembered across visits, Undo after trash and after a bulk archive moves
  each note back, a failed move withdraws the Undo offer
- `services/ui/src/test/NotesAutosave.test.tsx`: an open note autosaves in
  place with `If-Match`; a note changed elsewhere is refused and Keep both
  saves a "(my copy)"; without a version the stored text is compared first;
  closing saves pending changes and an untouched close writes nothing; a
  draft survives an unmounted app and is restored or discarded; opening a
  note with kept changes can discard them for the stored text
- `services/ui/src/test/NotesShortcuts.test.tsx`: C and / on the shelf, not in
  a field or with a modifier; Ctrl+S saves from a field, Ctrl/Cmd+Enter saves
  and closes
- `services/ui/src/test/noteDrafts.test.ts`: per-account drafts, the newest-20
  cap, a damaged store, storage that throws
- `services/ui/src/pages/Recipes.test.tsx`: edit in place, rename, delete
- `services/gateway/tests/test_notes_rag_sync.py`: indexing writes to RAG;
  the list route passes `previews` through; the write route passes `if_match`
- `services/ui/src/test/noteRichText.test.ts` / `RichNoteEditor.test.tsx`:
  markdown round trips, the unsupported-note fallback, span whitelist, tick,
  Enter and toolbar behaviour, span rendering in previews; checklist items
  move by grip arrow keys (focus follows), by pointer drag and by Alt+arrow,
  and delete one at a time
- `services/ui/src/test/notesMeta.test.ts`: checklist parsing, previews,
  sorting, pin/colour storage, the layout preference (and its fallback when
  storage throws)
- `services/execution/tests/test_note_actions.py`: `write` replaces (single
  PUT) without stacking headers, `check_off` toggles and reports missing items,
  previews are read once and reused, a save forgets the cached text, reads run
  at most four at a time, an unreadable note does not fail the list, a
  timeout is named in the error, the folder check is `Depth: 0`, `read`
  returns the ETag, an in-place `write` sends `If-Match` and returns the new
  ETag, a 412 is a conflict, a new note never sends `If-Match`
- `services/execution/tests/test_http_client_session_retire.py`: a pooled
  session that ages out keeps serving the requests already on it

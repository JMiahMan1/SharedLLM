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
| Unsaved drafts on the device | `services/ui/src/lib/noteDrafts.ts` |
| Offline edit queue (replay with If-Match) | `services/ui/src/lib/noteOutbox.ts` |
| Visual editor, checklist item controls | `services/ui/src/components/notes/RichNoteEditor.tsx` |
| Version history, reminder, sharing panels | `services/ui/src/components/notes/{NoteHistory,ReminderPanel,SharePanel}.tsx` |
| Attachments row (photo, drawing, voice) and links | `services/ui/src/components/notes/NoteAttachments.tsx`, `services/ui/src/lib/noteAttachments.ts` |
| Undo toast | `services/ui/src/lib/undoToast.tsx` |
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
| `meta_read` / `meta_write` | read or replace the shared index `Notes/.jarvis-notes.json` (pin, colour, archive origin, reminder) |
| `check_off` | toggle one checklist item (`- [ ] x` ↔ `- [x] x`) by text |
| `list` | list markdown files under the configured directories, newest first; with `previews: true` the newest 24 also carry their text in `content` |
| `sync_rag` | read every note (execution); the gateway then indexes them (below) |
| `shares` / `sharees` / `share_add` / `share_remove` | Nextcloud sharing of one note (OCS): who has it, who it could go to (`search`), share with `share_with` (read only, or `can_edit`), stop a share by `share_id`. Gateway: `POST /api/communication/notes/share` with `action: list\|people\|add\|remove` |
| `versions` / `version_read` / `version_restore` | Nextcloud's own version history of one note (`path`): list (newest first, with label and author on Nextcloud 26+), read one (`version`), or restore one over the note. Gateway: `POST /api/communication/notes/versions` with `action: list\|read\|restore` |
| `attachment_upload` / `attachment_read` / `attachment_delete` | one attachment of a saved note (`path`): upload `data` (base64, `filename`) into `.attachments.<fileid>/` beside it and get back the relative `link`; read one (`attachment`, the link) as base64 with its type; delete one. Gateway: `POST /api/communication/notes/attachment` with `action: upload\|read\|delete` |

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
  The one image or link it does draw is an attachment alone on its line
  (below): it becomes a block the caret cannot enter that keeps its
  markdown line verbatim (`data-attachment`), with the picture, player or
  file drawn into it.
- **Attachments (photos, files, drawings, voice notes)**: stored exactly as
  Nextcloud Notes stores them, so both apps show the same files and the note
  stays plain markdown. A file goes in a hidden folder beside the note named
  after the note's Nextcloud file id (`Notes/Work/.attachments.4242/bed.png`),
  and the note links to it relatively: `![bed.png](.attachments.4242/bed.png)`
  for an image, `[voice-….webm](.attachments.4242/voice-….webm)` for anything
  else. The editor's attachments row (`components/notes/NoteAttachments.tsx`,
  `lib/noteAttachments.ts`) offers **Photo or file** (images, audio, PDF),
  **Draw** (a finger or mouse canvas saved as a PNG) and **Voice note** (the
  microphone, saved in the recorder's own format); each uploads through
  `POST /api/communication/notes/attachment` (`action: upload`, base64, up
  to 10 MB, execution `attachment_upload`), then adds its link at the end of
  the note, which autosave writes. File names are made link-safe and never
  overwrite one already there (`-2`, `-3`). Only a saved note takes
  attachments (the folder is named after its file id); a new one says to
  save first. Cards and the editor load a file with `action: read` (base64
  through the note's own account, since an `<img>` cannot send the app's
  token) and show it from an object URL: pictures inline (cards, editor and
  the visual editor's blocks), voice notes as a player, other files as a
  download link; a card only names a voice note or file instead of fetching
  it. Each has a delete button that asks, deletes the file (`action: delete`)
  and takes its link out of the text. Deleting the link by hand leaves the
  file, as in Nextcloud Notes. Links are only followed when they are one
  file directly inside an `.attachments.<digits>` folder; anything else
  (`..`, absolute paths, URLs) never reaches WebDAV. The note's lifecycle
  carries them: archive, trash, restore and folder moves move the linked
  attachment folders beside the note; a rename (new file, then the old one
  deleted) hands them to the new copy (`delete` with `attachments_to`);
  duplicate copies the one linked folder under the copy's own file id and
  relinks it; delete forever deletes them; Empty trash deletes every
  attachment folder left in Trash. Listings skip anything inside an
  attachment folder, so a `.md` file there is never a note. Shared pieces,
  not copies: execution stores and reads files with the Nextcloud
  provider's `upload_file` / `download_file`; the voice note uses chat's
  recorder (`hooks/useAudioRecorder.ts`), the drawing the family board's
  `canvasPoint` / `paintSegment`, base64 goes through `lib/base64.ts` (also
  chat uploads and Bible read-aloud), and files show through the same
  per-page object-URL cache as chat photos (`lib/blobUrlCache.ts`).
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
- **Reminders (time)**: a saved note's **Remind me** panel offers "This
  evening 18:00" (while still ahead), "Tomorrow 9:00" or any date and time
  (`components/notes/ReminderPanel.tsx`). Setting one makes an ordinary
  personal reminder on the existing timers (`POST /api/communication/timers`,
  `type: reminder`, titled "📝 <note title>"), so it rings, notifies and
  snoozes exactly like reminders made on the Communication page, and is
  listed there. Timers now take an optional `url` (in-app paths only;
  execution's `app_link` drops anything with a scheme, `//host`, backslash or
  control character), which the notice opens: `/notes?note=<path>`. A snooze
  carries the link too. The shared index stores `reminder: {at, timerId}`
  for the note, so cards show a bell with the time while it is ahead. A
  change schedules the new timer before cancelling the old one; Remove
  cancels it ("no matching timer" means it already went off, which is fine).
  A rename keeps the reminder; archive and trash keep it too (the timer still
  fires, and the deep link falls back to the note's title if the path
  moved); delete forever and Empty trash cancel pending ones. A past time is
  refused in the panel, because the timers read a past clock time as that
  time tomorrow. Reminders are personal (the person who set it), not
  household-wide.
- **Reminders (place)**: the same panel's **At a place** part sets a "when I
  get home" reminder: pick one of the family's places (geo's `GET /zones`,
  the Home Assistant zones Wander lists) and **arrive** or **leave**. Geo
  keeps it (`services/geo/place_reminders.py`, Redis
  `geo:place_reminders:<user>`) and fires it from the arrivals and
  departures it already works out from every fix the phone posts
  (`_presence_transition`), so nothing extra runs on the phone: the Android
  shell's location service already posts with the app closed when location
  is "Allow all the time". The person's own movement, and only theirs,
  rings it once as a `timers` notice (`📍 Reminder: 📝 <note title>`,
  "Arrived at Home") that opens the note; then it is gone. The first fix
  after a quiet spell is not an arrival (geo cannot tell where they came
  from), so a reminder set while already at home waits for the next time
  they come back. The gateway (`GET/POST /api/geo/place-reminders`,
  `DELETE /api/geo/place-reminders/{id}`, `services/gateway/family_safety.py`)
  always acts for the caller; geo refuses a zone it does not have, a link
  outside the app (the timers' own rule, `services/common/links.py`), and
  more than 50 waiting per person. The panel reads places with Wander's
  `zonePlaces`, and both reminders change through one set-then-cancel path
  (`replaceReminder` in the page). The shared index
  keeps `placeReminder: {id, zone, zoneName, on}` for the card's pin chip
  ("Arriving at Home"); the page asks geo which are still waiting (only when
  some note has one, and again on Refresh), so a reminder that went off
  drops off the card and reads "went off" in the panel. A change sets the
  new one before removing the old; rename, archive and trash keep it;
  delete forever and Empty trash remove it. The panel shows loading, "no
  places yet" and load failures as such.
- **Sharing**: a saved note's **Share** panel (`components/notes/SharePanel.tsx`)
  shows who has it and shares it with someone on the family's Nextcloud,
  read only or with editing, through Nextcloud's own sharing (OCS
  `files_sharing/api/v1/shares`, user shares only) via the provider's existing
  OCS client, as the note's own account. The people offered come from
  Nextcloud's sharee search (`…/sharees`, itemType file), so they are exactly
  the users this account may share with (the caller is never offered, and
  someone already shared with is not offered again). Stop sharing deletes the
  share by its numeric id. The note stays one file: the other person sees it
  under Files → Shared with you, and in their Jarvis Notes once they move it
  into their Notes folder (Jarvis lists `Notes/` only). Public links and
  group shares are not offered. Failures show Nextcloud's own reason.
- **Version history**: an open, saved note has a **History** button that
  lists Nextcloud's earlier versions of the file (`components/notes/NoteHistory.tsx`).
  Nextcloud's Versions app (files_versions, on by default) keeps one each time
  the file changes, so autosave, saves from other Nextcloud clients and
  `check_off` all leave history; nothing extra is stored by Jarvis. Execution
  looks up the file's `oc:fileid` with a `Depth: 0` PROPFIND, then lists
  `/remote.php/dav/versions/<user>/versions/<fileid>`, reads a version with a
  GET and restores one with a WebDAV `MOVE` to `…/restore/target`, all as the
  note's own account. Pick a version to read it; **Restore this version**
  confirms first (and warns that unsaved edits will be dropped), then the
  editor reopens on the restored note. Nextcloud files the current text as a
  version before restoring, so a restore can be undone from the same list.
  Version ids must be all digits (they are Unix times) before they are put in
  a URL. A server without the Versions app answers 404, which reads as "No
  earlier versions yet"; other failures show the reason.
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
- **Offline editing** (`lib/noteOutbox.ts`): the drafts double as the
  outbox. A save with no connection (the browser reports offline, or the
  request fails with no answer: `ERR_NETWORK`, refused, host not found; a
  timeout is not counted, since that write may have landed) is answered at
  once instead of after a minute, and the draft is marked `queued`. The
  editor says "Offline: kept on this device, saves when you are back
  online", autosave stops retrying the same text, and the browser's `online`
  event saves it again. Closing the sheet offline keeps the queued draft
  ("Made offline: saves by itself…" in the Unsaved offer). The page replays
  every queued draft, oldest first, through the ordinary save on load, on
  `online` and when the app comes back to the front (not the one open in the
  editor, which saves itself). Each replay carries the version its edit
  started from as `If-Match` (or, without one, compares the stored text
  first), so a note changed elsewhere meanwhile is never overwritten: the
  draft becomes a conflict ("Changed somewhere else meanwhile"), and
  restoring it opens straight on Keep both / Use the stored version /
  Overwrite with mine. A refused replay whose stored note already holds the
  same text (the first attempt landed but its answer was lost) counts as
  saved. Other refusals keep the draft with the server's reason. Opening a
  note offline from the shelf's cached text keeps it open (the background
  read fails with a notice) instead of closing it; it has no version, so
  its save is checked against the stored text first.
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
checklist controls (drag, arrow-key and Alt+arrow reordering, delete),
version history with read and restore from Nextcloud's Versions app, timed
reminders on the existing timers whose notice opens the note, place
reminders ("when I get home") fired by geo from the location the phone
already shares, sharing with someone on the family's Nextcloud (view or
edit), attachments (photos, files, drawings, voice notes) stored the
Nextcloud Notes way beside the note, offline editing with a queue that
replays under the same If-Match rules.

Still open: nothing from the Keep list above. Attachments themselves need a
connection (an upload is not queued offline; the row shows the server's or
the network's error).

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
  note with kept changes can discard them for the stored text; choosing the
  stored version after a conflict drops the edit and reopens it
- `services/ui/src/test/NoteShare.test.tsx`: share with a household member
  (view only), the share shows and is not offered again, stop sharing; the
  empty and failure states
- `services/ui/src/test/NoteHistory.test.tsx`: versions list, read and
  restore (the editor reopens on the stored note), the empty and failure
  states, no History for an unsaved note
- `services/ui/src/test/NotesShortcuts.test.tsx`: C and / on the shelf, not in
  a field or with a modifier; Ctrl+S saves from a field, Ctrl/Cmd+Enter saves
  and closes
- `services/ui/src/test/NotesReminder.test.tsx`: a reminder becomes a
  `reminder` timer with the note's link and lands in the index and on the
  card; Remove cancels the timer by id; a past time and a refused timer show
  an error and change nothing; no reminder on an unsaved note
- `services/ui/src/test/NotesPlaceReminder.test.tsx`: a place reminder goes to
  geo with the note's link and lands in the index and on the card; changing
  it sets the new one before cancelling the old, Remove cancels it; the chip
  drops once geo no longer lists it; no places, a failed load and geo's
  refusal are shown as such
- `services/geo/tests/test_place_reminders.py`: arriving home rings the
  person's reminder once (only them, then it is gone); someone else's
  arrival and the wrong movement do not; a first sighting is not an
  arrival; bad zones, movements, titles and off-app links are refused;
  removal is by id and owner; the routes add, list and remove, and refuse
  an unknown place or a missing secret.
  `services/gateway/tests/test_place_reminders_proxy.py`: the gateway always
  asks geo for the caller, whatever the body says, and passes geo's refusals
  on
- `services/execution/tests/test_timer.py` (reminder part): a reminder stores
  its in-app link and reports when it fires, an off-app link is dropped, the
  notice opens the link and a snooze keeps it; `app_link` accepts only app
  paths. `services/gateway/tests/test_push_callbacks_timers.py`: a snoozed
  note reminder keeps its link
- `services/ui/src/test/noteDrafts.test.ts`: per-account drafts, the newest-20
  cap, a damaged store, storage that throws
- `services/ui/src/test/NotesOffline.test.tsx`: an edit made offline is
  queued (no write) and saved with its version once `online` fires; one
  closed offline is saved by the shelf when the connection returns; a
  replay over a note changed elsewhere becomes a conflict that opens on the
  choice; a replay whose first attempt landed counts as saved; nothing is
  replayed while still offline; a lost connection is told from a refusal
- `services/ui/src/pages/Recipes.test.tsx`: edit in place, rename, delete
- `services/gateway/tests/test_notes_rag_sync.py`: indexing writes to RAG;
  the list route passes `previews` through; the write route passes `if_match`;
  the versions and share routes map their actions and refuse anything else;
  a note reminder reaches the timers with its link
- `services/ui/src/test/NotesAttachments.test.tsx`: a note's photo shows on
  its card and inside the visual editor and saves back unchanged; a photo
  upload goes beside the note and its link lands in the saved text; delete
  asks, removes the file and the link; a voice note and a drawing upload and
  link; a refused upload is shown and changes nothing; a new note asks to be
  saved first. `noteAttachments.test.ts`: link parsing (relative attachment
  links only), embed vs link, removal, base64, and the visual editor's exact
  round trip of attachment lines (a link inside a sentence still opens as
  text). `services/ui/src/pages/Recipes.test.tsx`: a rename hands its
  attachments to the new path
- `services/execution/tests/test_note_attachments.py`: upload lands in
  `.attachments.<fileid>/` beside the note with a link-safe, never
  overwriting name; unsaved, empty, garbled and oversized uploads are
  refused; read follows only attachment links; delete removes one file;
  delete forever drops the folders, a rename carries them (same folder:
  untouched), archive moves them, duplicate copies them under its own id;
  files inside an attachment folder are never listed as notes.
  `services/gateway/tests/test_notes_rag_sync.py`: the attachment route maps
  its actions and the delete route passes `attachments_to`
- `services/ui/src/test/noteRichText.test.ts` / `RichNoteEditor.test.tsx`:
  markdown round trips, the unsupported-note fallback, span whitelist, tick,
  Enter and toolbar behaviour, span rendering in previews; checklist items
  move by grip arrow keys (focus follows), by pointer drag and by Alt+arrow,
  and delete one at a time
- `services/ui/src/test/notesMeta.test.ts`: checklist parsing, previews,
  sorting, pin/colour storage, reminders kept or dropped by shape, the layout
  preference (and its fallback when storage throws)
- `services/execution/tests/test_note_actions.py`: `write` replaces (single
  PUT) without stacking headers, `check_off` toggles and reports missing items,
  previews are read once and reused, a save forgets the cached text, reads run
  at most four at a time, an unreadable note does not fail the list, a
  timeout is named in the error, the folder check is `Depth: 0`, `read`
  returns the ETag, an in-place `write` sends `If-Match` and returns the new
  ETag, a 412 is a conflict, a new note never sends `If-Match`; versions are
  listed newest first from the fileid folder, read and restored (MOVE to
  `restore/target`), a missing Versions app is an empty list, a non-numeric
  version id never reaches a URL, a missing note says so; sharing lists user
  shares only, shares read only or editable (permissions 1 or 3), refuses
  nobody or yourself, removes by numeric id only, offers each other user once
  and passes on Nextcloud's reason
- `services/execution/tests/test_http_client_session_retire.py`: a pooled
  session that ages out keeps serving the requests already on it

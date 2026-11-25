# Export my data

Every person can save an encrypted copy of everything Jarvis holds for them,
read it later, and restore it into their account on this server or a new one
(Settings, Account: **Export my data** and **Open an export file**). An admin
can do the same for a person they manage (Admin, Users: the archive button).

The file is **always encrypted and never readable without its password**. The
password never leaves the device that made the file: the server streams the
person's data to the signed-in device, and the device encrypts it as it
arrives. No clear copy is ever written to the server's disk.

The parents' private chat is not part of this export. It has its own encrypted
backup, opened with the chat password (docs/PRIVATE_CHAT_BACKUP.md).

## What is in it

How it was decided: a row is in the export when it is about the person and
keyed by their username (or user id); it is left out when it is a secret, when
it belongs to someone else, or when another system owns it and has its own
export. Things a parent decides for a child (screen time rules, kid mode,
recommendation lanes) are in the child's file so they can read them, but a
restore never writes them back, so a file cannot loosen them. A child's data
is never in a parent's own export; an admin exports the child's account
separately (identity's `may-manage`: the person themselves, or an admin or a
holder of `family.manage`, which is what Admin, Users already allows).

| Service | Included | Left out, and why |
|---|---|---|
| identity | Profile (name, family role, picture choice, chosen voice, the addresses and user names of connected services, the book catalogue choices), profile picture, theme, weather, calendar settings (including any calendar subscription links), notification choices, activity sharing, which profile areas the family may see, dashboard widgets, workspace sessions, workspace chats and their messages, conversations with Jarvis. For reading only: features used, screen time rules, screen time, app and unlock requests, chores, journal notes the person wrote, the account's own audit history | Password hash, API keys (the Google Books key among them), every stored service password or token (`*_enc`, `*_hash`), linked-account tokens (Spotify), push registrations and the ntfy topic, the guard's offline-code verifier, the family chat token: secrets, never exported even encrypted. Voice signature: a biometric template, enrol again. Devices and device assignments: household equipment. Credential shares: grants between accounts |
| health | Profile, preferences, measurements, sleep, check-ins, food, fasting, goals, cycle settings, periods and logs, recipe ratings and recipes made. For reading only: who it is shared with, cheers sent and received, challenges joined | Food product cache (not personal). Other people's health data, even when shared with the person |
| bible | Reading position and preferences, highlights and bookmarks, memory verses, reading plans and reflections, reading history, achievements, game scores, study sessions with their questions. For reading only: plan groups joined | The Bible text and every shared corpus. A highlight's note text lives in the person's Nextcloud notes (only its path is here) |
| geo | Saved places the person owns (read from and restored to HomeHub, where places live; skipped with no HomeHub), place reminders, trips, workouts, daily steps, goals, earned achievements. For reading only: places they stopped and the location trail (up to the 30 days geo keeps), sent page by page | The household's shared places and other people's trips. A trip's riders are dropped on restore |
| Nextcloud (notes, recipes and meal plans, calendar, files, family chat on Talk) | Not included | They are in the person's own Nextcloud account, which has its own export; reaching them would mean the server using the person's Nextcloud password for this |
| telemetry, rag, storage, automation, homehub | Not included | Screen time and app activity reports are kept for the household for a limited time; the knowledge index is derived from files; artifacts are downloadable where they are; timers are short-lived; the hub holds household devices |

## Where the encryption happens

On the device (`services/ui/src/lib/accountExport/crypto.ts`), with WebCrypto.
The gateway (`services/gateway/account_export.py`) only gathers and streams:
`POST /api/account/export` answers NDJSON, one service at a time (and the
location trail one page at a time), so neither side holds the whole export in
memory: the device encrypts each 1 MiB chunk as it arrives and folds the
ciphertext into a Blob, which a browser may keep on disk. A stream that does
not finish with its end line is refused and nothing is saved.

The trade-off considered: encrypting on the server would let a very large
export be written without the device's memory, but the server would then see
the password. The data here is a few megabytes for most people (the location
trail is the largest part, bounded at 30 days), so the device does it.

### The key

PBKDF2-SHA256, 600,000 rounds or more, 16-byte random salt, to an AES-256-GCM
key. Argon2id and scrypt are not in WebCrypto (or in the gateway image's
Python); a WASM library would be a new dependency in every build. PBKDF2 at
this count is what the private chat already uses, and it lets the
self-contained page repeat the decryption with nothing but the browser.
A file that asks for fewer rounds is refused. The password must be at least
10 characters and pass the strength hint.

### The file format (version 1)

All integers are big-endian.

    MAGIC          12 bytes  "JARVISACCT" 0x00 0x01
    header length   4 bytes
    header         JSON (UTF-8)
    chunk 0 .. n   AES-256-GCM ciphertext + 16-byte tag each

The header (readable without the password, and holding nothing personal):
`format` (`jarvis-account-export`), `version`, `kdf` (`name`, `iterations`,
`salt`), `cipher`, `chunk_bytes`, `nonce_prefix` (4 random bytes),
`compression` (`gzip` or `none`), `content` (`application/x-ndjson`),
`created_at`, and `check`.

* `check` is AES-GCM of 16 zero bytes with nonce `prefix || 2^64-1` and
  associated data `jarvis-account-export/v1/check`: a wrong password is told
  apart from a damaged file before any chunk is read.
* The payload is cut into chunks of `chunk_bytes`. Chunk `i`'s nonce is
  `prefix || i` (8 bytes); its associated data is
  `sha256(MAGIC || header length || header) || i || final` (final is one byte,
  1 only for the last chunk). Every chunk but the last is exactly
  `chunk_bytes` long and the last is always shorter (empty when the payload is
  an exact multiple). So a changed header, a changed, reordered, dropped or
  duplicated chunk, a truncation and trailing bytes are all refused.
* The clear payload (gzip-compressed when the device can) is NDJSON:
  `{"kind":"manifest", "subject", "exported_by", "services", "excluded", ...}`,
  then `{"kind":"section", "service", "collections": {name: [rows]}}` lines (a
  service can have several; they are joined), then
  `{"kind":"end", "sections", "failed"}`. A part the server could not read is
  a section with `error` and is named in `failed`; the device says so.

## Reading a file

**Open an export file** asks for the file and its password, checks the
password, checks every chunk and the end line, and shows a read-only copy
grouped by service and kind, 25 records at a time, long lists split by month
with their counts.

Every kind of record reads the way the app shows it (`present.ts`): a weight
with its date, a night's sleep with its times, a highlight with its verse and
colour, a trip from one place to another with its day, times and distance, a
conversation with Jarvis or a workspace chat as bubbles. Dates, times and
numbers follow the reader's locale; distances, weights and water follow the
health profile's unit choice, else the locale. Internal ids, keys, raw flags
and field names are never shown. A kind with no formatter is shown as a tidy
card with plain labels and no ids, and two tests keep that from happening by
accident: `tests/unit/test_account_export_kinds.py` keeps `kinds.json` equal
to what the services export, and the vitest test checks every kind in it has
a formatter.

**Save as an encrypted page** writes one HTML file holding the encrypted file
unchanged and a small decryptor (`page.ts`) that asks for the password and
shows the same read-only copy (the same `presentExport`, embedded as source
text and checked to still run after the build minifies it) in any modern browser, offline, with a
`default-src 'none'` content policy. The page never holds the data in clear.

A plain readable file is deliberately not offered: every copy Jarvis makes is
encrypted, and the encrypted page already gives a readable copy anywhere with
the password. Someone who wants text can copy it from the page.

## Restoring

From an opened file, **Restore into my account** sends one service's part at a
time to `POST /api/account/import`, first as a dry run that answers what it
would do, then, after the person confirms, for real. Each service merges with
`services/common/account_data.py` (geo with its own Redis version):

* The owner of every row is set to the account being restored, whatever the
  file says. A file cannot write into anyone else's account.
* Rows are matched by a stable id: a natural key within the owner where there
  is one (a measurement's source and external id, a check-in's day, a
  highlight's verse and creation time, ...), otherwise the row's id when that
  row already belongs to the person. A row with no match is added, keeping its
  id when it is free and taking a new one when it is not (a new server); child
  rows (chat messages, study questions) follow their parent's new id.
* When the file's copy is newer (`updated_at`), it updates the row. When the
  server's copy is newer, or the two differ and there is no time to compare,
  it is a **conflict**: the summary counts them and they are replaced only if
  the person ticks "Also replace". Nothing is ever deleted.
* Rows marked for reading only (above) are counted as kept and never written.
  The location trail is never sent back.
* An admin can restore for a person they manage (for example a child who
  cannot do it themselves): the restore goes into the account the file belongs
  to, and only an admin can restore a file that is not their own.

A restore never brings back a password, key or login: sign in to the services
again from Accounts & Integrations.

## Rate limits and audit

* One export per `ACCOUNT_EXPORT_COOLDOWN_S` (default 60) per person, and at
  most `ACCOUNT_IMPORT_MAX_PER_WINDOW` (60) restore calls per
  `ACCOUNT_IMPORT_WINDOW_S` (600) per person (gateway environment, read once
  at startup).
* Every export and every applied restore (not the dry runs) is in identity's
  audit log as `account.export` / `account.import`, with who did it and for
  whom.

## Code

| Where | What |
|---|---|
| `services/common/account_data.py` | Table specs, export without secrets, the merge rules, the two internal routes |
| `services/identity/account_export.py` | Identity's tables, `may-manage`, the audit route |
| `services/health/account_export.py`, `services/bible/account_export.py` | Their tables |
| `services/geo/account_export.py` | Redis export, the paged location trail, the merge |
| `services/gateway/account_export.py` | `/api/account/export` and `/api/account/import` |
| `services/ui/src/lib/accountExport/` | The file format, the viewer model, the encrypted page |
| `services/ui/src/components/settings/AccountExportPanel.tsx` | The Settings rows and sheets; Admin, Users uses `ExportSheet` |

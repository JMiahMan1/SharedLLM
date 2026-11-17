# Private chat backups

The parents' private chat is end to end encrypted, and so are its backups:
**a backup can be opened only with the chat password.** Nothing of the chat
is ever written to a backup, an export or a temp file in clear.

## What is in a backup

The nightly archive (`scripts/backup_databases.sh`, `data/backups/sharedllm-<stamp>.tar.gz`)
holds, for the private chat, only one file per chat:

    folders/private_chat/<token>.jarvis-backup

Inside it (encrypted) is that chat's whole folder, laid out as it is under
`PRIVATE_CHAT_DIR`, so a restored folder can be put straight back:

| Path | What |
|---|---|
| `chats/<token>.json` | The registry record: members, key record (salt, wrapped key, verifier), password history, the backup key |
| `messages/<token>/` | The message store: sealed envelopes, authors, times, read markers |
| `blobs/<token>/` | Encrypted pictures (and anything else kept as a blob) |
| `gallery/<token>/` | Sealed gallery entries |
| `marks/<token>/` | Sealed edits and reactions, and who has seen what |
| `beloved/<token>/` | Beloved's sealed records |
| `migration/<token>/` | The Talk exports written when the chat moved off Talk |

Every `<kind>/<token>/` folder is included, so a kind added later is backed
up without a change here. Files under `PRIVATE_CHAT_DIR` that belong to no
chat are named in the log and left out (never archived in clear).

Readable in the file, outside the encryption: the chat token, when the backup
was made, the algorithms, the public keys, the sealed private key and the
chat's key records (salts, wrapped keys and verifiers: exactly what the
server holds, never `auth_hash` or anyone's name).

## How it is protected

1. **A backup key pair per chat.** The first time a member unlocks the chat on
   any screen (the thread, Beloved, the gallery, a call), the phone makes an
   ECDH key pair (X25519; P-256 where the WebView has no X25519), seals the
   private key with the chat's data key DK (the same envelope as a message,
   record kind `backup-key`) and uploads the public key and the sealed
   private key (`POST /api/private-chat/chats/<token>/backup-key`). The
   server never replaces it unless asked to rotate (`"rotate": true`; the old
   pair is kept in `backup_keys_retired`). A password change does not touch
   it: DK does not change.
2. **Each backup is encrypted to that public key**
   (`scripts/private_chat_backup.py`): an ephemeral ECDH key pair with the
   chat's public key, HKDF-SHA256 to an AES-256-GCM key, and the chat's tar
   streamed through AES-GCM in 1 MiB chunks. Each chunk's nonce carries its
   index; its associated data binds the header's hash, the index and a final
   flag, so a changed header, a changed, reordered, dropped or duplicated
   chunk, a truncation or extra bytes are all refused. The tar is streamed
   straight into the encryption: no clear tar touches the disk.
3. **Opening it needs the password**: the password unwraps DK (as the app
   does, v1 and v2 records), DK unseals the private key, the private key
   opens the file. The server and whoever holds the archive have none of these.

The file format is described in full at the top of `scripts/private_chat_backup.py`.

## In the app: back up, export, open and restore

Everything below needs the chat password, and nothing below ever produces a
copy that can be read without it.

### Back up and export (Lock and password, Back up and export)

A member of the unlocked chat asks the gateway for the chat's backup file
(`GET /api/private-chat/chats/<token>/export`, services/gateway/private_chat_export.py).
It is made by the nightly encrypter's own code (`write_backup` from
`scripts/private_chat_backup.py`, loaded as a module, so the format is the
same file for file): the chat's folder is streamed as a tar straight through
the encryption into the response. No clear tar is written anywhere, and the
response is ciphertext from its first byte. The file is named
`private-chat-<date>.jarvis-backup`, with no names in it.

* Members only (anyone else gets the chat's usual 404), the chat must have its
  backup key (it is made the first time the chat is unlocked), and at most
  `EXPORT_LIMIT` exports per chat per hour, one at a time (429 with
  `Retry-After`).
* The phone saves it where the person chooses: in the Android app the system
  "Save to" picker (`lib/saveFile.ts`, the `JarvisSaveFile` plugin), in a
  browser a normal download.
* **Save as an encrypted page** wraps the same file in one HTML page that asks
  for the password (below). There is no plain readable export: the rule is
  that a copy is never readable without the password.
* Not offered: copying it to Nextcloud Files from here. The person can upload
  the saved file there themselves; it stays encrypted wherever it goes.

### Open a backup file (Lock and password, or "Have a backup? Open it" on the start screen)

Pick a `.jarvis-backup` (or an encrypted page saved earlier) and enter the
chat password. The device does everything (`lib/privateChatBackupCore.js`,
WebCrypto, never the network):

1. The password opens the data key DK from the header's key records (v1 and
   v2, the current one and the password history; when opened from a chat, that
   chat's current record is tried too, for a password set after the backup).
2. DK unseals the backup private key; ECDH with the header's ephemeral key and
   HKDF give the file key.
3. Every 1 MiB chunk is decrypted and checked (its place, the header, the
   final flag) as it is read; a changed, moved, dropped or added chunk, a cut
   or extra bytes refuse the whole file and nothing is shown.
4. The tar inside is read into memory (only this chat's paths are accepted)
   and the **keepsake** opens: read-only, the thread with dates, authors,
   edits, reactions, replies, deleted messages marked, pictures and video
   clips (decrypted when shown), the gallery and every Beloved section. It
   closes, and its decrypted pictures are dropped, when it is closed or the
   app goes to the background.

**The encrypted page** (`lib/privateChatBackupFile.ts` `buildKeepsakePage`) is
one self-contained HTML file: the backup, still encrypted, as base64, and the
same decryptor (the very text of `privateChatBackupCore.js`) with a small
viewer. Its content security policy lets it load nothing and send nothing. It
opens in any current browser from a file, asks for the password, and shows
the same keepsake; Lock forgets everything. Opening it needs X25519 in the
browser for a chat whose phone made an X25519 key (Chrome 133+, Safari 17+,
Firefox 130+); P-256 keys open everywhere.

### Restore into Jarvis (from the keepsake)

The device has opened and checked the file; it then sends the chat's records
back exactly as they were in it, sealed, never decrypted:

1. `POST /api/private-chat/restore` with the registry record from the backup
   (members, key records, the backup key: what the server keeps for any chat,
   reduced to those fields).
   * **The chat exists here**: the caller must be in it, the members must be
     the backup's, and the verifier must match (otherwise nothing in the
     backup would open with this chat's key). If it already has data the
     server answers 409 with counts and the app asks: **merge**? Merging adds
     only what is missing.
   * **It does not**: it is made again for the two people the backup names,
     if both are parents with a chat account here and have no other private
     chat together. Otherwise the 409 says who is missing and what to do.
   * Either way a restore session opens for the caller (two hours), and the
     other parent gets a notice with no content ("Sam restored the private
     chat from a backup").
2. `GET .../restore/have`, then `PUT .../restore/blobs/<id>` for each blob not
   here yet (opaque ciphertext under its own id; one already here is never
   replaced).
3. `POST .../restore/records` in batches: messages with their ids, authors,
   times, replies, tombstones and blob lists; gallery entries; edits and
   reactions; Beloved records; read markers and the id floor. Every record
   must be a well-formed sealed envelope by one of the two members; one bad
   record refuses its whole batch.
4. `POST .../restore/finish` closes the session.

Merge rules (by id, never duplicated): a message already here is kept as it
is (a deletion here stays deleted); a reaction is added if its id is new
(who has seen it is united); an edit is added only to a message with none; a
Beloved record keeps whichever version was changed last. The Talk migration
exports a backup may hold are not restored.

What the server sees during all this: the same ciphertext, ids, authors and
times it held before, never a password, a key or a word. What it cannot
check: who really wrote a sealed record. A member could add records in the
other's name during a restore session, which is why a session is short, only
for the member who opened it, and announced to the other parent.

### Limits worth knowing

* The whole backup is held in the device's memory while it is opened (and,
  in the Android app, once more as base64 while it is saved). A chat with
  many videos can be hundreds of MB; a phone may run out of memory on a very
  large one. The nightly backups and `scripts/open_private_chat_backup.py` on
  a computer have no such limit.
* The Android "Save to" picker and opening the encrypted page from a phone's
  Files app were not checked on a device for this change.

## Checking that a chat has a key

* Members: `GET /api/private-chat/chats/<token>/backup-key` answers
  `{"has_backup_key": true, "backup_key": {"alg", "public_key", "key_id", "created_at"}}`.
* Admin: `GET /api/private-chat/backup-keys` lists every chat's token,
  `has_backup_key`, `alg`, `key_id` and `created_at` (no names, nothing secret).
* The nightly log: a chat without a key prints
  `private chat <token> has no backup key yet: open the chat on a phone once (Family, Private chat, then unlock it; ...)`
  and the run exits 3. That chat is left out of the archive, never copied in clear.

## Where the encryption runs, and why

The encrypter needs Python's `cryptography` (X25519/P-256, HKDF, AES-GCM in C:
a pure Python AES would take hours over the pictures). The host has `openssl`
and `python3` but `cryptography` is not guaranteed there, and `openssl enc`
cannot do AES-GCM. So the backup script:

1. uses the host's `python3` (`BACKUP_PYTHON`) when it can import `cryptography`;
2. otherwise runs the same script in the gateway image, which always has it
   (`BACKUP_CRYPTO_IMAGE`, default `ghcr.io/jmiahman1/sharedllm-gateway:latest`):
   `docker run --rm --network none --user <you>` with the chat folder mounted
   read-only. It needs only the image (already pulled for the gateway), not a
   running gateway;
3. with neither, leaves the private chat out, says so, and exits 3.

## Restoring

You need the backup file, the chat password, and Python 3 with `cryptography`
(`pip install cryptography`, or run inside the gateway image).

    tar -xzf sharedllm-<stamp>.tar.gz ./folders/private_chat/<token>.jarvis-backup
    python3 scripts/open_private_chat_backup.py folders/private_chat/<token>.jarvis-backup restored/

It asks for the password (never take it on the command line), checks every
chunk, and writes the chat's folder into `restored/` (mode 700). Any password
the chat had when the backup was made works. For a password set after the
backup, add the live registry record and its current and earlier records are
tried too:

    python3 scripts/open_private_chat_backup.py BACKUP restored/ --registry data/private_chat/chats/<token>.json

To put a chat back, stop the gateway, copy `restored/*` over
`data/private_chat/` and start it again.

Check a backup without opening it: `python3 scripts/private_chat_backup.py inspect BACKUP`.

## Sealing a clear snapshot that already exists

A clear copy taken before this existed (a `.tar.gz` of the folder, a Talk
export `.json`) can be encrypted to a chat's backup key and the clear file
destroyed:

    python3 scripts/private_chat_backup.py seal-file --root data/private_chat --token <token> /path/to/snapshot.tar.gz

It writes `snapshot.tar.gz.jarvis-backup` (the file is inside it as
`snapshots/snapshot.tar.gz`), checks the result's structure, then overwrites
the clear file with random bytes, syncs it and deletes it (`--keep-clear` to
keep it). It refuses a chat that has no backup key yet and leaves the clear
file alone. On a copy-on-write or flash filesystem an overwrite may not reach
the original blocks, so treat the deletion as best effort and do not keep
clear snapshots anywhere else.

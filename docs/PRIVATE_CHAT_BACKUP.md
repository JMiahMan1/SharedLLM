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

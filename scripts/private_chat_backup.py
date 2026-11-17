#!/usr/bin/env python3
"""Encrypted backups of the parents' private chat: only the chat password opens one.

The private chat (services/gateway/private_chat.py) is end-to-end encrypted,
but what the server keeps beside the ciphertext (who wrote, when, how many,
which kinds, who has seen what) is readable. So the nightly backup never
archives the folder as it is. For every chat it writes one file,
``<token>.jarvis-backup``, that holds the chat's whole data (its registry
record and every ``<kind>/<token>/`` folder: messages, blobs, gallery, marks,
beloved, the Talk migration exports, and any kind added later), encrypted to
the chat's **backup public key**.

The key pair is made on a member's phone the first time the chat is unlocked
(services/ui/src/lib/privateChatBackupKey.ts). The private key is sealed with
the chat's data key DK (the same envelope as a message) and only its sealed
form ever reaches the server. DK is wrapped by the password, so a backup can
be opened only by someone who knows the chat password (or a phone already
holding DK), never by the server or whoever holds the archive.

File format (version 1), all integers big-endian::

    MAGIC                   12 bytes  b"JARVISPCBK\\x00\\x01"
    header length            4 bytes
    header                   JSON (UTF-8), see ``make_header``
    chunk 0 .. chunk n       AES-256-GCM ciphertext + 16-byte tag each

* Key agreement: ephemeral-static ECDH (X25519, or P-256 for a phone whose
  WebView had no X25519) between a fresh key pair and the chat's backup
  public key.
* HKDF-SHA256 (salt: 32 random bytes in the header; info: ``INFO`` plus the
  token and both public keys) gives 36 bytes: the AES-256 key and a 4-byte
  nonce prefix.
* The payload (a tar stream) is cut into chunks of ``chunk_bytes`` of
  plaintext. Chunk ``i``'s nonce is ``prefix || i`` (8 bytes) and its
  associated data is ``sha256(MAGIC || header length || header) || i || final``
  (final is one byte, 1 only for the last chunk). Every chunk but the last is
  exactly ``chunk_bytes`` long and the last is always shorter (empty when the
  payload is an exact multiple), so a reorder, a dropped or duplicated chunk,
  a truncation at any point, trailing bytes, or any change to the header is
  refused.
* The header carries the sealed private key and the chat's key records (the
  current one and its password history: salts, wrapped keys and verifiers,
  exactly what the server holds, never ``auth_hash`` or anyone's name), so a
  backup plus a password that was ever the chat's is enough to open it
  (scripts/open_private_chat_backup.py).

Usage::

    private_chat_backup.py encrypt-all --root data/private_chat --out DIR
        One <token>.jarvis-backup per chat with a backup key. Exit 3 when a chat
        has no key yet (it is NOT written in clear), after doing every other chat.
    private_chat_backup.py seal-file --root data/private_chat --token T FILE
        Encrypt an existing clear file (a snapshot .tar.gz or a Talk export) to
        the chat's backup key, check the result, then overwrite and delete FILE.
    private_chat_backup.py inspect FILE
        Print a backup's header (no secrets in it) after checking its structure.

Needs the ``cryptography`` package (scripts/backup_databases.sh runs this with
the host's python3 when it has it, otherwise inside the gateway image).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import secrets
import struct
import sys
import tarfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, BinaryIO

MAGIC = b"JARVISPCBK\x00\x01"
FORMAT = "jarvis-private-chat-backup"
VERSION = 1
SUFFIX = ".jarvis-backup"
CHUNK_BYTES = 1024 * 1024
TAG_BYTES = 16
MAX_HEADER_BYTES = 1024 * 1024
INFO = b"jarvis-private-chat-backup/v1"
ALGS = ("X25519", "P-256")
#: What a phone seals as the backup key (privateChatBackupKey.ts).
RECORD_KIND = "backup-key"

_TOKEN = re.compile(r"^[A-Za-z0-9]{4,64}$")
_FENCE = "```jarvis-envelope"


class BackupError(Exception):
    """A backup that cannot be made or opened, with a reason safe to print."""


# --- Key material -------------------------------------------------------------


def _b64d(value: Any, field: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise BackupError(f"{field} is missing")
    try:
        return base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise BackupError(f"{field} is not base64") from exc


def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def read_sealed(envelope: str) -> dict[str, str]:
    """The ``{iv, ct}`` inside a sealed envelope string (the device's sealRecord)."""
    start = envelope.find(_FENCE)
    end = envelope.find(_FENCE, start + len(_FENCE)) if start != -1 else -1
    if start == -1 or end == -1:
        raise BackupError("the sealed private key is not a sealed envelope")
    try:
        parsed = json.loads(envelope[start + len(_FENCE):end].strip())
    except ValueError as exc:
        raise BackupError("the sealed private key is not a sealed envelope") from exc
    if not isinstance(parsed, dict) or parsed.get("kind") != "sealed":
        raise BackupError("the sealed private key is not a sealed envelope")
    iv, ct = _b64d(parsed.get("iv"), "sealed.iv"), _b64d(parsed.get("ct"), "sealed.ct")
    if len(iv) != 12 or len(ct) <= TAG_BYTES:
        raise BackupError("the sealed private key has the wrong size")
    return {"iv": parsed["iv"], "ct": parsed["ct"]}


def load_public_key(alg: str, raw: bytes):
    from cryptography.hazmat.primitives.asymmetric import ec, x25519

    if alg == "X25519":
        if len(raw) != 32:
            raise BackupError("an X25519 public key is 32 bytes")
        return x25519.X25519PublicKey.from_public_bytes(raw)
    if alg == "P-256":
        if len(raw) != 65 or raw[0] != 4:
            raise BackupError("a P-256 public key is 65 bytes, uncompressed")
        return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
    raise BackupError(f"unknown key agreement {alg!r}")


def _public_raw(key) -> bytes:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import x25519

    if isinstance(key, x25519.X25519PublicKey):
        return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def ephemeral(alg: str):
    """A fresh key pair for one backup file."""
    from cryptography.hazmat.primitives.asymmetric import ec, x25519

    return x25519.X25519PrivateKey.generate() if alg == "X25519" else ec.generate_private_key(ec.SECP256R1())


def exchange(private, public) -> bytes:
    from cryptography.hazmat.primitives.asymmetric import ec, x25519

    shared = private.exchange(public) if isinstance(private, x25519.X25519PrivateKey) else private.exchange(ec.ECDH(), public)
    if not any(shared):
        raise BackupError("the key agreement gave an all-zero secret")
    return shared


def derive(shared: bytes, salt: bytes, token: str, eph_pub: bytes, recipient_pub: bytes) -> tuple[bytes, bytes]:
    """The AES-256 key and the 4-byte nonce prefix for one file."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    info = INFO + struct.pack(">H", len(token)) + token.encode() + eph_pub + recipient_pub
    out = HKDF(algorithm=hashes.SHA256(), length=36, salt=salt, info=info).derive(shared)
    return out[:32], out[32:]


def _nonce(prefix: bytes, index: int) -> bytes:
    return prefix + struct.pack(">Q", index)


def _aad(header_digest: bytes, index: int, final: bool) -> bytes:
    return header_digest + struct.pack(">QB", index, 1 if final else 0)


# --- Key records (what the chat password unlocks) -----------------------------


def _record_from(fields: dict[str, Any]) -> dict[str, Any] | None:
    """The public key record of a registry entry or history entry, in the device's shape."""
    verifier = fields.get("verifier")
    if not isinstance(verifier, dict):
        return None
    if fields.get("version") == 2 or fields.get("wrapped_key"):
        kdf = fields.get("kdf")
        if not isinstance(kdf, dict) or not isinstance(fields.get("wrapped_key"), dict):
            return None
        return {"version": 2, "kdf": {"salt": kdf.get("salt"), "iterations": kdf.get("iterations")},
                "wrapped_key": dict(fields["wrapped_key"]), "verifier": dict(verifier)}
    if "salt" not in fields:
        return None
    return {"version": 1, "kdf": {"salt": fields.get("salt"), "iterations": fields.get("iterations")},
            "verifier": dict(verifier)}


def key_records(chat: dict[str, Any]) -> list[dict[str, Any]]:
    """The current key record, then every earlier one (newest first): any password the chat ever had opens it."""
    out = []
    for fields in [chat, *reversed(chat.get("history") or [])]:
        if isinstance(fields, dict):
            record = _record_from(fields)
            if record and record not in out:
                out.append(record)
    return out


# --- Writing ------------------------------------------------------------------


def make_header(chat: dict[str, Any], *, payload: str, now: int | None = None) -> tuple[dict[str, Any], Any]:
    """A header for one file to the chat's backup key, and the ephemeral private key."""
    backup = chat.get("backup_key")
    if not isinstance(backup, dict):
        raise BackupError("this chat has no backup key yet")
    alg = backup.get("alg")
    if alg not in ALGS:
        raise BackupError(f"unknown key agreement {alg!r}")
    recipient_raw = _b64d(backup.get("public_key"), "backup_key.public_key")
    load_public_key(alg, recipient_raw)
    eph = ephemeral(alg)
    header = {
        "format": FORMAT,
        "version": VERSION,
        "payload": payload,
        "token": chat["token"],
        "created_at": int(now if now is not None else time.time()),
        "alg": alg,
        "kdf": "HKDF-SHA256",
        "cipher": "AES-256-GCM",
        "chunk_bytes": CHUNK_BYTES,
        "key_id": backup.get("key_id") or hashlib.sha256(recipient_raw).hexdigest()[:32],
        "recipient_public_key": _b64e(recipient_raw),
        "ephemeral_public_key": _b64e(_public_raw(eph.public_key())),
        "hkdf_salt": _b64e(secrets.token_bytes(32)),
        "sealed_private_key": read_sealed(str(backup.get("sealed_private_key") or "")),
        "key_records": key_records(chat),
    }
    return header, eph


class EncryptingWriter(io.RawIOBase):
    """A write-only stream that encrypts into ``out`` chunk by chunk. Call ``finish()`` once."""

    def __init__(self, out: BinaryIO, header: dict[str, Any], eph) -> None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        recipient = load_public_key(header["alg"], _b64d(header["recipient_public_key"], "recipient_public_key"))
        key, self._prefix = derive(
            exchange(eph, recipient), _b64d(header["hkdf_salt"], "hkdf_salt"), header["token"],
            _b64d(header["ephemeral_public_key"], "ephemeral_public_key"),
            _b64d(header["recipient_public_key"], "recipient_public_key"),
        )
        self._aes = AESGCM(key)
        head = json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
        if len(head) > MAX_HEADER_BYTES:
            raise BackupError("the header is too large")
        lead = MAGIC + struct.pack(">I", len(head)) + head
        self._digest = hashlib.sha256(lead).digest()
        self._out = out
        self._out.write(lead)
        self._buf = bytearray()
        self._index = 0
        self._done = False
        self.plain_bytes = 0

    def writable(self) -> bool:
        return True

    def write(self, data) -> int:  # type: ignore[override]
        if self._done:
            raise ValueError("the backup is already finished")
        view = bytes(data)
        self._buf += view
        self.plain_bytes += len(view)
        # Keep the last full chunk back: only finish() knows whether it is the final one.
        while len(self._buf) > CHUNK_BYTES:
            self._emit(bytes(self._buf[:CHUNK_BYTES]), final=False)
            del self._buf[:CHUNK_BYTES]
        return len(view)

    def _emit(self, plain: bytes, *, final: bool) -> None:
        self._out.write(self._aes.encrypt(_nonce(self._prefix, self._index), plain, _aad(self._digest, self._index, final)))
        self._index += 1

    def finish(self) -> None:
        if self._done:
            return
        if len(self._buf) == CHUNK_BYTES:
            self._emit(bytes(self._buf), final=False)
            self._buf.clear()
        self._emit(bytes(self._buf), final=True)
        self._buf.clear()
        self._done = True


def _chat_files(root: Path, token: str) -> Iterator[tuple[Path, str]]:
    """Every file that belongs to one chat, with its path inside the backup (relative to the root)."""
    record = root / "chats" / f"{token}.json"
    if record.is_file():
        yield record, f"chats/{token}.json"
    for kind in sorted(p for p in root.iterdir() if p.is_dir() and p.name != "chats"):
        folder = kind / token
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*")):
            if path.is_file() and not path.is_symlink() and not path.name.endswith(".tmp"):
                yield path, path.relative_to(root).as_posix()


def _add(tar: tarfile.TarFile, path: Path, arcname: str) -> bool:
    """One file into the tar; False when it went away meanwhile (the gateway deleted it)."""
    try:
        with path.open("rb") as handle:
            info = tar.gettarinfo(arcname=arcname, fileobj=handle)
            info.uname = info.gname = ""
            info.uid = info.gid = 0
            tar.addfile(info, handle)
        return True
    except FileNotFoundError:
        return False


def write_backup(out: BinaryIO, chat: dict[str, Any], files: Iterator[tuple[Path, str]], *, payload: str = "chat") -> int:
    """Stream a tar of ``files`` straight through the encryption into ``out``. Returns the file count."""
    header, eph = make_header(chat, payload=payload)
    writer = EncryptingWriter(out, header, eph)
    count = 0
    with tarfile.open(fileobj=writer, mode="w|", format=tarfile.PAX_FORMAT) as tar:  # type: ignore[call-overload]
        for path, arcname in files:
            count += _add(tar, path, arcname)
    writer.finish()
    return count


def survey(root: Path) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """The chats with a backup key, the tokens of those without one, and paths that belong to no chat."""
    chats_dir = root / "chats"
    keyed: list[dict[str, Any]] = []
    missing: list[str] = []
    tokens: set[str] = set()
    orphans: list[str] = []
    if chats_dir.is_dir():
        for path in sorted(chats_dir.iterdir()):
            token = path.name[:-5] if path.name.endswith(".json") else ""
            if not path.is_file() or not _TOKEN.match(token):
                if not path.name.endswith(".tmp"):
                    orphans.append(path.relative_to(root).as_posix())
                continue
            try:
                chat = json.loads(path.read_text())
            except (OSError, ValueError):
                orphans.append(path.relative_to(root).as_posix())
                continue
            if not isinstance(chat, dict) or chat.get("token") != token:
                orphans.append(path.relative_to(root).as_posix())
                continue
            tokens.add(token)
            if isinstance(chat.get("backup_key"), dict):
                keyed.append(chat)
            else:
                missing.append(token)
    for entry in sorted(root.iterdir()) if root.is_dir() else []:
        if entry.name == "chats":
            continue
        if not entry.is_dir():
            orphans.append(entry.name)
            continue
        orphans.extend(child.relative_to(root).as_posix() for child in sorted(entry.iterdir()) if child.name not in tokens)
    return keyed, missing, orphans


def encrypt_all(root: Path, out_dir: Path) -> int:
    """One encrypted file per chat with a backup key. Exit 3 when any chat has none (nothing is written in clear)."""
    status = 0
    if not root.is_dir():
        print(f"no private chat folder at {root}; nothing to back up")
        return 0
    out_dir.mkdir(parents=True, exist_ok=True)
    keyed, missing, orphans = survey(root)
    for chat in keyed:
        token = chat["token"]
        target = out_dir / f"{token}{SUFFIX}"
        partial = target.with_suffix(".partial")
        try:
            with partial.open("wb") as handle:
                count = write_backup(handle, chat, _chat_files(root, token))
            os.replace(partial, target)
            print(f"private chat {token}: encrypted {count} file(s) to its backup key {chat['backup_key'].get('key_id', '')}")
        except (BackupError, OSError, ValueError) as exc:
            partial.unlink(missing_ok=True)
            print(f"private chat {token}: NOT backed up, could not encrypt it ({exc})", file=sys.stderr)
            status = 3
    for token in missing:
        print(f"private chat {token} has no backup key yet: open the chat on a phone once "
              "(Family, Private chat, then unlock it; the thread, Beloved or the gallery all count). "
              "It was NOT backed up.", file=sys.stderr)
        status = 3
    for path in orphans:
        print(f"private chat folder: {path} belongs to no chat; it was not backed up", file=sys.stderr)
    return status


# --- Reading (structure; decryption needs the private key) --------------------


def read_header(handle: BinaryIO) -> tuple[dict[str, Any], bytes]:
    """The header and the digest every chunk is bound to. Raises BackupError for anything malformed."""
    magic = handle.read(len(MAGIC))
    if magic != MAGIC:
        raise BackupError("not a private chat backup (wrong magic)")
    raw_len = handle.read(4)
    if len(raw_len) != 4:
        raise BackupError("the backup is truncated (no header)")
    (length,) = struct.unpack(">I", raw_len)
    if not 0 < length <= MAX_HEADER_BYTES:
        raise BackupError("the backup's header has an impossible size")
    head = handle.read(length)
    if len(head) != length:
        raise BackupError("the backup is truncated (header)")
    try:
        header = json.loads(head)
    except ValueError as exc:
        raise BackupError("the backup's header is not JSON") from exc
    if not isinstance(header, dict) or header.get("format") != FORMAT or header.get("version") != VERSION:
        raise BackupError("not a version 1 private chat backup")
    if header.get("alg") not in ALGS or header.get("cipher") != "AES-256-GCM" or header.get("kdf") != "HKDF-SHA256":
        raise BackupError("the backup names an algorithm this tool does not know")
    if header.get("chunk_bytes") != CHUNK_BYTES or not _TOKEN.match(str(header.get("token") or "")):
        raise BackupError("the backup's header is malformed")
    return header, hashlib.sha256(magic + raw_len + head).digest()


def chunks(handle: BinaryIO) -> Iterator[tuple[int, bytes, bool]]:
    """Each ciphertext chunk with its index and whether it is the final one, by length (the AAD then proves it)."""
    full = CHUNK_BYTES + TAG_BYTES
    index = 0
    while True:
        block = handle.read(full)
        if len(block) == full:
            yield index, block, False
            index += 1
            continue
        if len(block) < TAG_BYTES:
            raise BackupError("the backup is truncated (it has no final chunk)")
        yield index, block, True
        if handle.read(1):
            raise BackupError("the backup has bytes after its final chunk")
        return


def check_structure(path: Path) -> dict[str, Any]:
    """The header, after checking the whole file is well formed (without the private key)."""
    with path.open("rb") as handle:
        header, _digest = read_header(handle)
        load_public_key(header["alg"], _b64d(header["recipient_public_key"], "recipient_public_key"))
        load_public_key(header["alg"], _b64d(header["ephemeral_public_key"], "ephemeral_public_key"))
        read_sealed_pair(header.get("sealed_private_key"))
        if not header.get("key_records"):
            raise BackupError("the backup carries no key record to open it with")
        for _ in chunks(handle):
            pass
    return header


def read_sealed_pair(value: Any) -> tuple[bytes, bytes]:
    if not isinstance(value, dict):
        raise BackupError("the backup has no sealed private key")
    iv, ct = _b64d(value.get("iv"), "sealed_private_key.iv"), _b64d(value.get("ct"), "sealed_private_key.ct")
    if len(iv) != 12 or len(ct) <= TAG_BYTES:
        raise BackupError("the sealed private key has the wrong size")
    return iv, ct


# --- Sealing an existing clear file -------------------------------------------


def shred(path: Path) -> None:
    """Overwrite a file with random bytes, flush it to disk, then delete it.

    On a copy-on-write or flash filesystem an overwrite may land elsewhere, so
    this is the best a script can do, not a guarantee; see docs/PRIVATE_CHAT_BACKUP.md.
    """
    size = path.stat().st_size
    with path.open("r+b") as handle:
        left = size
        while left > 0:
            step = min(left, CHUNK_BYTES)
            handle.write(secrets.token_bytes(step))
            left -= step
        handle.flush()
        os.fsync(handle.fileno())
    path.unlink()


def seal_file(root: Path, token: str, clear: Path, out: Path | None = None, *, keep: bool = False) -> Path:
    """Encrypt a clear snapshot to the chat's backup key, check the result, and destroy the clear file."""
    if not _TOKEN.match(token):
        raise BackupError("that is not a chat token")
    record = root / "chats" / f"{token}.json"
    try:
        chat = json.loads(record.read_text())
    except (OSError, ValueError) as exc:
        raise BackupError(f"cannot read the chat's registry record {record}") from exc
    if not isinstance(chat.get("backup_key"), dict):
        raise BackupError(f"private chat {token} has no backup key yet: open the chat on a phone once")
    if not clear.is_file():
        raise BackupError(f"{clear} is not a file")
    target = out or clear.with_name(clear.name + SUFFIX)
    partial = target.with_name(target.name + ".partial")
    try:
        with partial.open("wb") as handle:
            write_backup(handle, chat, iter([(clear, f"snapshots/{clear.name}")]), payload="snapshot")
        header = check_structure(partial)
        if header["token"] != token:
            raise BackupError("the sealed file does not name this chat's key")
        os.replace(partial, target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    if not keep:
        shred(clear)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    every = sub.add_parser("encrypt-all", help="one encrypted backup per chat")
    every.add_argument("--root", type=Path, required=True)
    every.add_argument("--out", type=Path, required=True)
    seal = sub.add_parser("seal-file", help="encrypt an existing clear snapshot and delete it")
    seal.add_argument("--root", type=Path, required=True)
    seal.add_argument("--token", required=True)
    seal.add_argument("--out", type=Path)
    seal.add_argument("--keep-clear", action="store_true", help="do not delete the clear file afterwards")
    seal.add_argument("file", type=Path)
    look = sub.add_parser("inspect", help="check a backup's structure and print its header")
    look.add_argument("file", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.cmd == "encrypt-all":
            return encrypt_all(args.root, args.out)
        if args.cmd == "seal-file":
            target = seal_file(args.root, args.token, args.file, args.out, keep=args.keep_clear)
            print(f"sealed {args.file.name} to {target} ({'kept' if args.keep_clear else 'overwrote and deleted'} the clear file)")
            return 0
        header = check_structure(args.file)
        shown = {k: v for k, v in header.items() if k not in ("sealed_private_key", "key_records")}
        shown["key_records"] = len(header.get("key_records") or [])
        print(json.dumps(shown, indent=2, sort_keys=True))
        return 0
    except BackupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

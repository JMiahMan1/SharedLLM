#!/usr/bin/env python3
"""Open an encrypted private chat backup with the chat password.

    open_private_chat_backup.py BACKUP OUTDIR [--registry chats/<token>.json]

The password is asked for on the terminal (never on the command line). It
opens the chat's data key DK exactly as the app does
(services/ui/src/lib/privateChatCrypto.ts):

* v1 record: DK = PBKDF2-SHA256(password, salt, iterations, 32 bytes).
* v2 record: PBKDF2-SHA256(password, kdf.salt, kdf.iterations, 64 bytes); the
  first 32 bytes are the KEK and DK = AES-GCM-decrypt(KEK, wrapped_key).

Either way DK must open the verifier to ``jarvis-private-chat-verifier-v1``.
DK then opens the backup's sealed private key (sealed on a phone with the
chat's key), which with the header's ephemeral public key gives the file key
(scripts/private_chat_backup.py describes the format). Every chunk is checked
before anything is written out: the payload is decrypted to a private temp
file in OUTDIR first and only extracted once the whole file proved intact.

The backup carries every key record the chat had when it was made, so any
password the chat had by then works. A password set *after* the backup is
not in it: pass the live registry record (``--registry``, from
``data/private_chat/chats/<token>.json``) and its current and earlier records
are tried too.

Set PRIVATE_CHAT_PASSWORD to pass the password without a prompt (tests; do
not put it in a shell history).
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import private_chat_backup as fmt

VERIFIER_PLAINTEXT = b"jarvis-private-chat-verifier-v1"
MIN_ITERATIONS = 600_000
MAX_ITERATIONS = 10_000_000


class WrongPassword(fmt.BackupError):
    pass


def _b(value: Any, field: str) -> bytes:
    return fmt._b64d(value, field)


def data_key(password: str, record: dict[str, Any]) -> bytes | None:
    """DK for a password and one key record, or None when the password does not open it."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    kdf = record.get("kdf") or {}
    iterations = kdf.get("iterations")
    if not isinstance(iterations, int) or not MIN_ITERATIONS <= iterations <= MAX_ITERATIONS:
        raise fmt.BackupError("a key record asks for an impossible key stretch")
    salt = _b(kdf.get("salt"), "kdf.salt")
    verifier = record.get("verifier") or {}
    if record.get("version") == 2:
        bits = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations, 64)
        wrapped = record.get("wrapped_key") or {}
        try:
            dk = AESGCM(bits[:32]).decrypt(_b(wrapped.get("iv"), "wrapped_key.iv"), _b(wrapped.get("ct"), "wrapped_key.ct"), None)
        except InvalidTag:
            return None
    else:
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations, 32)
    try:
        plain = AESGCM(dk).decrypt(_b(verifier.get("iv"), "verifier.iv"), _b(verifier.get("ct"), "verifier.ct"), None)
    except InvalidTag:
        return None
    return dk if plain == VERIFIER_PLAINTEXT else None


def private_key(header: dict[str, Any], dk: bytes):
    """The backup private key, unsealed with DK, after checking it matches the header's public key."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    iv, ct = fmt.read_sealed_pair(header.get("sealed_private_key"))
    try:
        record = json.loads(AESGCM(dk).decrypt(iv, ct, None))
    except (InvalidTag, ValueError) as exc:
        raise fmt.BackupError("the chat's key does not open this backup's private key") from exc
    if not isinstance(record, dict) or record.get("t") != fmt.RECORD_KIND or record.get("alg") != header["alg"]:
        raise fmt.BackupError("the sealed record is not this backup's key")
    if record.get("public_key") != header["recipient_public_key"]:
        raise fmt.BackupError("the sealed private key belongs to another backup key")
    key = serialization.load_der_private_key(_b(record.get("private_key"), "private_key"), password=None)
    if fmt._public_raw(key.public_key()) != _b(header["recipient_public_key"], "recipient_public_key"):
        raise fmt.BackupError("the sealed private key does not match its public key")
    return key


def candidate_records(header: dict[str, Any], registry: Path | None) -> list[dict[str, Any]]:
    records = list(header.get("key_records") or [])
    if registry is not None:
        chat = json.loads(registry.read_text())
        if chat.get("token") != header["token"]:
            raise fmt.BackupError("that registry record is for another chat")
        records = [*fmt.key_records(chat), *(r for r in records if r not in fmt.key_records(chat))]
    return records


def unlock(header: dict[str, Any], password: str, registry: Path | None = None) -> bytes:
    if not password:
        raise WrongPassword("no password given")
    for record in candidate_records(header, registry):
        dk = data_key(password, record)
        if dk is not None:
            return dk
    raise WrongPassword("that password does not open this chat (none of its key records accept it)")


def decrypt_to(backup: Path, password: str, sink, registry: Path | None = None) -> dict[str, Any]:
    """Decrypt the payload (a tar stream) into ``sink``. Raises on any tampering or truncation."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    with backup.open("rb") as handle:
        header, digest = fmt.read_header(handle)
        dk = unlock(header, password, registry)
        key = private_key(header, dk)
        eph = fmt.load_public_key(header["alg"], _b(header["ephemeral_public_key"], "ephemeral_public_key"))
        file_key, prefix = fmt.derive(
            fmt.exchange(key, eph), _b(header["hkdf_salt"], "hkdf_salt"), header["token"],
            _b(header["ephemeral_public_key"], "ephemeral_public_key"),
            _b(header["recipient_public_key"], "recipient_public_key"),
        )
        aes = AESGCM(file_key)
        for index, block, final in fmt.chunks(handle):
            try:
                sink.write(aes.decrypt(fmt._nonce(prefix, index), block, fmt._aad(digest, index, final)))
            except InvalidTag as exc:
                raise fmt.BackupError(f"chunk {index} was altered, reordered or cut short; nothing was restored") from exc
    return header


def _safe_members(tar: tarfile.TarFile, dest: Path):
    root = dest.resolve()
    for member in tar.getmembers():
        target = (dest / member.name).resolve()
        if not (member.isfile() or member.isdir()) or (target != root and root not in target.parents):
            raise fmt.BackupError(f"the backup holds an unsafe path: {member.name}")
        yield member


def restore(backup: Path, out_dir: Path, password: str, registry: Path | None = None) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(out_dir, 0o700)
    fd, temp_name = tempfile.mkstemp(dir=out_dir, prefix=".restore-", suffix=".tar")
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as sink:
            header = decrypt_to(backup, password, sink, registry)
        with tarfile.open(temp, "r:") as tar:
            tar.extractall(out_dir, members=list(_safe_members(tar, out_dir)))  # members checked above
    finally:
        temp.unlink(missing_ok=True)
    return header


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Open an encrypted private chat backup with the chat password.")
    parser.add_argument("backup", type=Path, help="a <token>.jarvis-backup file")
    parser.add_argument("out", type=Path, help="where to put the chat's folder (created, mode 700)")
    parser.add_argument("--registry", type=Path, help="the live chats/<token>.json, for a password set after the backup")
    args = parser.parse_args(argv)
    password = os.environ.get("PRIVATE_CHAT_PASSWORD") or getpass.getpass("Private chat password: ")
    try:
        header = restore(args.backup, args.out, password, args.registry)
    except fmt.BackupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"restored private chat {header['token']} (backup of {header['created_at']}) into {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

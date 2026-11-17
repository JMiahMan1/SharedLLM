"""A fake private chat sealed the way the phones seal one, for the backup tests on both sides.

The chats are the fake ones in fixtures/private_chat_backup_vectors.json (made
by the app's own crypto). This module fills one with real sealed content
(messages, a picture, a video clip in pieces, an edit, a reaction, a deleted
message, a gallery picture, Beloved records) under the chat's data key, so:

* the gateway tests (services/gateway/tests/test_private_chat_export.py) can
  export it and restore it, and
* the app's decryptor (services/ui/src/lib/privateChatBackupCore.test.ts) can
  open a backup the Python encrypter made, checked against what is written here.

Regenerate the app's fixture (fixtures/private_chat_backup_files.json) with::

    python tests/unit/private_chat_backup_fixture.py

Every person, password and word in it is made up.
"""
from __future__ import annotations

import base64
import io
import json
import os
import struct
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import open_private_chat_backup as opener  # noqa: E402
import private_chat_backup as fmt  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
VECTORS = json.loads((FIXTURES / "private_chat_backup_vectors.json").read_text())
FILES = FIXTURES / "private_chat_backup_files.json"
LINE = "\U0001f512 Encrypted message"
FENCE = "```jarvis-envelope"
#: A 1x1 PNG, the smallest real picture.
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")
VIDEO_LABEL = b"jarvis-private-video-v1"
T0 = 1_700_000_000


def data_key(chat: dict[str, Any], password: str) -> bytes:
    """The chat's data key for a password (as the app derives it)."""
    for record in fmt.key_records(chat):
        dk = opener.data_key(password, record)
        if dk is not None:
            return dk
    raise ValueError("that password does not open this fake chat")


def seal(dk: bytes, obj: Any) -> str:
    """A sealed envelope, byte for byte the app's sealMessage/sealRecord."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    iv = os.urandom(12)
    ct = AESGCM(dk).encrypt(iv, json.dumps(obj, separators=(",", ":")).encode(), None)
    envelope = {"kind": "sealed", "title": "Encrypted message", "v": 1,
                "iv": base64.b64encode(iv).decode(), "ct": base64.b64encode(ct).decode()}
    return f"{LINE}\n\n{FENCE}\n{json.dumps(envelope, separators=(',', ':'))}\n{FENCE}"


def encrypt_bytes(dk: bytes, data: bytes) -> bytes:
    """A picture as the app uploads it: IV, then the ciphertext."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    iv = os.urandom(12)
    return iv + AESGCM(dk).encrypt(iv, data, None)


def encrypt_video_chunk(dk: bytes, clip: bytes, index: int, count: int, size: int, plain: bytes) -> bytes:
    """One piece of a clip, with its place bound in (privateChatCrypto.ts videoChunkAad)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    aad = VIDEO_LABEL + clip + struct.pack(">IIQ", index, count, size)
    iv = os.urandom(12)
    return iv + AESGCM(dk).encrypt(iv, plain, aad)


def _write(path: Path, obj: Any, *, sort_keys: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, sort_keys=sort_keys))


def _blob_id(n: int) -> str:
    return f"{n:032x}"


def build_chat(root: Path, chat: dict[str, Any], password: str) -> dict[str, Any]:
    """A whole chat folder under ``root`` (as the gateway keeps it), and what a reader should find in it."""
    token = chat["token"]
    a, b = chat["members"]
    dk = data_key(chat, password)
    _write(root / "chats" / f"{token}.json", chat)
    blobs = root / "blobs" / token
    blobs.mkdir(parents=True, exist_ok=True)
    photo, poster, added = _blob_id(1), _blob_id(2), _blob_id(3)
    for blob_id in (photo, poster, added):
        (blobs / blob_id).write_bytes(encrypt_bytes(dk, PNG))
    clip = os.urandom(16)
    video = b"not really a video, just its bytes"
    piece = 16
    count = -(-len(video) // piece)
    pieces = [_blob_id(10 + i) for i in range(count)]
    for i, blob_id in enumerate(pieces):
        (blobs / blob_id).write_bytes(encrypt_video_chunk(dk, clip, i, count, len(video), video[i * piece:(i + 1) * piece]))

    msgs = root / "messages" / token
    records = [
        {"id": 1, "by": a, "at": T0, "sealed": seal(dk, {"t": "text", "text": "Good morning, love"}), "reply_to": None, "deleted": False},
        {"id": 2, "by": b, "at": T0 + 60, "sealed": seal(dk, {"t": "image", "blob": photo, "mime": "image/png", "name": "lake.png",
                                                              "caption": "Us at the lake"}),
         "reply_to": None, "deleted": False, "blobs": [photo]},
        {"id": 3, "by": a, "at": T0 + 120, "sealed": seal(dk, {"t": "text", "text": "Dinner at seven?"}), "reply_to": 1, "deleted": False},
        {"id": 4, "by": b, "at": T0 + 180, "sealed": None, "reply_to": None, "deleted": True, "deleted_at": T0 + 200},
        {"id": 5, "by": b, "at": T0 + 86_400, "sealed": seal(dk, {
            "t": "video", "mime": "video/mp4", "size": len(video), "duration": 3, "poster": poster, "chunks": pieces,
            "clip": base64.b64encode(clip).decode(), "chunk_bytes": piece, "name": "waves.mp4"}),
         "reply_to": None, "deleted": False, "blobs": [*pieces, poster]},
        {"id": 6, "by": a, "at": T0 + 86_460, "sealed": seal(dk, {"t": "comment", "blob": photo, "text": "Best day"}),
         "reply_to": None, "deleted": False},
    ]
    for record in records:
        _write(msgs / "m" / f"{record['id']}.json", record)
    _write(msgs / "read.json", {a: 6, b: 5})
    _write(msgs / "meta.json", {"floor": 0})

    _write(root / "marks" / token / "3.json", {
        "message_id": 3,
        "edit": {"by": a, "at": T0 + 130, "sealed": seal(dk, {"t": "edit", "m": 3, "text": "Dinner at seven, just us?"})},
        "reactions": [{"id": "c" * 32, "by": b, "at": T0 + 140, "sealed": seal(dk, {"t": "reaction", "m": 3, "e": "❤️"}),
                       "seen_by": [b, a]}],
    })
    _write(root / "gallery" / token / f"{'d' * 32}.json", {
        "id": "d" * 32, "by": a, "at": T0 + 300,
        "sealed": seal(dk, {"t": "added", "blob": added, "mime": "image/png", "name": "beach.png", "taken_at": T0 - 86_400}),
    }, sort_keys=False)  # as the gateway writes a gallery entry
    beloved = [
        ("profile", "1" * 32, a, {"b": "profile", "wedding": "2015-06-20", "title": "Alex and Sam"}),
        ("note", "2" * 32, b, {"b": "note", "text": "You make every day better"}),
        ("prayer", "3" * 32, a, {"b": "prayer", "text": "For a restful week"}),
        ("day", "4" * 32, b, {"b": "day", "name": "Our first date", "date": "2014-02-14", "yearly": True}),
    ]
    for i, (kind, rid, by, record) in enumerate(beloved):
        _write(root / "beloved" / token / kind / f"{rid}.json", {
            "id": rid, "kind": kind, "by": by, "at": T0 + 400 + i, "updated_at": T0 + 400 + i, "updated_by": by,
            "sealed": seal(dk, record)})
    return {
        "token": token,
        "members": [a, b],
        "messages": 6,
        "texts": ["Good morning, love", "Dinner at seven, just us?", "Best day"],
        "edited": {"id": 3, "original": "Dinner at seven?", "text": "Dinner at seven, just us?"},
        "reaction": {"id": 3, "by": b, "emoji": "❤️"},
        "deleted": [4],
        "picture": {"id": 2, "blob": photo, "caption": "Us at the lake"},
        "video": {"id": 5, "bytes": base64.b64encode(video).decode(), "pieces": count},
        "gallery": {"blob": added, "name": "beach.png"},
        "beloved": {"profile": "Alex and Sam", "note": "You make every day better", "prayer": "For a restful week",
                    "day": "Our first date"},
        "blobs": sorted([photo, poster, added, *pieces]),
        "files": sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()),
    }


def backup_bytes(chat: dict[str, Any], password: str, root: Path) -> tuple[bytes, dict[str, Any]]:
    """A whole backup of a fake chat, made by the nightly encrypter's own code, and what is in it."""
    expect = build_chat(root, chat, password)
    out = io.BytesIO()
    fmt.write_backup(out, chat, fmt._chat_files(root, chat["token"]))
    return out.getvalue(), expect


def write_fixture(path: Path = FILES) -> None:
    import tempfile

    pw = VECTORS["passwords"]
    doc: dict[str, Any] = {"note": "Fake chats sealed by tests/unit/private_chat_backup_fixture.py; fake people and passwords."}
    with tempfile.TemporaryDirectory() as tmp:
        for name, chat, password, others in (
            ("v2", VECTORS["v2_chat"], pw["second"], [pw["first"]]),
            ("v1", VECTORS["v1_chat"], pw["v1"], []),
        ):
            data, expect = backup_bytes(chat, password, Path(tmp) / name)
            doc[name] = {"password": password, "other_passwords": others, "alg": chat["backup_key"]["alg"],
                         "file": base64.b64encode(data).decode(), "expect": expect}
    path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    write_fixture()
    print(f"wrote {FILES}")

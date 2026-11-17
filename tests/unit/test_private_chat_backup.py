"""Encrypted private chat backups: only the chat password opens one, and nothing is ever archived in clear.

The chats here are fake ones made by the app's own crypto
(services/ui/src/lib/privateChatBackupKey.test.ts writes
fixtures/private_chat_backup_vectors.json), so the Python encrypter and
restorer are checked against the TypeScript that runs on the phones.
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import sqlite3
import struct
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import open_private_chat_backup as opener  # noqa: E402
import private_chat_backup as fmt  # noqa: E402

VECTORS = json.loads((Path(__file__).parent / "fixtures" / "private_chat_backup_vectors.json").read_text())
PW = VECTORS["passwords"]
SCRIPT = ROOT / "scripts" / "backup_databases.sh"
MEMBERS = [b"alexfake", b"samfake"]
SEALED = '\U0001f512 Encrypted message\n\n```jarvis-envelope\n{"kind":"sealed","title":"Encrypted message","v":1,"iv":"AAAAAAAAAAAAAAAA","ct":"BBBB"}\n```'


def _chat_dir(root: Path, chat: dict, *, big: bool = False) -> Path:
    """A private chat folder as the gateway keeps it, for one fake chat."""
    token = chat["token"]
    (root / "chats").mkdir(parents=True, exist_ok=True)
    (root / "chats" / f"{token}.json").write_text(json.dumps(chat))
    msgs = root / "messages" / token
    msgs.mkdir(parents=True)
    for i in range(3):
        (msgs / f"{i + 1}.json").write_text(json.dumps({"id": i + 1, "actor": "alexfake", "at": 1_700_000_000 + i, "message": SEALED}))
    (root / "blobs" / token).mkdir(parents=True)
    (root / "blobs" / token / ("ab" * 16)).write_bytes(os.urandom(int(2.5 * fmt.CHUNK_BYTES) if big else 3000))
    (root / "marks" / token).mkdir(parents=True)
    (root / "marks" / token / "1.json").write_text(json.dumps({"seen_by": ["samfake"], "reactions": {"samfake": SEALED}}))
    (root / "beloved" / token / "notes").mkdir(parents=True)
    (root / "beloved" / token / "notes" / "n1.json").write_text(json.dumps({"by": "samfake", "sealed": SEALED}))
    (root / "migration" / token).mkdir(parents=True)
    (root / "migration" / token / "talk-export-1.json").write_text(json.dumps([{"actorId": "alexfake", "message": SEALED}]))
    return root


def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def v2(tmp_path):
    src = _chat_dir(tmp_path / "private_chat", VECTORS["v2_chat"], big=True)
    out = tmp_path / "out"
    assert fmt.encrypt_all(src, out) == 0
    return src, out / "vectorchat2.jarvis-backup"


def _restore(backup: Path, dest: Path, password: str, registry: Path | None = None) -> dict[str, bytes]:
    opener.restore(backup, dest, password, registry)
    return _tree(dest)


def test_round_trip_with_the_current_password(v2, tmp_path):
    src, backup = v2
    restored = _restore(backup, tmp_path / "back", PW["second"])
    assert restored == _tree(src)
    assert "migration/vectorchat2/talk-export-1.json" in restored


def test_an_earlier_password_of_the_chat_also_opens_it(v2, tmp_path):
    _src, backup = v2
    assert _restore(backup, tmp_path / "back", PW["first"])


def test_a_v1_chat_with_a_p256_key_round_trips(tmp_path):
    src = _chat_dir(tmp_path / "pc", VECTORS["v1_chat"])
    assert fmt.encrypt_all(src, tmp_path / "out") == 0
    backup = tmp_path / "out" / "vectorchat1.jarvis-backup"
    assert fmt.check_structure(backup)["alg"] == "P-256"
    assert _restore(backup, tmp_path / "back", PW["v1"]) == _tree(src)


def test_the_wrong_password_is_refused_and_nothing_is_written(v2, tmp_path):
    _src, backup = v2
    with pytest.raises(opener.WrongPassword):
        opener.restore(backup, tmp_path / "back", "not the password")
    assert list((tmp_path / "back").iterdir()) == []


def test_a_password_set_after_the_backup_opens_it_with_the_live_registry(tmp_path):
    # The backup was made before the password change: it only knows the first record.
    chat = dict(VECTORS["v2_chat"])
    first = chat["history"][0]
    chat.update({"kdf": first["kdf"], "wrapped_key": first["wrapped_key"], "verifier": first["verifier"], "history": []})
    src = _chat_dir(tmp_path / "pc", chat)
    assert fmt.encrypt_all(src, tmp_path / "out") == 0
    backup = tmp_path / "out" / "vectorchat2.jarvis-backup"
    with pytest.raises(opener.WrongPassword):
        opener.restore(backup, tmp_path / "back", PW["second"])
    live = tmp_path / "live.json"
    live.write_text(json.dumps(VECTORS["v2_chat"]))
    assert _restore(backup, tmp_path / "back2", PW["second"], live)
    other = tmp_path / "other.json"
    other.write_text(json.dumps(VECTORS["v1_chat"]))
    with pytest.raises(fmt.BackupError, match="another chat"):
        opener.restore(backup, tmp_path / "back3", PW["v1"], other)


def _layout(backup: Path) -> tuple[bytes, int]:
    data = backup.read_bytes()
    (length,) = struct.unpack(">I", data[len(fmt.MAGIC):len(fmt.MAGIC) + 4])
    return data, len(fmt.MAGIC) + 4 + length


FULL = fmt.CHUNK_BYTES + fmt.TAG_BYTES


def _mangle(backup: Path, how: str) -> None:
    data, start = _layout(backup)
    body = data[start:]
    assert len(body) > 2 * FULL  # three chunks: two full, one final
    if how == "flip":
        body = body[:10] + bytes([body[10] ^ 1]) + body[11:]
    elif how == "header":
        data = data.replace(b'"payload":"chat"', b'"payload":"chaT"')
    elif how == "reorder":
        body = body[FULL:2 * FULL] + body[:FULL] + body[2 * FULL:]
    elif how == "truncate-boundary":
        body = body[:2 * FULL]
    elif how == "truncate-middle":
        body = body[:FULL + 1000]
    elif how == "drop-middle":
        body = body[:FULL] + body[2 * FULL:]
    elif how == "trailing":
        body = body + b"x"
    backup.write_bytes(data[:start] + body)


@pytest.mark.parametrize("how", ["flip", "header", "reorder", "truncate-boundary", "truncate-middle", "drop-middle", "trailing"])
def test_tampering_truncation_and_reordering_are_refused(v2, tmp_path, how):
    _src, backup = v2
    _mangle(backup, how)
    with pytest.raises(fmt.BackupError):
        opener.restore(backup, tmp_path / "back", PW["second"])
    assert list((tmp_path / "back").iterdir()) == []


def test_a_chat_without_a_key_is_never_written_and_fails_the_run(tmp_path, capsys):
    chat = {k: v for k, v in VECTORS["v2_chat"].items() if k != "backup_key"}
    chat["token"] = "nokeychat"
    src = _chat_dir(tmp_path / "pc", chat)
    _chat_dir(src, VECTORS["v1_chat"])
    (src / "messages" / "deadbeef00").mkdir()
    (src / "stray.txt").write_text("alexfake")
    assert fmt.encrypt_all(src, tmp_path / "out") == 3
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["vectorchat1.jarvis-backup"]
    err = capsys.readouterr().err
    assert "private chat nokeychat has no backup key yet: open the chat on a phone once" in err
    assert "messages/deadbeef00 belongs to no chat" in err
    assert "stray.txt belongs to no chat" in err


def test_the_backup_file_has_nothing_readable_in_it(v2):
    _src, backup = v2
    data = backup.read_bytes()
    for needle in [*MEMBERS, b"jarvis-envelope", b'"kind"', b"seen_by", b"talk-export", b"messages/", b"replaced_by", b"auth_hash"]:
        assert needle not in data, needle


def test_seal_file_encrypts_a_clear_snapshot_and_destroys_it(tmp_path):
    src = _chat_dir(tmp_path / "pc", VECTORS["v2_chat"])
    snapshot = tmp_path / "private_chat-clear.tar.gz"
    with tarfile.open(snapshot, "w:gz") as tar:
        tar.add(src, arcname="private_chat")
    original = snapshot.read_bytes()
    target = fmt.seal_file(src, "vectorchat2", snapshot)
    assert not snapshot.exists()
    assert target.name == "private_chat-clear.tar.gz.jarvis-backup"
    assert fmt.check_structure(target)["payload"] == "snapshot"
    restored = _restore(target, tmp_path / "back", PW["second"])
    assert restored == {"snapshots/private_chat-clear.tar.gz": original}


def test_seal_file_refuses_a_chat_without_a_key_and_keeps_the_file(tmp_path):
    chat = {k: v for k, v in VECTORS["v2_chat"].items() if k != "backup_key"}
    src = _chat_dir(tmp_path / "pc", chat)
    clear = tmp_path / "talk-export.json"
    clear.write_text("{}")
    with pytest.raises(fmt.BackupError, match="no backup key yet"):
        fmt.seal_file(src, "vectorchat2", clear)
    assert clear.exists()
    assert not list(tmp_path.glob("*.jarvis-backup*"))


def test_the_cli_reads_the_password_from_the_environment_never_an_argument(v2, tmp_path):
    _src, backup = v2
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "open_private_chat_backup.py"), str(backup), str(tmp_path / "back")],
        capture_output=True, text=True, env={**os.environ, "PRIVATE_CHAT_PASSWORD": PW["second"]}, timeout=120,
    )
    assert out.returncode == 0, out.stderr
    assert PW["second"] not in out.stdout + out.stderr
    assert (tmp_path / "back" / "chats" / "vectorchat2.json").exists()


# --- The nightly script ---------------------------------------------------------


def _install(tmp_path: Path, *, keyless: bool = False) -> Path:
    root = tmp_path / "root"
    (root / "data" / "identity").mkdir(parents=True)
    with sqlite3.connect(root / "data" / "identity" / "identity.db") as db:
        db.execute("create table t (x)")
    _chat_dir(root / "data" / "private_chat", VECTORS["v2_chat"])
    if keyless:
        chat = {k: v for k, v in VECTORS["v1_chat"].items() if k != "backup_key"}
        _chat_dir(root / "data" / "private_chat", chat)
    return root


def _run(root: Path, dest: Path, **env) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT)], capture_output=True, text=True, timeout=180,
        env={**os.environ, "BACKUP_ROOT": str(root), "BACKUP_DIR": str(dest), "BACKUP_PYTHON": sys.executable,
             "BACKUP_CRYPTO_IMAGE": "", **env},
    )


def _archive_members(dest: Path) -> dict[str, bytes]:
    (archive,) = dest.glob("sharedllm-*.tar.gz")
    out = {}
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            if member.isfile():
                out[member.name] = tar.extractfile(member).read()  # type: ignore[union-attr]
    return out


needs_sqlite = pytest.mark.skipif(shutil.which("sqlite3") is None, reason="sqlite3 not installed")


@needs_sqlite
def test_the_archive_holds_only_encrypted_chats(tmp_path):
    root = _install(tmp_path)
    out = _run(root, tmp_path / "out")
    assert out.returncode == 0, out.stderr
    members = _archive_members(tmp_path / "out")
    chat_files = sorted(n for n in members if "private_chat" in n)
    assert chat_files == ["./folders/private_chat/vectorchat2.jarvis-backup"]
    everything = b"".join(members.values())
    # The header names its "sealed_private_key"; no sealed envelope or record of the chat is readable.
    for needle in [*MEMBERS, b"jarvis-envelope", b'"kind":"sealed"', b'"sealed":', b"seen_by", b"talk-export"]:
        assert needle not in everything, needle
    # And it opens with the password.
    backup = tmp_path / "b.jarvis-backup"
    backup.write_bytes(members["./folders/private_chat/vectorchat2.jarvis-backup"])
    assert _restore(backup, tmp_path / "back", PW["second"]) == _tree(root / "data" / "private_chat")


@needs_sqlite
def test_a_chat_without_a_key_fails_the_run_but_the_rest_is_archived(tmp_path):
    root = _install(tmp_path, keyless=True)
    out = _run(root, tmp_path / "out")
    assert out.returncode == 3
    assert "private chat vectorchat1 has no backup key yet" in out.stderr
    members = _archive_members(tmp_path / "out")
    assert "./identity-identity.db" in members
    assert sorted(n for n in members if "private_chat" in n) == ["./folders/private_chat/vectorchat2.jarvis-backup"]
    assert not any(b"alexfake" in data for data in members.values())


@needs_sqlite
def test_with_no_way_to_encrypt_the_chat_is_left_out_not_copied(tmp_path):
    root = _install(tmp_path)
    stub = tmp_path / "nopython"
    stub.write_text("#!/bin/sh\nexit 1\n")
    stub.chmod(0o755)
    out = _run(root, tmp_path / "out", BACKUP_PYTHON=str(stub))
    assert out.returncode == 3
    assert "it was NOT backed up" in out.stderr
    members = _archive_members(tmp_path / "out")
    assert "./identity-identity.db" in members
    assert not any("private_chat" in n for n in members)
    raw = gzip.decompress(next((tmp_path / "out").glob("sharedllm-*.tar.gz")).read_bytes())
    assert b"alexfake" not in raw

"""The backup files the app's decryptor is tested against are real backups, and still open.

fixtures/private_chat_backup_files.json is written by private_chat_backup_fixture.py
with the nightly encrypter's own code; the app's in-browser decryptor
(services/ui/src/lib/privateChatBackupCore.test.ts) must open the same files.
This keeps the fixture honest: each file opens here with its password, holds
what the fixture says, and is refused with any other password.
"""
from __future__ import annotations

import base64
import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

pytest.importorskip("cryptography")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import private_chat_backup_fixture as fixture

DOC = json.loads(fixture.FILES.read_text())


def _open(tmp_path: Path, name: str, password: str) -> dict[str, bytes]:
    path = tmp_path / f"{name}.jarvis-backup"
    path.write_bytes(base64.b64decode(DOC[name]["file"]))
    sink = io.BytesIO()
    fixture.opener.decrypt_to(path, password, sink)
    sink.seek(0)
    with tarfile.open(fileobj=sink, mode="r:") as tar:
        return {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}


@pytest.mark.parametrize("name", ["v2", "v1"])
def test_each_fixture_opens_with_its_passwords_and_holds_what_it_says(tmp_path, name):
    entry = DOC[name]
    for password in [entry["password"], *entry["other_passwords"]]:
        files = _open(tmp_path, name, password)
        assert sorted(files) == entry["expect"]["files"]
    header = fixture.fmt.check_structure(tmp_path / f"{name}.jarvis-backup")
    assert header["alg"] == entry["alg"] and header["token"] == entry["expect"]["token"]


def test_a_wrong_password_is_refused(tmp_path):
    with pytest.raises(fixture.opener.WrongPassword):
        _open(tmp_path, "v2", "not the password")

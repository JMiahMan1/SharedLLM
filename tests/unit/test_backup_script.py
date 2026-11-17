"""The nightly backup script: executable, and it archives the databases and the private chat folder."""

import os
import shutil
import sqlite3
import subprocess
import tarfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "backup_databases.sh"


def test_the_script_is_executable():
    # cron runs it directly; without the bit every night failed with "Permission denied".
    assert os.access(SCRIPT, os.X_OK)


@pytest.mark.skipif(shutil.which("sqlite3") is None, reason="sqlite3 not installed")
def test_it_archives_databases_and_the_private_chat_folder(tmp_path):
    root = tmp_path / "root"
    (root / "data" / "identity").mkdir(parents=True)
    with sqlite3.connect(root / "data" / "identity" / "identity.db") as db:
        db.execute("create table t (x)")
        db.execute("insert into t values (1)")
    chats = root / "data" / "private_chat" / "chats"
    chats.mkdir(parents=True)
    (chats / "abc123.json").write_text('{"verifier": "sealed"}')
    dest = tmp_path / "out"
    out = subprocess.run(
        ["bash", str(SCRIPT)], capture_output=True, text=True, check=True,
        env={**os.environ, "BACKUP_ROOT": str(root), "BACKUP_DIR": str(dest)},
    )
    assert "1 database(s), 1 folder(s)" in out.stdout
    (archive,) = dest.glob("sharedllm-*.tar.gz")
    with tarfile.open(archive) as tar:
        names = tar.getnames()
    assert "./identity-identity.db" in names
    assert "./folders/private_chat/chats/abc123.json" in names


@pytest.mark.skipif(shutil.which("sqlite3") is None, reason="sqlite3 not installed")
def test_a_missing_folder_is_skipped_not_fatal(tmp_path):
    root = tmp_path / "root"
    (root / "data" / "x").mkdir(parents=True)
    with sqlite3.connect(root / "data" / "x" / "x.db") as db:
        db.execute("create table t (x)")
    out = subprocess.run(
        ["bash", str(SCRIPT)], capture_output=True, text=True, check=True,
        env={**os.environ, "BACKUP_ROOT": str(root), "BACKUP_DIR": str(tmp_path / "out")},
    )
    assert "0 folder(s)" in out.stdout


@pytest.mark.skipif(shutil.which("sqlite3") is None, reason="sqlite3 not installed")
def test_a_locked_database_is_saved_as_files_and_the_rest_still_archived(tmp_path):
    root = tmp_path / "root"
    (root / "data" / "a").mkdir(parents=True)
    (root / "data" / "b").mkdir(parents=True)
    with sqlite3.connect(root / "data" / "a" / "a.db") as db:
        db.execute("create table t (x)")
    locker = sqlite3.connect(root / "data" / "b" / "b.db", isolation_level=None)
    locker.execute("pragma locking_mode=exclusive")
    locker.execute("create table t (x)")
    locker.execute("begin exclusive")
    try:
        out = subprocess.run(
            ["bash", str(SCRIPT)], capture_output=True, text=True,
            env={**os.environ, "BACKUP_ROOT": str(root), "BACKUP_DIR": str(tmp_path / "out"),
                 "BACKUP_FOLDERS": ""},
            timeout=120,
        )
    finally:
        locker.close()
    assert out.returncode == 2
    assert "locked" in out.stderr
    (archive,) = (tmp_path / "out").glob("sharedllm-*.tar.gz")
    with tarfile.open(archive) as tar:
        names = tar.getnames()
    assert "./a-a.db" in names
    assert "./raw/b-b.db" in names

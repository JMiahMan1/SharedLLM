"""The storage index checkpoint is configuration, and it must survive a recreate.

``index_checkpoint.json`` used to be a relative default resolved against the
container's working directory, so every ``up -d --force-recreate`` threw the
progress of a multi-hour crawl away and the next run re-fetched every book. The
checkpoint path now comes from ``STORAGE_CHECKPOINT_PATH`` (compose mounts
``/data`` for storage), an unset path is an honest, warned "not persisted"
state rather than a write somewhere disposable, and a manager with no path
never touches the filesystem at all.
"""

import inspect
import logging
from pathlib import Path

import pytest

from services.storage import main as storage_main
from services.storage.indexer import CheckpointManager

COMPOSE = Path(__file__).resolve().parents[3] / "docker-compose.yml"


@pytest.fixture(autouse=True)
def _reset_warn_once(monkeypatch):
    """Each test starts with the once-per-process warning unspent."""
    monkeypatch.setattr(storage_main, "_CHECKPOINT_WARNED", False)


def test_the_checkpoint_path_comes_from_config(monkeypatch):
    monkeypatch.setenv("STORAGE_CHECKPOINT_PATH", "/data/index_checkpoint.json")
    assert storage_main._checkpoint_path() == "/data/index_checkpoint.json"


def test_an_unset_path_is_none_and_warns_once_by_name(caplog, monkeypatch):
    monkeypatch.delenv("STORAGE_CHECKPOINT_PATH", raising=False)
    with caplog.at_level(logging.WARNING, logger="storage"):
        assert storage_main._checkpoint_path() is None
        assert storage_main._checkpoint_path() is None
    warnings = [r for r in caplog.records if "STORAGE_CHECKPOINT_PATH" in r.getMessage()]
    assert len(warnings) == 1
    assert "not persisted" in warnings[0].getMessage()


def test_whitespace_is_treated_as_unset(monkeypatch):
    monkeypatch.setenv("STORAGE_CHECKPOINT_PATH", "   ")
    assert storage_main._checkpoint_path() is None


def test_a_manager_with_no_path_never_writes_a_file(tmp_path, caplog):
    manager = CheckpointManager("")
    manager.mark_indexed("/Books/Text/Author/Book (1).txt", "123")
    with caplog.at_level(logging.ERROR):
        manager.save()
    assert list(tmp_path.iterdir()) == []
    assert not any("Failed to save checkpoint" in r.getMessage() for r in caplog.records)


def test_a_configured_checkpoint_survives_a_new_manager(tmp_path):
    path = str(tmp_path / "index_checkpoint.json")
    first = CheckpointManager(path)
    first.mark_indexed("/Books/Text/Author/Book (1).txt", "123")
    first.save()
    second = CheckpointManager(path)
    assert second.is_indexed("/Books/Text/Author/Book (1).txt", "123")


def test_the_index_task_reads_the_path_from_config():
    source = inspect.getsource(storage_main._run_full_index_task)
    assert "_checkpoint_path()" in source
    assert "CheckpointManager()" not in source


def test_the_manager_has_no_relative_default_path():
    default = inspect.signature(CheckpointManager.__init__).parameters[
        "checkpoint_file"
    ].default
    assert default == ""


def test_compose_mounts_data_and_names_the_checkpoint_path():
    text = COMPOSE.read_text()
    assert "- ./data/storage:/data" in text
    assert (
        "STORAGE_CHECKPOINT_PATH=${STORAGE_CHECKPOINT_PATH:-/data/index_checkpoint.json}"
        in text
    )

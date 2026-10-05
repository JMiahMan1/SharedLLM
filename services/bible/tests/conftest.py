"""Shared fixtures for the bible service tests.

Two databases on purpose: ``session`` exercises the import/read helpers directly,
while ``client`` runs the real FastAPI app against its own engine so the HTTP
tests cannot pass on a connection the API does not use.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from services.bible import corpus
from services.bible import main as bible_main

SECRET = "test-secret"

GEN_CHAPTERS = 50
GEN_VERSES_PER_CHAPTER = 3


def _mini_source() -> list[dict]:
    """A structurally complete but tiny corpus: every book, real chapter counts.

    Genesis carries three distinguishable verses per chapter so passage and
    search assertions can find something specific; every other book is one
    placeholder verse per chapter, which is all the reader needs from them.
    Chapter counts are exact, because the importer refuses a short book.
    """
    from services.bible.books import BOOKS

    out = []
    for entry in BOOKS:
        if entry["osis"].lower() == "gen":
            rows = [
                [f"Verse {c}:{v} in {entry['osis']}." for v in range(1, GEN_VERSES_PER_CHAPTER + 1)]
                for c in range(1, entry["chapters"] + 1)
            ]
        else:
            rows = [["Placeholder."] for _ in range(entry["chapters"])]
        out.append({"abbrev": entry["osis"], "name": entry["name"], "chapters": rows})
    return out


@pytest.fixture
def corpus_file(tmp_path: Path) -> Path:
    path = tmp_path / "mini.json"
    path.write_text(json.dumps(_mini_source()), encoding="utf-8")
    return path


@pytest.fixture
def engine(tmp_path: Path):
    created = create_engine(
        f"sqlite:///{tmp_path / 'bible.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    SQLModel.metadata.create_all(created)
    return created


@pytest.fixture
def session(engine) -> Session:
    with Session(engine) as s:
        yield s


@pytest.fixture
def reader_db(engine) -> Session:
    with Session(engine) as s:
        yield s


@pytest.fixture
def loaded(session: Session, corpus_file: Path) -> Session:
    corpus.import_corpus(session, code="kjv", name="King James Version", source_path=corpus_file)
    return session


@pytest.fixture
def client(engine, monkeypatch) -> TestClient:
    monkeypatch.setattr(bible_main.app.state, "engine", engine)
    with TestClient(bible_main.app, headers={"X-Internal-Secret": SECRET}) as c:
        yield c


@pytest.fixture
def loaded_client(client: TestClient, reader_db: Session, corpus_file: Path) -> TestClient:
    corpus.import_corpus(reader_db, code="kjv", name="King James Version", source_path=corpus_file)
    return client


def auth() -> dict:
    return {"X-Internal-Secret": SECRET}
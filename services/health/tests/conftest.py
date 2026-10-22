"""Fixtures for the health service tests.

Each test gets its own SQLite file and a TestClient pointed at it. The app's
lifespan also creates the tables, but the fixture does it first so a test can
seed rows before the first request.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine

from services.health import main as health_main


@pytest.fixture
def engine(tmp_path):
    created = create_engine(
        f"sqlite:///{tmp_path / 'health.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    SQLModel.metadata.create_all(created)
    return created


@pytest.fixture
def client(engine):
    health_main.app.state.engine = engine
    with TestClient(health_main.app) as test_client:
        test_client.headers.update(
            {
                "X-Internal-Secret": health_main.INTERNAL_SECRET,
                "X-User-Id": "jeremiah",
            }
        )
        yield test_client
    health_main.app.state.engine = None

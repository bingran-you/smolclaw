"""Test fixtures."""

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from claw_gmail.models import reset_engine, init_db, get_session_factory
from claw_gmail.seed.generator import seed_database


@pytest.fixture
def db_path(tmp_path):
    """Temporary database path."""
    path = str(tmp_path / "test.db")
    yield path
    reset_engine()


@pytest.fixture
def seeded_db(db_path):
    """Seed a temporary database."""
    reset_engine()
    seed_database(scenario="default", seed=42, db_path=db_path)
    return db_path


@pytest.fixture
def client(seeded_db):
    """FastAPI test client with seeded database."""
    reset_engine()
    init_db(seeded_db)
    from claw_gmail.api.app import app
    with TestClient(app) as c:
        yield c
    reset_engine()

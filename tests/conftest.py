"""Shared pytest fixtures.

Tests use a per-test isolated SQLite file under tmp_path so they never
touch the real `upwork_jobs.db`.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Make the project root importable so `import features.n8n` etc. resolve.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Point DB_PATH at a per-test sqlite file. Yields the path."""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("UPWORK_DB_PATH", str(db_path))

    # config caches DB_PATH at import time; re-import to pick up the env var.
    import importlib

    import config as _config
    importlib.reload(_config)
    import db as _db
    importlib.reload(_db)

    return db_path

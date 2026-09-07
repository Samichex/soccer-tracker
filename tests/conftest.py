import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, db  # noqa: E402


@pytest.fixture
def conn(tmp_path, monkeypatch):
    """A fresh, empty DB with the real schema applied, for testing db.py
    functions and schema constraints in isolation from any real data."""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    with db.get_conn() as c:
        yield c


@pytest.fixture(scope="session")
def live_conn():
    """Read-only connection to the real data/soccer.db, for data-quality
    regression checks against whatever has actually been synced. Skips
    (rather than fails) when no DB has been synced yet, e.g. a fresh
    checkout or a CI environment with no data directory."""
    if not config.DB_PATH.exists():
        pytest.skip("data/soccer.db not present -- no live data to check")
    c = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    yield c
    c.close()

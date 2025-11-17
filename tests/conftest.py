from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from eng_dna.db import connect, ensure_schema


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    path = tmp_path / "eng_dna.db"
    conn = connect(path)
    ensure_schema(conn)
    yield conn
    conn.close()


@pytest.fixture()
def sample_file(tmp_path: Path) -> Path:
    path = tmp_path / "sample.txt"
    path.write_text("hello world", encoding="utf-8")
    return path

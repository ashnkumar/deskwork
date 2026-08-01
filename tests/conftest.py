"""Shared fixtures.

Tests that need Postgres are marked `db` and skip cleanly when DATABASE_URL is unset, so
`pytest` does something useful on a laptop with nothing running. CI sets DATABASE_URL and
runs them for real.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS_DIR = REPO_ROOT / "corpus"
EMBED_MODEL = os.environ.get("DESKWORK_EMBED_MODEL", "BAAI/bge-small-en-v1.5")


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set; skipping tests that need Postgres")
    return url


@pytest.fixture(scope="session")
def conn(database_url: str):
    from deskwork import db

    connection = db.connect(database_url)
    db.init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture(scope="session")
def ingested(conn):
    """Ingest the real corpus once per session.

    Deliberately the real PDFs and the real embedding model. The point of these tests is to
    catch retrieval regressions on the actual documents, which a synthetic fixture cannot do.
    """
    from deskwork import ingest

    counts = ingest.ingest_directory(conn, CORPUS_DIR, EMBED_MODEL)
    assert sum(counts.values()) > 0, "corpus ingested zero chunks"
    return conn

"""Postgres access and schema.

One store for two things: the embedded document corpus the agent retrieves from, and the
submissions the portal writes. Keeping them together means the demo needs one container,
and it lets a test assert "did the agent's run actually land the right row?" without a
second connection.
"""

from __future__ import annotations

import psycopg
from pgvector.psycopg import register_vector

# bge-small-en-v1.5. Changing the embedding model means changing this and re-ingesting;
# the dimension is baked into the column type.
EMBED_DIM = 384

SCHEMA = f"""
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documents (
    id          SERIAL PRIMARY KEY,
    filename    TEXT NOT NULL UNIQUE,
    title       TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chunks (
    id          SERIAL PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal     INTEGER NOT NULL,
    page        INTEGER NOT NULL,
    text        TEXT NOT NULL,
    embedding   VECTOR({EMBED_DIM}) NOT NULL,
    UNIQUE (document_id, ordinal)
);

-- Cosine distance, matching the normalised embeddings bge produces.
CREATE INDEX IF NOT EXISTS chunks_embedding_idx
    ON chunks USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS submissions (
    id           SERIAL PRIMARY KEY,
    quarter      TEXT NOT NULL,
    department   TEXT NOT NULL,
    report_id    TEXT NOT NULL,
    answers      JSONB NOT NULL,
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def connect(database_url: str) -> psycopg.Connection:
    """Open a connection with pgvector's adapters registered.

    register_vector must run per-connection — without it, psycopg sends the embedding as a
    Python list and Postgres rejects it as the wrong type.
    """
    conn = psycopg.connect(database_url, autocommit=True)
    register_vector(conn)
    return conn


def init_schema(conn: psycopg.Connection) -> None:
    conn.execute(SCHEMA)

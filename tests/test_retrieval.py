"""Retrieval tests. No API key, no network — local embeddings and local Postgres.

The important test here is `test_corpus_eval`: it asserts that the facts the demo depends on
are actually findable. Retrieval that silently degrades is the failure mode that makes an
agent look stupid for reasons that have nothing to do with the model, and it is invisible
without a test like this.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from deskwork import ingest, retrieval
from tests.conftest import CORPUS_DIR, EMBED_MODEL

# (question, string that must appear, filename it must have come from)
# Ground truth read out of the source PDFs by hand.
#
# The filename is part of the oracle deliberately. A bare substring check passes when
# "460" turns up in an unrelated number, or when the generic word "electronic" appears
# anywhere at all — so it can go green without the answer-bearing passage ever being
# retrieved. Requiring the right document makes the assertion mean what its name claims.
RULE = "cms-0055-f-ncpdp-d0-final-rule-2020.pdf"
BULLETIN = "cms-hipaa-d0-information-bulletin-2020.pdf"

CORPUS_EVAL = [
    (
        "Which NCPDP Telecommunication Standard field must be used to identify partial "
        "fills for Schedule II drugs?",
        "460",
        {RULE, BULLETIN},
    ),
    ("What is the effective date of this final rule?", "March 24, 2020", {RULE}),
    ("By what date is compliance with these regulations required?", "September 21, 2020", {RULE}),
    (
        "What does HIPAA require the Secretary of HHS to adopt standards for?",
        "electronic",
        {RULE, BULLETIN},
    ),
    ("Which CFR section defines Schedule II drugs?", "1308.12", {RULE}),
]

TOP_K = 4


# --------------------------------------------------------------------------- pure functions


def test_normalise_rejoins_hyphenated_line_breaks():
    assert "administrative" in ingest.normalise("adminis-\ntrative simplification")


def test_normalise_keeps_paragraph_breaks():
    assert "\n\n" in ingest.normalise("First para.\n\n\n\nSecond para.")


def test_split_page_covers_the_whole_input():
    text = ". ".join(f"sentence number {i}" for i in range(200))
    pieces = ingest.split_page(text, size=300, overlap=50)
    assert len(pieces) > 1
    # Every non-overlapping character must survive somewhere.
    assert "sentence number 0" in pieces[0]
    assert "sentence number 199" in pieces[-1]


def test_split_page_windows_respect_size():
    text = "x" * 5000
    for piece in ingest.split_page(text, size=400, overlap=50):
        assert len(piece) <= 400


def test_split_page_rejects_overlap_at_least_size():
    with pytest.raises(ValueError):
        ingest.split_page("abc", size=100, overlap=100)


def test_split_page_of_empty_text_is_empty():
    assert ingest.split_page("") == []


def test_chunking_never_spans_a_page():
    """Page numbers are cited into a compliance form, so they have to be exact."""
    pdf = next(CORPUS_DIR.glob("*.pdf"))
    chunks = ingest.chunk_pdf(pdf)
    assert chunks
    # Ordinals are dense and ascending; page numbers never decrease.
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    pages = [c.page for c in chunks]
    assert pages == sorted(pages)


# ------------------------------------------------------------------------------ with a db


@pytest.mark.db
def test_ingest_is_idempotent(conn):
    pdf = CORPUS_DIR / "cms-hipaa-d0-information-bulletin-2020.pdf"
    first = ingest.ingest_file(conn, pdf, EMBED_MODEL)
    second = ingest.ingest_file(conn, pdf, EMBED_MODEL)
    assert first == second
    rows = conn.execute(
        "SELECT count(*) FROM documents WHERE filename = %s", (pdf.name,)
    ).fetchone()
    assert rows is not None and rows[0] == 1, "re-ingesting duplicated the document row"


@pytest.mark.db
def test_search_returns_k_scored_passages(ingested):
    passages = retrieval.search(ingested, "HIPAA administrative simplification", EMBED_MODEL, k=3)
    assert len(passages) == 3
    assert all(0.0 <= p.score <= 1.0 for p in passages)
    # Ordered best-first.
    assert [p.score for p in passages] == sorted((p.score for p in passages), reverse=True)
    assert all(p.filename.endswith(".pdf") and p.page >= 1 for p in passages)


@pytest.mark.db
@pytest.mark.parametrize(
    ("question", "expected", "sources"), CORPUS_EVAL, ids=lambda v: str(v)[:40]
)
def test_corpus_eval(ingested, question: str, expected: str, sources: set[str]):
    """Every fact the demo depends on must be retrievable at the configured top-k.

    The passage carrying the answer must also come from a document that genuinely
    contains it — otherwise a coincidental substring match anywhere in the corpus is
    enough to call retrieval working.
    """
    passages = retrieval.search(ingested, question, EMBED_MODEL, k=TOP_K)
    hits = [p for p in passages if expected.lower() in p.text.lower() and p.filename in sources]
    assert hits, (
        f"{expected!r} not found in top-{TOP_K} of {sorted(sources)} for {question!r}. "
        f"Got: {[p.cite() for p in passages]}"
    )


@pytest.mark.db
def test_chunk_size_is_tuned(conn):
    """The configured chunk size must beat the obvious larger alternative.

    This exists because the first draft of this repo shipped 1100-character chunks and
    quietly answered 3 of these 5 questions. The number in ingest.py is a measurement, and
    this test is the measurement.
    """

    def score(size: int, overlap: int) -> int:
        ingest.ingest_directory(conn, CORPUS_DIR, EMBED_MODEL, size=size, overlap=overlap)
        found = 0
        for question, expected, sources in CORPUS_EVAL:
            passages = retrieval.search(conn, question, EMBED_MODEL, k=TOP_K)
            if any(expected.lower() in p.text.lower() and p.filename in sources for p in passages):
                found += 1
        return found

    tuned = score(ingest.CHUNK_CHARS, ingest.CHUNK_OVERLAP)
    assert tuned == len(CORPUS_EVAL), f"configured chunk size answers only {tuned}/5"
    assert tuned > score(1100, 150), "chunk size no longer beats the 1100-char baseline"

    # Leave the corpus at the configured size for any later test in the session.
    ingest.ingest_directory(conn, CORPUS_DIR, EMBED_MODEL)


@pytest.mark.db
def test_format_for_model_carries_citations(ingested):
    passages = retrieval.search(ingested, "partial fills Schedule II", EMBED_MODEL, k=2)
    rendered = retrieval.format_for_model(passages)
    for passage in passages:
        assert passage.cite() in rendered


def test_format_for_model_handles_no_results():
    assert "No matching passages" in retrieval.format_for_model([])


def test_corpus_ships_only_expected_documents():
    """Guard against an unvetted PDF being dropped into the corpus."""
    names = {p.name for p in Path(CORPUS_DIR).glob("*.pdf")}
    assert names == {
        "cms-0055-f-ncpdp-d0-final-rule-2020.pdf",
        "cms-hipaa-d0-information-bulletin-2020.pdf",
        "cms-hipaa-statutes-timeline-2021.pdf",
    }


@pytest.mark.db
def test_connect_bootstraps_a_completely_fresh_database(database_url):
    """A stranger's first run hits a database with no `vector` extension.

    This is a regression test for a real bug: register_vector() looks the type up in the
    catalog, so creating the extension later in init_schema() was too late and ingestion
    died with "vector type not found in the database". Every existing test passed, because
    the test database already had the extension.
    """
    import psycopg

    from deskwork import db

    scratch = "deskwork_fresh_boot_test"
    admin = psycopg.connect(database_url, autocommit=True)
    try:
        admin.execute(f'DROP DATABASE IF EXISTS "{scratch}"')
        admin.execute(f'CREATE DATABASE "{scratch}"')
    finally:
        admin.close()

    fresh_url = database_url.rsplit("/", 1)[0] + f"/{scratch}"
    try:
        conn = db.connect(fresh_url)  # must not raise
        db.init_schema(conn)
        conn.execute("INSERT INTO documents (filename, title, sha256) VALUES ('a','b','c')")
        row = conn.execute("SELECT count(*) FROM documents").fetchone()
        assert row is not None and row[0] == 1
        conn.close()
    finally:
        admin = psycopg.connect(database_url, autocommit=True)
        try:
            admin.execute(f'DROP DATABASE IF EXISTS "{scratch}"')
        finally:
            admin.close()

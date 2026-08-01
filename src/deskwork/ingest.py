"""Corpus ingestion: PDF → chunks → embeddings → Postgres.

Chunks never span a page. That costs a little context at page boundaries, but it means the
page number attached to a chunk is exactly right, and the agent cites page numbers into a
compliance form. A citation that is approximately right is worse than no citation.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import psycopg
from pypdf import PdfReader

from . import embeddings
from .db import init_schema

# Chosen by measurement, not taste — see tests/test_retrieval.py::test_chunk_size_is_tuned.
# On the corpus eval, 1100-char chunks answer 3/5 questions at k=3 and 500-char chunks
# answer 5/5. A dense regulatory page packs several unrelated facts into 1100 characters
# (the CMS-0055-F page 1 window holds the effective date, the compliance date, four staff
# phone numbers and the start of the background section), and averaging all of that into one
# vector buries the date a query is actually asking about.
CHUNK_CHARS = 500
CHUNK_OVERLAP = 90

# Federal Register PDFs are multi-column and extract with hard-wrapped lines and hyphenated
# line breaks. Left as-is, "adminis-\ntrative" never matches a search for "administrative".
_HYPHEN_BREAK = re.compile(r"(\w)-\n(\w)")
_SOFT_WRAP = re.compile(r"(?<![\n.:;!?])\n(?![\n•])")
_WHITESPACE = re.compile(r"[ \t]+")
_BLANK_LINES = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    page: int
    text: str


def normalise(raw: str) -> str:
    """Undo PDF line-wrapping artefacts without destroying paragraph structure."""
    text = _HYPHEN_BREAK.sub(r"\1\2", raw)
    text = _SOFT_WRAP.sub(" ", text)
    text = _WHITESPACE.sub(" ", text)
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip()


def split_page(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Fixed-window split that prefers to break at a sentence end.

    Deliberately boring. A smarter splitter is not what makes this repo interesting, and a
    reader should be able to confirm it is correct at a glance.
    """
    if not text:
        return []
    if overlap >= size:
        raise ValueError("overlap must be smaller than size")

    out: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            window = text[start:end]
            breakpoint_ = max(window.rfind(". "), window.rfind(".\n"))
            # Only honour the sentence break if it is not pathologically early.
            if breakpoint_ > size // 2:
                end = start + breakpoint_ + 1
        out.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [c for c in out if c]


def chunk_pdf(path: Path, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[Chunk]:
    reader = PdfReader(str(path))
    chunks: list[Chunk] = []
    ordinal = 0
    for page_index, page in enumerate(reader.pages, start=1):
        text = normalise(page.extract_text() or "")
        for piece in split_page(text, size=size, overlap=overlap):
            chunks.append(Chunk(ordinal=ordinal, page=page_index, text=piece))
            ordinal += 1
    return chunks


def _title_of(path: Path, chunks: list[Chunk]) -> str:
    """First non-trivial line of page 1, falling back to the filename."""
    for chunk in chunks:
        if chunk.page != 1:
            break
        for line in chunk.text.splitlines():
            candidate = line.strip()
            if len(candidate) > 12:
                return candidate[:200]
    return path.stem


def ingest_file(
    conn: psycopg.Connection,
    path: Path,
    model_name: str,
    size: int = CHUNK_CHARS,
    overlap: int = CHUNK_OVERLAP,
) -> int:
    """Ingest one PDF. Re-ingesting a file replaces it, so this is safe to re-run."""
    chunks = chunk_pdf(path, size=size, overlap=overlap)
    if not chunks:
        return 0

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    vectors = embeddings.embed_documents([c.text for c in chunks], model_name)

    with conn.transaction():
        conn.execute("DELETE FROM documents WHERE filename = %s", (path.name,))
        row = conn.execute(
            "INSERT INTO documents (filename, title, sha256) VALUES (%s, %s, %s) RETURNING id",
            (path.name, _title_of(path, chunks), digest),
        ).fetchone()
        assert row is not None
        document_id = row[0]
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO chunks (document_id, ordinal, page, text, embedding)"
                " VALUES (%s, %s, %s, %s, %s)",
                [
                    (document_id, c.ordinal, c.page, c.text, v)
                    # strict=True: a length mismatch here would silently drop the tail of
                    # the document rather than fail, which is the worst way to lose data.
                    for c, v in zip(chunks, vectors, strict=True)
                ],
            )
    return len(chunks)


def ingest_directory(
    conn: psycopg.Connection,
    directory: Path,
    model_name: str,
    size: int = CHUNK_CHARS,
    overlap: int = CHUNK_OVERLAP,
) -> dict[str, int]:
    init_schema(conn)
    counts: dict[str, int] = {}
    for pdf in sorted(directory.glob("*.pdf")):
        counts[pdf.name] = ingest_file(conn, pdf, model_name, size=size, overlap=overlap)
    return counts

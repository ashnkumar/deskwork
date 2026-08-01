"""Semantic search over the ingested corpus.

This is the whole retrieval layer. It is one embedding call and one SQL query, and that is
the point — the reference implementation imported eight LangChain symbols to wrap the same
two operations, and used exactly one of them.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg

from . import embeddings


@dataclass(frozen=True)
class Passage:
    text: str
    filename: str
    title: str
    page: int
    score: float

    def cite(self) -> str:
        return f"{self.filename} p.{self.page}"


# `<=>` is pgvector's cosine distance: 0 identical, 2 opposite. Reported as similarity so
# that larger is better, which is what a reader expects from something called a score.
_SQL = """
SELECT c.text, d.filename, d.title, c.page, 1 - (c.embedding <=> %s::vector) AS score
FROM chunks c
JOIN documents d ON d.id = c.document_id
ORDER BY c.embedding <=> %s::vector
LIMIT %s
"""


def search(conn: psycopg.Connection, query: str, model_name: str, k: int = 4) -> list[Passage]:
    vector = embeddings.embed_query(query, model_name)
    rows = conn.execute(_SQL, (vector, vector, k)).fetchall()
    return [
        Passage(text=text, filename=filename, title=title, page=page, score=float(score))
        for text, filename, title, page, score in rows
    ]


def format_for_model(passages: list[Passage]) -> str:
    """Render passages for a tool result.

    Every passage carries its citation inline. The system prompt tells the agent to quote
    the citation when it enters a regulatory fact, and it can only do that if the citation
    travels with the text it came from.
    """
    if not passages:
        return "No matching passages found in the corpus."
    return "\n\n".join(
        f"[{i}] {p.cite()} (relevance {p.score:.2f})\n{p.text}"
        for i, p in enumerate(passages, start=1)
    )

"""The `search_regulations` tool — the retrieval half of the pattern.

This is what stops the agent inventing a regulation. It is a custom tool rather than
context stuffed into the system prompt, which matters: the agent retrieves *when it notices
it needs to*, mid-task, having just read the question off the screen. Pre-loading the
prompt with the corpus would answer questions the agent has not been asked yet, and would
not scale past a corpus that fits in context.
"""

from __future__ import annotations

import psycopg

from .. import retrieval
from .base import ToolResult

DESCRIPTION = (
    "Search the authoritative regulatory corpus (HIPAA Administrative Simplification "
    "publications from CMS and the Federal Register). Use this for any regulatory fact: "
    "rule identifiers, field names and numbers, effective dates, compliance deadlines, CFR "
    "citations. Returns verbatim passages with the source filename and page number. Never "
    "answer a regulatory question from memory — search first, then quote what you find."
)


class SearchRegulationsTool:
    name = "search_regulations"

    def __init__(self, conn: psycopg.Connection, embed_model: str, top_k: int = 4) -> None:
        self.conn = conn
        self.embed_model = embed_model
        self.top_k = top_k

    def to_params(self) -> dict:
        return {
            "name": self.name,
            "description": DESCRIPTION,
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "A natural-language question. Phrase it as the question you "
                            "actually need answered, not as keywords."
                        ),
                    },
                    "k": {
                        "type": "integer",
                        "description": "How many passages to return. Defaults to 4.",
                        "minimum": 1,
                        "maximum": 10,
                    },
                },
                "required": ["query"],
            },
        }

    def __call__(self, **payload) -> ToolResult:
        query = payload.get("query")
        if not isinstance(query, str) or not query.strip():
            return ToolResult.error("`query` must be a non-empty string.")
        try:
            k = int(payload.get("k") or self.top_k)
        except (TypeError, ValueError):
            return ToolResult.error("`k` must be an integer.")
        k = max(1, min(k, 10))

        passages = retrieval.search(self.conn, query.strip(), self.embed_model, k=k)
        return ToolResult(output=retrieval.format_for_model(passages))

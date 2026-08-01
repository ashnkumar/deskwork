"""Local embeddings.

Anthropic has no embeddings endpoint, and the deliverable is that a stranger runs this with
exactly one API key — so a hosted embedding provider is out. The model runs in-process,
which also means the whole retrieval path is deterministic and testable with no network.

bge-small-en-v1.5 is asymmetric. Its model card prescribes a short instruction prefix on
*queries* but not on the passages being indexed, for short-query-to-long-passage retrieval —
which is exactly what this is.

Note that fastembed's `query_embed()` does **not** apply that prefix for this model: it is
byte-identical to `embed()` (measured cosine similarity 1.0, versus 0.961 against the
correctly prefixed text). So the prefix is applied here explicitly. Relying on
`query_embed()` to do it would silently cost recall with nothing to notice in a test.
"""

from __future__ import annotations

from functools import lru_cache

from fastembed import TextEmbedding

QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


@lru_cache(maxsize=2)
def _model(name: str) -> TextEmbedding:
    """Loading weights takes seconds, so do it once per process."""
    return TextEmbedding(model_name=name)


def embed_documents(texts: list[str], model_name: str) -> list[list[float]]:
    """Embed passages for indexing — no prefix."""
    if not texts:
        return []
    return [v.tolist() for v in _model(model_name).embed(texts)]


def embed_query(text: str, model_name: str) -> list[float]:
    """Embed a search query — prefixed, per the model card."""
    return next(iter(_model(model_name).embed([QUERY_PREFIX + text]))).tolist()


def warm(model_name: str) -> None:
    """Force the weight download at image-build time rather than mid-demo."""
    embed_documents(["warm"], model_name)

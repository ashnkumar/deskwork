"""The corpus hygiene claim, as a test rather than a sentence.

`corpus/SOURCES.md` says the shipped PDFs carry no personal identifiers. That was true when it
was written, and a sentence in a markdown file has no way of staying true — the corpus is the
one part of this repository a reader is actively invited to replace. Running the patterns here
means swapping in a PDF that does carry identifiers fails the suite instead of shipping.

This is a hygiene check on public-domain government publications, not a privacy guarantee. It
matches patterns; it cannot recognise an identifier that does not look like one.
"""

from __future__ import annotations

import re

import pytest
from pypdf import PdfReader

from tests.conftest import CORPUS_DIR

PATTERNS = {
    "US Social Security number": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "employer identification number": re.compile(r"\b\d{2}-\d{7}\b"),
    "date of birth": re.compile(r"\b(?:DOB|date of birth)\b", re.IGNORECASE),
    "medical record number": re.compile(
        r"\b(?:MRN|medical record (?:number|no\.?))\b", re.IGNORECASE
    ),
    "patient name": re.compile(r"\bpatient(?:'s)?\s+name\b", re.IGNORECASE),
}


def _text_of(pdf) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(str(pdf)).pages)


@pytest.mark.parametrize("pdf", sorted(CORPUS_DIR.glob("*.pdf")), ids=lambda p: p.name)
def test_no_personal_identifiers_in_the_corpus(pdf):
    text = _text_of(pdf)
    assert text.strip(), f"{pdf.name} extracted to nothing — a scanned PDF cannot be checked"
    found = {
        label: pattern.findall(text)[:3]
        for label, pattern in PATTERNS.items()
        if pattern.search(text)
    }
    assert not found, f"{pdf.name} matches identifier patterns: {found}"


def test_the_corpus_is_the_three_documents_the_docs_describe():
    """SOURCES.md, SPEC and both diagrams all name three PDFs."""
    assert len(list(CORPUS_DIR.glob("*.pdf"))) == 3

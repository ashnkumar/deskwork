"""Grading a filed report against the corpus it was supposed to come from.

Split out of the CLI because this is the part that has to be right. It is the only thing in
the repository that decides whether a run succeeded, and everything around it is argument
parsing. Pure functions, so the cases that matter — a negated answer, an invented filename,
a page that does not exist, a citation that points somewhere the answer isn't — are unit
tests rather than something you can only exercise by spending money on a live run.

What this can and cannot do is worth stating exactly, because the earlier version of this
check claimed more than it did. It verifies three things:

  1. the expected value is present in the answer,
  2. the citation names a document that is actually in the corpus, at a page that exists,
  3. the text on that page actually contains the value the answer asserts.

(3) is what makes this grading against the source documents rather than against a wish. The
`chunks` table is the extracted text of those PDFs, so the check reads the same bytes the
agent retrieved from.

It is not semantic validation. It cannot tell whether a sentence *means* what the page
means, only whether the value is there and the citation leads to it. The one specific way a
wrong answer used to slip through — stating the correct value under a negation — is closed
below, but that is a named hole being plugged, not a general guarantee.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# What a correct run must end up having entered, read out of the source PDFs by hand. Every
# entry is a tuple: all of its parts must appear. `ncpdp_field` needs both the name and the
# number, because "460" alone also matches a phone number and a paragraph reference on the
# same page.
EXPECTED: dict[str, tuple[str, ...]] = {
    "ncpdp_field": ("quantity prescribed", "460"),
    "effective_date": ("march 24, 2020",),
    "compliance_date": ("september 21, 2020",),
}

# The Federal Register sets the field identifier with an en dash where the bulletin uses a
# plain hyphen, and PDF extraction faithfully produces both. Written as escapes rather than
# literal characters because the whole point is that these are hard to tell apart on sight:
# U+2010 hyphen through U+2015 horizontal bar, plus U+2212 minus.
_DASHES = re.compile("[\u2010-\u2015\u2212]")
_SPACES = re.compile(r"\s+")

# A value stated under a negation is not that value. Deliberately narrow: only these words,
# and only immediately before the value. It closes a demonstrated false PASS; it is not an
# attempt to understand the sentence.
_NEGATIONS = ("not", "isn't", "is not", "no", "never", "nor", "neither", "cannot", "n't")
_NEGATION_WINDOW = 24

# `p.1`, `p. 1`, `page 1`, `pp. 4-5`, and the same with an en dash.
_PAGE_REF = re.compile(
    "\\b(?:p{1,2}\\.?|pages?)\\s*(\\d+)\\s*(?:[-\u2013]\\s*(\\d+))?", re.IGNORECASE
)


def normalize(text: str) -> str:
    """Lowercase, fold dash variants, collapse whitespace. Applied to both sides of every
    comparison, so the corpus and the answer are matched on the same footing."""
    return _SPACES.sub(" ", _DASHES.sub("-", text or "").lower()).strip()


def _negated(haystack: str, position: int) -> bool:
    """Is the match at `position` immediately preceded by a negation?"""
    window = haystack[max(0, position - _NEGATION_WINDOW) : position]
    return any(re.search(rf"\b{re.escape(word)}\b", window) for word in _NEGATIONS)


def contains_value(answer: str, value: str) -> bool:
    """Does `answer` assert `value`, rather than deny it?

    A value can legitimately appear more than once ("March 24, 2020. ... effective on
    March 24, 2020"). One un-negated occurrence is enough; requiring every occurrence to be
    clean would fail on a correct answer that also explains what the date is *not*.
    """
    hay = normalize(answer)
    needle = normalize(value)
    if not needle:
        return False
    start = 0
    while (found := hay.find(needle, start)) != -1:
        if not _negated(hay, found):
            return True
        start = found + 1
    return False


def parse_citations(citation: str, known_filenames: set[str]) -> set[tuple[str, int]]:
    """Pull every (filename, page) pair out of a free-text citation field.

    The form asks for one citation covering all three answers, and the agent writes prose:
    a filename, some page references, sometimes a second corroborating document. Only
    filenames that are actually in the corpus are recognised, which is what stops an
    invented source from counting. Page references are attributed to the filename that most
    recently preceded them.
    """
    text = normalize(citation)
    hits: list[tuple[int, str]] = []
    for name in known_filenames:
        needle = normalize(name)
        start = 0
        while (found := text.find(needle, start)) != -1:
            hits.append((found, name))
            start = found + len(needle)
    if not hits:
        return set()

    hits.sort()
    cited: set[tuple[str, int]] = set()
    for i, (position, name) in enumerate(hits):
        end = hits[i + 1][0] if i + 1 < len(hits) else len(text)
        for match in _PAGE_REF.finditer(text, position, end):
            first, last = match.group(1), match.group(2)
            lo, hi = int(first), int(last or first)
            if hi < lo or hi - lo > 50:  # "pp. 9-1" is a typo, not a 9-page span
                continue
            for page in range(lo, hi + 1):
                cited.add((name, page))
    return cited


@dataclass
class Check:
    key: str
    ok: bool
    detail: str


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    def add(self, key: str, ok: bool, detail: str) -> None:
        self.checks.append(Check(key=key, ok=ok, detail=detail))


def grade(answers: dict, corpus: dict[tuple[str, int], str]) -> Report:
    """Grade one filed submission.

    `corpus` maps (filename, page) to that page's extracted text — the same text the agent
    retrieved from. A page missing from this mapping does not exist in the corpus, which is
    how an invented page number is caught.
    """
    report = Report()
    known = {filename for filename, _ in corpus}
    cited = parse_citations(str(answers.get("citation", "")), known)

    report.add(
        "citation",
        bool(cited),
        f"names {sorted({name for name, _ in cited})} at pages "
        f"{sorted({page for _, page in cited})}"
        if cited
        else "no corpus document and page number found in the citation field",
    )

    cited_text = normalize(" ".join(corpus[ref] for ref in sorted(cited) if ref in corpus))

    for key, parts in EXPECTED.items():
        answer = str(answers.get(key, ""))
        missing = [part for part in parts if not contains_value(answer, part)]
        if missing:
            report.add(key, False, f"answer does not assert {missing!r}: {answer[:70]!r}")
            continue
        # The answer is right. Is it *supported* by the page the run cited?
        unsupported = [part for part in parts if normalize(part) not in cited_text]
        if unsupported:
            report.add(
                key,
                False,
                f"cited pages do not contain {unsupported!r} — answer is correct but not "
                f"sourced where it says",
            )
            continue
        report.add(key, True, f"{answer[:70]!r}")

    return report

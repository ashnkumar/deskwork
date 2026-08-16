"""The grader's own tests.

These exist because the grader is the one component whose failure is invisible. A retrieval
bug shows up as a bad answer; a grading bug shows up as PASS. Every test below is a wrong
report that an earlier version of this code accepted.

No database and no network: `grade()` takes the corpus as a plain mapping, so the cases that
matter are cheap enough to enumerate.
"""

from __future__ import annotations

import pytest

from deskwork import grading

FINAL_RULE = "cms-0055-f-ncpdp-d0-final-rule-2020.pdf"
BULLETIN = "cms-hipaa-d0-information-bulletin-2020.pdf"
TIMELINE = "cms-hipaa-statutes-timeline-2021.pdf"

# A stand-in for the ingested corpus, carrying the same facts as the real page 1.
CORPUS = {
    (FINAL_RULE, 1): (
        "DATES: Effective Date: This final rule is effective on March 24, 2020. "
        "Compliance Date: Compliance with these regulations is required by "
        "September 21, 2020. Covered entities must use the Quantity Prescribed "
        "(460-ET) field for retail pharmacy transactions for Schedule II drugs."
    ),
    (FINAL_RULE, 7): "45 CFR 162.1102(d), 162.1302(d), 162.1802(d).",
    (BULLETIN, 1): "The Quantity Prescribed (460-ET) field identifies partial fills.",
    (TIMELINE, 1): "Timeline of key statutes and regulations.",
}

CORRECT = {
    "ncpdp_field": "The Quantity Prescribed (460-ET) field.",
    "effective_date": "March 24, 2020.",
    "compliance_date": "September 21, 2020.",
    "citation": f"{FINAL_RULE}, p. 1 (DATES section).",
}


def test_a_correct_report_passes():
    assert grading.grade(CORRECT, CORPUS).ok


def test_the_real_citation_prose_parses():
    """The agent writes prose, not a structured field. This is a verbatim citation from a
    recorded run — if the parser regresses, every real run starts failing."""
    answers = {
        **CORRECT,
        "citation": (
            f"{FINAL_RULE}, p. 1 (field name/number and both dates); dates also discussed "
            f"at pp. 4-5. Published at 85 FR, Vol. 85, No. 16, January 24, 2020. Also "
            f"corroborated by {BULLETIN}, p. 1."
        ),
    }
    assert grading.grade(answers, CORPUS).ok


# --------------------------------------------------------------- reports that must fail


def test_an_invented_source_file_fails():
    """`.pdf` appearing somewhere in the field used to be the whole citation check."""
    answers = {**CORRECT, "citation": "invented.pdf p.999"}
    assert not grading.grade(answers, CORPUS).ok


def test_a_bare_pdf_mention_fails():
    answers = {**CORRECT, "citation": "see the attached .pdf"}
    assert not grading.grade(answers, CORPUS).ok


def test_a_page_that_does_not_exist_fails():
    answers = {**CORRECT, "citation": f"{FINAL_RULE} p.999"}
    assert not grading.grade(answers, CORPUS).ok


def test_a_real_page_that_does_not_support_the_answer_fails():
    """The citation is real and the answers are right, but the page does not say so. This is
    the case that separates grading against the corpus from grading against a string."""
    answers = {**CORRECT, "citation": f"{TIMELINE} p.1"}
    assert not grading.grade(answers, CORPUS).ok


@pytest.mark.parametrize("key", ["ncpdp_field", "effective_date", "compliance_date"])
def test_a_negated_answer_fails(key: str):
    """Substring matching passed "not March 24, 2020" because it contains the date."""
    negated = {
        "ncpdp_field": "It is not the Quantity Prescribed (460-ET) field.",
        "effective_date": "The rule is not effective on March 24, 2020.",
        "compliance_date": "Compliance is not required by September 21, 2020.",
    }[key]
    assert not grading.grade({**CORRECT, key: negated}, CORPUS).ok


def test_a_correct_answer_that_mentions_a_contrast_still_passes():
    """The negation guard must not punish a more careful answer."""
    answers = {
        **CORRECT,
        "effective_date": (
            "March 24, 2020. Note that it is not effective on January 24, 2020, which is "
            "the publication date."
        ),
    }
    assert grading.grade(answers, CORPUS).ok


def test_the_field_number_alone_is_not_enough():
    """`460` on its own also matches a paragraph reference and a phone number on the same
    page, so the field name is required with it."""
    assert not grading.grade({**CORRECT, "ncpdp_field": "460"}, CORPUS).ok


def test_a_missing_answer_fails():
    assert not grading.grade({**CORRECT, "effective_date": ""}, CORPUS).ok


def test_an_empty_submission_fails():
    assert not grading.grade({}, CORPUS).ok


# ------------------------------------------------------------------- the citation parser


def test_parse_citations_reads_pages_and_ranges():
    cited = grading.parse_citations(
        f"{FINAL_RULE}, pp. 4-5 and {BULLETIN} page 1", {FINAL_RULE, BULLETIN}
    )
    assert cited == {(FINAL_RULE, 4), (FINAL_RULE, 5), (BULLETIN, 1)}


def test_parse_citations_attributes_pages_to_the_preceding_document():
    cited = grading.parse_citations(
        f"{FINAL_RULE} p.1, corroborated by {BULLETIN} p.2", {FINAL_RULE, BULLETIN}
    )
    assert cited == {(FINAL_RULE, 1), (BULLETIN, 2)}


def test_parse_citations_ignores_unknown_documents():
    assert grading.parse_citations("invented.pdf p.1", {FINAL_RULE}) == set()


def test_parse_citations_needs_a_page_number():
    assert grading.parse_citations(FINAL_RULE, {FINAL_RULE}) == set()


def test_normalize_folds_the_dash_variants():
    """The Federal Register prints the field id with an en dash; the bulletin uses a hyphen."""
    assert grading.normalize("460\u2013ET") == grading.normalize("460-ET")

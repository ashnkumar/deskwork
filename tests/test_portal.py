"""Portal tests — the full wizard driven over HTTP, no browser and no model.

These matter more than they look. If the portal's validation or redirects regress, the
agent run fails in a way that looks like a model problem, and you waste an afternoon
reading transcripts. Pinning the target's behaviour keeps agent failures attributable.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from portal.app import ATTESTATIONS, create_app

GOOD = {"quarter": "Q3 2025", "department": "Pharmacy", "report_id": "QI-2025-014"}
ANSWERS = {
    "ncpdp_field": "Quantity Prescribed (460-ET)",
    "effective_date": "March 24, 2020",
    "compliance_date": "September 21, 2020",
    "citation": "cms-0055-f-ncpdp-d0-final-rule-2020.pdf p.1",
}


@pytest.fixture
def client():
    return TestClient(create_app())


def _start(client) -> str:
    response = client.get("/report/new", follow_redirects=False)
    assert response.status_code == 303
    return response.headers["location"].split("/")[2]


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_index_offers_a_new_report(client):
    body = client.get("/").text
    assert "Quality Improvement Reporting" in body
    assert "/report/new" in body


def test_period_step_renders_all_choices(client):
    draft_id = _start(client)
    body = client.get(f"/report/{draft_id}/period").text
    assert "Q3 2025" in body and "Pharmacy" in body
    assert "QI-YYYY-NNN" in body, "the format hint must be on screen for the agent to read"


@pytest.mark.parametrize(
    ("payload", "fragment"),
    [
        ({**GOOD, "report_id": "14"}, "not valid"),
        ({**GOOD, "report_id": ""}, "not valid"),
        ({**GOOD, "quarter": ""}, "Select a reporting quarter"),
        ({**GOOD, "department": "Astrology"}, "Select a department"),
    ],
)
def test_period_step_rejects_bad_input_with_a_visible_error(client, payload, fragment):
    draft_id = _start(client)
    response = client.post(f"/report/{draft_id}/period", data=payload)
    assert response.status_code == 400
    assert fragment in response.text


def test_period_step_preserves_input_after_an_error(client):
    """A form that clears itself on error would punish the agent for one bad field.

    Asserted on the specific input element. A bare `"nope" in response.text` also matches
    the validation message that quotes it back, and `value="Q3 2025"` is present in the
    static <option> list whether or not it was selected — both pass on a broken form.
    """
    draft_id = _start(client)
    response = client.post(f"/report/{draft_id}/period", data={**GOOD, "report_id": "nope"})
    assert 'id="report_id" name="report_id" value="nope"' in response.text
    assert '<option value="Q3 2025" selected>' in response.text


def test_attestations_require_every_answer(client):
    draft_id = _start(client)
    client.post(f"/report/{draft_id}/period", data=GOOD)
    partial = {**ANSWERS, "compliance_date": ""}
    response = client.post(f"/report/{draft_id}/attestations", data=partial)
    assert response.status_code == 400
    assert "This field is required." in response.text


def test_cannot_skip_ahead_to_review(client):
    """Deep-linking past an incomplete step returns to the start rather than half-rendering."""
    draft_id = _start(client)
    response = client.get(f"/report/{draft_id}/review", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_unknown_draft_id_redirects_home(client):
    response = client.get("/report/deadbeef/period", follow_redirects=False)
    assert response.status_code == 303


@pytest.mark.db
def test_full_wizard_writes_a_submission(client, conn):
    draft_id = _start(client)

    assert (
        client.post(f"/report/{draft_id}/period", data=GOOD, follow_redirects=False).status_code
        == 303
    )
    assert (
        client.post(
            f"/report/{draft_id}/attestations", data=ANSWERS, follow_redirects=False
        ).status_code
        == 303
    )

    review = client.get(f"/report/{draft_id}/review").text
    assert "Quantity Prescribed (460-ET)" in review
    assert "QI-2025-014" in review

    submitted = client.post(f"/report/{draft_id}/submit", follow_redirects=False)
    assert submitted.status_code == 303
    submission_id = int(submitted.headers["location"].rsplit("/", 1)[1])

    confirmation = client.get(f"/report/done/{submission_id}").text
    assert f"QIR-{submission_id:05d}" in confirmation

    row = conn.execute(
        "SELECT quarter, department, report_id, answers FROM submissions WHERE id = %s",
        (submission_id,),
    ).fetchone()
    assert row is not None
    quarter, department, report_id, answers = row
    assert (quarter, department, report_id) == ("Q3 2025", "Pharmacy", "QI-2025-014")
    assert answers["ncpdp_field"] == "Quantity Prescribed (460-ET)"


def test_every_attestation_has_a_stable_key():
    """The grader in scripts/ matches on these keys; renaming one silently breaks it."""
    assert [key for key, _ in ATTESTATIONS] == [
        "ncpdp_field",
        "effective_date",
        "compliance_date",
        "citation",
    ]


def test_a_rejected_attestation_does_not_unlock_review(client):
    """The bypass: storing answers before validating made a blank draft look complete."""
    draft_id = _start(client)
    client.post(f"/report/{draft_id}/period", data=GOOD)
    rejected = client.post(
        f"/report/{draft_id}/attestations", data={**ANSWERS, "compliance_date": ""}
    )
    assert rejected.status_code == 400

    review = client.get(f"/report/{draft_id}/review", follow_redirects=False)
    assert review.status_code == 303, "review was reachable after a rejected step"

    submitted = client.post(f"/report/{draft_id}/submit", follow_redirects=False)
    assert submitted.status_code == 303
    assert submitted.headers["location"] == "/", "a blank report was filed"


def test_a_rejected_attestation_still_redisplays_what_was_typed(client):
    draft_id = _start(client)
    client.post(f"/report/{draft_id}/period", data=GOOD)
    response = client.post(
        f"/report/{draft_id}/attestations", data={**ANSWERS, "compliance_date": ""}
    )
    assert "Quantity Prescribed (460-ET)" in response.text


@pytest.mark.db
def test_confirmation_page_refuses_a_reference_that_was_never_filed(client, conn):
    """Rendering /report/done/999999 would hand the agent a fabricated success page."""
    response = client.get("/report/done/999999")
    assert response.status_code == 404
    assert "No such report" in response.text


def test_draft_store_is_bounded(client):
    """Unbounded growth from repeated /report/new is a slow leak in a long-lived portal."""
    from portal.app import DRAFTS, MAX_DRAFTS

    for _ in range(MAX_DRAFTS + 20):
        client.get("/report/new", follow_redirects=False)
    assert len(DRAFTS) <= MAX_DRAFTS

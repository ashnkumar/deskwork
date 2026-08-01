"""The QI Portal — the software the work lives in.

This is the target the agent operates. It ships with the repo, and the README says so; it
is not third-party software and pretending otherwise would be dishonest. What it is not is
a strawman: it is a three-step wizard with server-side validation, required fields with
format rules, and errors that only appear on screen. The agent has to read the page to get
through it, which is the entire point.

Deliberately server-rendered with no JavaScript framework. A SPA would add load-timing
flakiness that has nothing to do with the pattern being demonstrated.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

QUARTERS = ["Q1 2025", "Q2 2025", "Q3 2025", "Q4 2025"]
DEPARTMENTS = ["Cardiology", "Emergency", "Oncology", "Pharmacy", "Radiology"]

# Real format validation, so a wrong guess produces an on-screen error the agent must read.
REPORT_ID_RE = re.compile(r"^QI-\d{4}-\d{3}$")

ATTESTATIONS = [
    (
        "ncpdp_field",
        "Under final rule CMS-0055-F, which NCPDP Telecommunication Standard field must be "
        "used to identify partial fills for Schedule II drugs? Give the field name and number.",
    ),
    ("effective_date", "What is the effective date of that final rule?"),
    ("compliance_date", "By what date is compliance with the modified standard required?"),
    (
        "citation",
        "Cite the source document and page number supporting the answers above.",
    ),
]

MIN_ANSWER_CHARS = 4


@dataclass
class Draft:
    quarter: str = ""
    department: str = ""
    report_id: str = ""
    answers: dict[str, str] = field(default_factory=dict)
    # Set only when every attestation validated. Inferring completeness from `answers`
    # being non-empty is what let a rejected step through to review.
    complete: bool = False


# In-memory, keyed by an id in the URL. A single-user demo does not need Redis, and a
# restart losing an in-flight draft costs nothing.
DRAFTS: dict[str, Draft] = {}
MAX_DRAFTS = 64


def _remember(draft_id: str, draft: Draft) -> None:
    """Keep the store bounded — every /report/new otherwise retains a Draft forever."""
    while len(DRAFTS) >= MAX_DRAFTS:
        DRAFTS.pop(next(iter(DRAFTS)))
    DRAFTS[draft_id] = draft


def _database_url() -> str:
    return os.environ.get("DATABASE_URL", "postgresql://deskwork:deskwork@db:5432/deskwork")


def _save_submission(draft: Draft) -> int:
    from deskwork.db import connect, init_schema

    conn = connect(_database_url())
    try:
        init_schema(conn)
        row = conn.execute(
            "INSERT INTO submissions (quarter, department, report_id, answers)"
            " VALUES (%s, %s, %s, %s) RETURNING id",
            (draft.quarter, draft.department, draft.report_id, json.dumps(draft.answers)),
        ).fetchone()
        assert row is not None
        return int(row[0])
    finally:
        conn.close()


def _submission_exists(submission_id: int) -> bool:
    from deskwork.db import connect, init_schema

    conn = connect(_database_url())
    try:
        init_schema(conn)
        row = conn.execute("SELECT 1 FROM submissions WHERE id = %s", (submission_id,)).fetchone()
        return row is not None
    finally:
        conn.close()


def create_app() -> FastAPI:
    app = FastAPI(title="QI Portal", docs_url=None, redoc_url=None)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return TEMPLATES.TemplateResponse(request, "index.html", {})

    @app.get("/report/new")
    def new_report():
        draft_id = uuid.uuid4().hex[:8]
        _remember(draft_id, Draft())
        return RedirectResponse(f"/report/{draft_id}/period", status_code=303)

    # ---------------------------------------------------------------- step 1: period & unit

    @app.get("/report/{draft_id}/period", response_class=HTMLResponse)
    def get_period(request: Request, draft_id: str):
        draft = DRAFTS.get(draft_id)
        if draft is None:
            return RedirectResponse("/", status_code=303)
        return TEMPLATES.TemplateResponse(
            request,
            "period.html",
            {
                "draft_id": draft_id,
                "draft": draft,
                "quarters": QUARTERS,
                "departments": DEPARTMENTS,
                "error": None,
            },
        )

    @app.post("/report/{draft_id}/period", response_class=HTMLResponse)
    def post_period(
        request: Request,
        draft_id: str,
        quarter: str = Form(""),
        department: str = Form(""),
        report_id: str = Form(""),
    ):
        draft = DRAFTS.get(draft_id)
        if draft is None:
            return RedirectResponse("/", status_code=303)

        report_id = report_id.strip()
        error = None
        if quarter not in QUARTERS:
            error = "Select a reporting quarter."
        elif department not in DEPARTMENTS:
            error = "Select a department."
        elif not REPORT_ID_RE.match(report_id):
            error = (
                f"Report ID {report_id!r} is not valid. Use the format QI-YYYY-NNN, "
                "for example QI-2025-014."
            )

        if error:
            return TEMPLATES.TemplateResponse(
                request,
                "period.html",
                {
                    "draft_id": draft_id,
                    "draft": Draft(quarter, department, report_id, draft.answers),
                    "quarters": QUARTERS,
                    "departments": DEPARTMENTS,
                    "error": error,
                },
                status_code=400,
            )

        draft.quarter, draft.department, draft.report_id = quarter, department, report_id
        return RedirectResponse(f"/report/{draft_id}/attestations", status_code=303)

    # ------------------------------------------------------------- step 2: attestations

    @app.get("/report/{draft_id}/attestations", response_class=HTMLResponse)
    def get_attestations(request: Request, draft_id: str):
        draft = DRAFTS.get(draft_id)
        if draft is None or not draft.quarter:
            return RedirectResponse("/", status_code=303)
        return TEMPLATES.TemplateResponse(
            request,
            "attestations.html",
            {
                "draft_id": draft_id,
                "draft": draft,
                "attestations": ATTESTATIONS,
                "errors": {},
            },
        )

    @app.post("/report/{draft_id}/attestations", response_class=HTMLResponse)
    async def post_attestations(request: Request, draft_id: str):
        draft = DRAFTS.get(draft_id)
        if draft is None or not draft.quarter:
            return RedirectResponse("/", status_code=303)

        form = await request.form()
        answers = {key: str(form.get(key, "")).strip() for key, _ in ATTESTATIONS}
        errors = {
            key: "This field is required."
            for key, value in answers.items()
            if len(value) < MIN_ANSWER_CHARS
        }

        # Only store answers once they are all valid. Storing them first left a truthy
        # four-key dict of blanks behind, so a GET of /review or a direct POST to /submit
        # sailed past the `not draft.answers` guard and filed an empty report.
        if errors:
            draft.answers = answers  # keep what was typed so the form can be re-rendered
            return TEMPLATES.TemplateResponse(
                request,
                "attestations.html",
                {
                    "draft_id": draft_id,
                    "draft": draft,
                    "attestations": ATTESTATIONS,
                    "errors": errors,
                },
                status_code=400,
            )
        draft.answers = answers
        draft.complete = True
        return RedirectResponse(f"/report/{draft_id}/review", status_code=303)

    # ------------------------------------------------------------------ step 3: review

    @app.get("/report/{draft_id}/review", response_class=HTMLResponse)
    def get_review(request: Request, draft_id: str):
        draft = DRAFTS.get(draft_id)
        if draft is None or not draft.complete:
            return RedirectResponse("/", status_code=303)
        return TEMPLATES.TemplateResponse(
            request,
            "review.html",
            {"draft_id": draft_id, "draft": draft, "attestations": ATTESTATIONS},
        )

    @app.post("/report/{draft_id}/submit")
    def submit(draft_id: str):
        # Pop first: two near-simultaneous POSTs would otherwise both read the same draft
        # and file it twice. Whichever request wins the pop does the insert.
        draft = DRAFTS.pop(draft_id, None)
        if draft is None or not draft.complete:
            return RedirectResponse("/", status_code=303)
        submission_id = _save_submission(draft)
        return RedirectResponse(f"/report/done/{submission_id}", status_code=303)

    @app.get("/report/done/{submission_id}", response_class=HTMLResponse)
    def done(request: Request, submission_id: int):
        # Look the row up rather than trusting the URL. Rendering a confirmation for
        # /report/done/12345 would hand the agent a fabricated success page to believe.
        if not _submission_exists(submission_id):
            return TEMPLATES.TemplateResponse(
                request, "not_found.html", {"submission_id": submission_id}, status_code=404
            )
        return TEMPLATES.TemplateResponse(
            request,
            "done.html",
            {"reference": f"QIR-{submission_id:05d}", "submission_id": submission_id},
        )

    return app


app = create_app()

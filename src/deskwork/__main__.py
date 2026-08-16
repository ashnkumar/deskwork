"""Command line entry point: `deskwork ingest | run | verify`."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import db, grading
from .agent import Step
from .agent import run as run_agent
from .config import Config
from .prompts import task_prompt
from .tools.computer import ComputerTool
from .tools.search import SearchRegulationsTool


def corpus_dir() -> Path:
    """Locate the corpus for both a source checkout and an installed package.

    `parents[2]` alone is wrong once the package is pip-installed: inside the container the
    module lives in site-packages and that path resolves somewhere in the Python install.
    The working directory is checked first, which is what makes `deskwork ingest` work in
    the container, where WORKDIR is /app and the corpus is copied to /app/corpus.
    """
    override = os.environ.get("DESKWORK_CORPUS_DIR")
    if override:
        return Path(override)
    local = Path.cwd() / "corpus"
    if local.is_dir():
        return local
    return Path(__file__).resolve().parents[2] / "corpus"


# The corpus as the grader reads it: one row per page, chunks reassembled in order. This is
# the same extracted text the agent retrieved from, which is what lets `verify` check a
# citation against the document instead of taking the agent's word for it.
_CORPUS_PAGES = """
SELECT d.filename, c.page, string_agg(c.text, ' ' ORDER BY c.ordinal)
FROM chunks c
JOIN documents d ON d.id = c.document_id
GROUP BY d.filename, c.page
"""


def cmd_ingest(config: Config) -> int:
    from .ingest import ingest_directory

    conn = db.connect(config.database_url)
    directory = corpus_dir()
    if not directory.is_dir():
        print(f"No corpus directory at {directory}. Set DESKWORK_CORPUS_DIR.", file=sys.stderr)
        return 2
    counts = ingest_directory(conn, directory, config.embed_model)
    for name, count in counts.items():
        print(f"  {count:>4} chunks  {name}")
    print(f"ingested {sum(counts.values())} chunks from {len(counts)} documents")
    return 0


def cmd_run(config: Config, args) -> int:
    import anthropic

    config.require_api_key()
    conn = db.connect(config.database_url)

    total = conn.execute("SELECT count(*) FROM chunks").fetchone()
    if not total or total[0] == 0:
        print("Corpus is empty. Run `deskwork ingest` first.", file=sys.stderr)
        return 2

    tools = [
        ComputerTool(
            width=config.display_width,
            height=config.display_height,
            display=args.display,
        ),
        SearchRegulationsTool(conn, config.embed_model, config.top_k),
    ]

    def show(step: Step) -> None:
        print(f"\n─── step {step.index}")
        if step.text.strip():
            print(f"  {step.text.strip()[:400]}")
        for name, payload in step.tool_calls:
            detail = payload.get("action") or payload.get("query") or ""
            print(f"  → {name}({str(detail)[:90]})")

    client = anthropic.Anthropic(api_key=config.api_key)
    result = run_agent(
        client,
        config,
        tools,
        task_prompt(args.quarter, args.department, args.report_id),
        on_step=show,
    )

    print(f"\n{'=' * 70}")
    print(f"stopped: {result.stopped_because} after {len(result.steps)} steps")
    print(f"tool calls: {result.tool_call_counts}")
    print(f"\n{result.final_text}")

    if args.transcript:
        # A run receipt, not just a narrative. Anything quoted about this run later — how
        # many steps, how long, how many tokens, what it cost — has to be readable out of
        # this file, or it is a recollection.
        Path(args.transcript).write_text(
            json.dumps(
                {
                    "model": result.model,
                    "effort": result.effort,
                    "task": {
                        "quarter": args.quarter,
                        "department": args.department,
                        "report_id": args.report_id,
                    },
                    "stopped_because": result.stopped_because,
                    "steps": len(result.steps),
                    "seconds": result.seconds,
                    "tool_calls": result.tool_call_counts,
                    "usage_totals": result.usage_totals,
                    "turns": [
                        {
                            "index": s.index,
                            "seconds": s.seconds,
                            "stop_reason": s.stop_reason,
                            "usage": s.usage,
                            "text": s.text,
                            "tools": [{"name": n, "input": p} for n, p in s.tool_calls],
                        }
                        for s in result.steps
                    ],
                },
                indent=2,
                default=str,
            )
        )
        print(f"\ntranscript written to {args.transcript}")

    # Non-zero when the run did not end because the model decided it was done. A run cut
    # off at the step budget may have clicked Submit without ever seeing the confirmation,
    # and scripts calling this need to be able to tell.
    if result.stopped_because != "model_finished":
        print(f"\nrun did not complete cleanly ({result.stopped_because})", file=sys.stderr)
        return 3
    return 0


def cmd_verify(config: Config, args) -> int:
    """Did the run actually file a correct report?

    This is what turns 'does it work?' into a number. It reads the row the portal wrote,
    not the agent's own account of what it did — an agent claiming success is not evidence.

    Scoped to the report ID that was asked for. Grading whichever row is newest would
    report PASS off a leftover submission from an earlier run, which is precisely the kind
    of false green a grader exists to prevent.

    The grading itself lives in `grading.py`, against the corpus rather than against a list
    of strings: the citation has to name a document that exists at a page that exists, and
    that page has to actually contain the value the answer gives.
    """
    conn = db.connect(config.database_url)
    row = conn.execute(
        "SELECT id, quarter, department, report_id, answers"
        " FROM submissions WHERE report_id = %s ORDER BY submitted_at DESC LIMIT 1",
        (args.report_id,),
    ).fetchone()
    if row is None:
        print(f"FAIL — no submission was filed for report ID {args.report_id}.")
        return 1

    corpus = {
        (filename, page): text for filename, page, text in conn.execute(_CORPUS_PAGES).fetchall()
    }
    if not corpus:
        print("Corpus is empty, so a citation cannot be checked. Run `deskwork ingest` first.")
        return 2

    submission_id, quarter, department, report_id, answers = row
    print(f"submission QIR-{submission_id:05d}: {quarter} / {department} / {report_id}")

    ok = True
    if (quarter, department) != (args.quarter, args.department):
        ok = False
        print(f"  [XX] expected {args.quarter} / {args.department}")

    report = grading.grade(answers, corpus)
    for check in report.checks:
        print(f"  [{'ok' if check.ok else 'XX'}] {check.key}: {check.detail}")
    ok = ok and report.ok

    print("PASS — report filed correctly." if ok else "FAIL — report filed with wrong answers.")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="deskwork", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ingest", help="embed the corpus into Postgres")

    runner = sub.add_parser("run", help="run the agent against the portal")
    runner.add_argument("--quarter", default="Q3 2025")
    runner.add_argument("--department", default="Pharmacy")
    runner.add_argument("--report-id", dest="report_id", default="QI-2025-014")
    runner.add_argument("--display", default=":99")
    runner.add_argument("--transcript", default=None, help="write a JSON transcript here")

    verifier = sub.add_parser(
        "verify", help="check the filed submission against the corpus ground truth"
    )
    verifier.add_argument("--quarter", default="Q3 2025")
    verifier.add_argument("--department", default="Pharmacy")
    verifier.add_argument("--report-id", dest="report_id", default="QI-2025-014")

    args = parser.parse_args(argv)
    config = Config.from_env()

    if args.command == "ingest":
        return cmd_ingest(config)
    if args.command == "run":
        return cmd_run(config, args)
    return cmd_verify(config, args)


if __name__ == "__main__":
    raise SystemExit(main())

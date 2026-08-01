"""Command line entry point: `deskwork ingest | run | verify`."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import db
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


# What a correct run must end up having entered. Substring match, case-insensitive, so a
# reasonable paraphrase still counts but an invented answer does not.
EXPECTED = {
    "ncpdp_field": "460",
    "effective_date": "march 24, 2020",
    "compliance_date": "september 21, 2020",
}


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
        Path(args.transcript).write_text(
            json.dumps(
                [
                    {"index": s.index, "text": s.text, "tools": [n for n, _ in s.tool_calls]}
                    for s in result.steps
                ],
                indent=2,
            )
        )
        print(f"\ntranscript written to {args.transcript}")
    return 0


def cmd_verify(config: Config) -> int:
    """Did the run actually file a correct report?

    This is what turns 'does it work?' into a number. It reads the row the portal wrote,
    not the agent's own account of what it did — an agent claiming success is not evidence.
    """
    conn = db.connect(config.database_url)
    row = conn.execute(
        "SELECT id, quarter, department, report_id, answers"
        " FROM submissions ORDER BY submitted_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        print("FAIL — no submission was filed.")
        return 1

    submission_id, quarter, department, report_id, answers = row
    print(f"submission QIR-{submission_id:05d}: {quarter} / {department} / {report_id}")

    ok = True
    for key, expected in EXPECTED.items():
        actual = str(answers.get(key, ""))
        hit = expected in actual.lower()
        ok = ok and hit
        print(f"  [{'ok' if hit else 'XX'}] {key}: {actual[:70]!r}")
        if not hit:
            print(f"        expected to contain {expected!r}")

    citation = str(answers.get("citation", ""))
    cited = ".pdf" in citation.lower()
    print(f"  [{'ok' if cited else 'XX'}] citation: {citation[:70]!r}")
    ok = ok and cited

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

    sub.add_parser("verify", help="check the filed submission against the corpus ground truth")

    args = parser.parse_args(argv)
    config = Config.from_env()

    if args.command == "ingest":
        return cmd_ingest(config)
    if args.command == "run":
        return cmd_run(config, args)
    return cmd_verify(config)


if __name__ == "__main__":
    raise SystemExit(main())

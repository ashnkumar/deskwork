# deskwork

An agent that reads the rulebook, then fills in the form.

[![ci](https://github.com/ashnkumar/deskwork/actions/workflows/ci.yml/badge.svg)](https://github.com/ashnkumar/deskwork/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.11+-blue)](https://www.python.org/downloads/)
[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

![An agent filing a quarterly compliance report in a browser: it reads four regulatory questions off the form, searches a corpus of federal PDFs, types each retrieved answer with its citation, and submits.](docs/demo.gif)

A real run, sped up. The agent has never seen this form. It clicks through to the
attestations, finds four questions it cannot answer from the task prompt, searches the corpus,
and types back what it found — including the filename and page number it came from. Eleven
runs out of eleven filed a correct report; the sample is small and the caveat is in
[Limitations](#limitations).

## Quickstart

```bash
git clone https://github.com/ashnkumar/deskwork && cd deskwork
cp .env.example .env          # then put your key in ANTHROPIC_API_KEY

docker compose up -d --build  # ~2 min, plus however long the base images take to pull

docker compose exec agent deskwork ingest   # 173 chunks from 3 PDFs, a few seconds
docker compose exec agent deskwork run      # ~2 minutes
docker compose exec agent deskwork verify   # did it actually file a correct report?
```

One Anthropic API key is the only credential. No AWS account, no hosted vector database, no
second key for embeddings — those run locally on CPU, and the model is baked into the image
so the first run does not download anything.

While `run` is going, open **<http://localhost:6080/vnc.html>** to watch it work. The portal
is on <http://localhost:8000> if you want to fill the form in yourself and see what the agent
is up against.

## What you get

A task prompt goes in. A row in the `submissions` table comes out, holding four regulatory
facts the agent looked up rather than recalled, each carrying the document and page it came
from.

| | Retrieval on its own | Computer use on its own | deskwork |
|---|---|---|---|
| **The answer** | Correct, and sitting in a chat window | Typed confidently into the right field, and sometimes invented | Retrieved, then typed into the right field |
| **Getting it into the system of record** | You do it | It does it | It does it |
| **Provenance** | You can cite it if you scroll up | None — the model cannot tell you where a remembered fact came from | Filename and page, in the form, because that is what the tool returned |
| **Knowing it worked** | You read it | The agent says it worked | `deskwork verify` reads the row back and grades it |

The pattern is not about healthcare. It applies wherever the authoritative source is a
document corpus and the system of record is software with no usable API. Healthcare
compliance is the example because the source documents are genuinely public.

## How it works

![Three panels. One: the task prompt and a three-PDF corpus, with the note that the agent has never seen the form. Two: at step 9 the attestation questions appear on screen, and at step 10 the agent passes that question verbatim to search_regulations. Three: the retrieved passage, the text typed into the form with its citation, and the grader's verdict.](docs/how-it-works.png)

The agent has exactly two tools. `computer` takes screenshots and drives mouse and keyboard.
`search_regulations` embeds a question and returns the nearest passages with their filename
and page number.

A run is 22 or 23 steps and about two minutes: screenshot, click through step 1, type the
quarter and report ID, reach the attestations, search the corpus twice, type four answers,
submit, read the reference number off the confirmation page.

### Architecture

![Seven numbered layers: the command line, the agent container holding the loop and its two tools, the FastAPI portal with its three form steps, Postgres with pgvector holding documents, chunks and submissions, and the grader reading the submissions row.](docs/architecture.png)

| # | Component | Module | What it does |
|---|---|---|---|
| **1** | Command line | `__main__.py` | `ingest`, `run`, `verify`. Nothing else |
| **2** | The loop | `agent.py` | Send, run the tool, append the result, repeat. Prunes old screenshots so a long run does not exhaust the context |
| **3** | Computer tool | `tools/computer.py` | `computer_20251124` against Xvfb via `xdotool` and `scrot` |
| **4** | Retrieval tool | `tools/search.py` | One embedding call, one SQL query, passages with provenance |
| **5** | The portal | `portal/app.py` | Three-step form with server-side validation. The target |
| **6** | Store | `db.py`, `ingest.py` | Postgres + pgvector. HNSW over 384-dim vectors |
| **7** | The grader | `verify` in `__main__.py` | Reads the `submissions` row and grades it against the PDFs |

The display is sized at exactly the geometry reported to the API, so screenshots are never
rescaled and coordinates map 1:1. The implementation this replaces carried a
`scale_coordinates()` helper converting between a Retina Mac and a 1024×768 target in both
directions, which is a permanent source of off-by-a-scale-factor bugs. A screenshot whose
dimensions disagree with the declared geometry is a hard error here.

Start with `src/deskwork/prompts.py`. It is the shortest file that matters.

## Retrieval is a tool, not context

Almost every retrieval system loads its context up front: search, prepend, then let the model
work. When you know the question in advance that is the right design — simpler, and one round
trip instead of two.

It cannot work here, and the trace shows why. **The questions are on page two of the form.**
The agent reaches them at step 9, and at step 10 it says:

> Four regulatory questions. Let me look each up in the corpus rather than relying on memory.

then passes that question — the one it read off the screen a step earlier — to
`search_regulations` almost verbatim. There is no earlier point at which that query could
have been written. A system that retrieved before the run would have been guessing what a
form it had never seen was about to ask.

Two rules in the system prompt hold this together. **Never state a regulatory fact from
memory**, and **never assume what is on screen**. Drop the first and the model types a
plausible rule identifier out of training data; the output is correctly formatted, wrong, and
indistinguishable from a right answer until someone gets audited. Drop the second and it
types into a field on a page that moved two steps ago.

**Neither rule is enforced.** They are instructions, and the model following them is
cooperation rather than a guarantee — nothing in the loop inspects a typed string to check it
came from a retrieved passage. That is why `deskwork verify` exists, and why it reads the
database instead of the agent's account of its own work. Fabrication is not prevented here.
It is caught afterwards, by something that had no hand in producing the answer.

`SPEC.md` has the data model, the chunking measurements, and what was rejected on the way.

## Commands

| Command | What it does |
|---|---|
| `deskwork ingest` | Chunk and embed the corpus. Re-running replaces a document cleanly |
| `deskwork run` | One task, start to finish. `--report-id`, `--quarter`, `--department` |
| `deskwork run --transcript run.json` | The same, plus every message and tool call written out |
| `deskwork verify --report-id QI-2025-014` | Grade one filed report. Exit 0 or 1 |

Every setting is an environment variable and `.env.example` lists them with defaults. The ones
worth changing are `DESKWORK_MODEL` (`claude-opus-5`), `DESKWORK_EFFORT` (`high`),
`DESKWORK_MAX_STEPS` (`40`) and `DESKWORK_MAX_IMAGES` (`6` — screenshots dominate the token
bill).

## Tests

```bash
uv run pytest           # 141 tests, no API key, no network
uv run pytest -m live   # the tier that spends money
```

- The **computer tool** is asserted against a recording runner — the exact `xdotool`
  invocations — and then driven against a real Xvfb, typing into a real `xterm` and reading
  the text back.
- The **loop** runs against a fake client replaying recorded turns, which is where the subtle
  bugs live: a dropped `tool_result`, an assistant turn not echoed back verbatim, a pruned
  thinking block, a run that never terminates.
- **Retrieval quality is a test.** `test_corpus_eval` asserts that every fact the demo depends
  on is retrievable, and `test_chunk_size_is_tuned` pins the chunk size, because 1100
  characters answered three of five questions and 500 answers five.

Tests that need a display skip without one, so the number above is what gets collected rather
than what passes on your machine.

## Limitations

- **Eleven out of eleven is a small sample.** Every graded run so far filed a correct report
  in 22 or 23 steps, but eleven trials cannot distinguish a reliable agent from a lucky one —
  the true rate could be as low as **76%** and this measurement would not know. One task, one
  form, one machine.
- **A failed run leaves a partial report behind.** There is no cleanup and no resume. The next
  run starts a new draft rather than finishing the abandoned one.
- **Nothing stops a fabricated answer being typed.** The grader catches it afterwards.
  Anything unattended would need that check in front of the write, not behind it.
- **The portal ships with this repo.** It has server-side validation, format rules, and errors
  that appear only on screen, so the agent cannot get through it without reading the page —
  but we wrote it, and a target you control is easier than one you do not.
- **The corpus is three documents**, enough to make retrieval meaningful and a wrong answer
  detectable, not enough to say anything about retrieval at scale. It is also trusted input:
  passages reach the model as authoritative, so pointing this at a corpus you do not control
  is a prompt-injection boundary, and it is not defended.
- **Computer use is a beta API and it misclicks.** That is the state of the art, and part of
  why the step budget and the grader exist.

Also: the portal has no authentication, ownership, or CSRF protection, which is why compose
binds it and the noVNC desktop to `127.0.0.1` only — `x11vnc` runs with `-nopw`, so port 6080
is an unauthenticated desktop in a container holding your API key. Drafts live in memory in
one process. Not medical or legal advice.

## Corpus

Three US federal publications on HIPAA Administrative Simplification — public domain, checked
for patient-identifiable content before inclusion. Provenance and retrieval dates are in
[`corpus/SOURCES.md`](corpus/SOURCES.md).

## License

MIT — see [LICENSE](LICENSE).

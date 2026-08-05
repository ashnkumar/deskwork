# deskwork

Claude's computer use needs a search tool, not a bigger prompt.

[![ci](https://github.com/ashnkumar/deskwork/actions/workflows/ci.yml/badge.svg)](https://github.com/ashnkumar/deskwork/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.11+-blue)](https://www.python.org/downloads/)
[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

![An agent filing a quarterly compliance report in a browser: it reads four regulatory questions off the form, searches a corpus of federal PDFs, types each retrieved answer with its citation, and submits.](docs/demo.gif)

A real run, sped up. Three screens into a form it has never seen, the agent hits four
regulatory questions. It searches a corpus of federal PDFs, types back what it found with the
filename and page number, and submits.

## The pattern

Computer use lets Claude operate software nobody gave it an API to. That moves where its
information needs come from. The agent finds out what it has to know by *using* the software
— several steps in, off a screen you did not write and could not have predicted.

Retrieval that runs before the loop cannot help with that. The usual shape — take the
question, embed it, fetch the top passages, paste them into the prompt, one completion —
assumes you are holding the question when the run starts. Here the question is behind a
Continue button.

So retrieval goes in the `tools` array next to `computer`, and the model reaches for it when
the screen gives it a reason to. Both tools are in the same request. There is no router.

The other half of why this matters is what computer use *does*. A chat model that invents a
regulation number says a wrong thing to a person who can push back. A computer-use agent
types it into a system of record, correctly formatted, where it stays until an audit. Taking
the human out from in front of the write is what raises the stakes on grounding.

deskwork is one worked example: an agent that files a quarterly healthcare compliance report
by looking up the regulations the form asks about. The domain is incidental —
[Applying this elsewhere](#applying-this-elsewhere) is the part to take.

## Quickstart

```bash
git clone https://github.com/ashnkumar/deskwork && cd deskwork
cp .env.example .env          # then put your key in ANTHROPIC_API_KEY

docker compose up -d --build  # ~2 min, plus however long the base images take to pull

docker compose exec agent deskwork ingest   # 173 chunks from 3 PDFs, a few seconds
docker compose exec agent deskwork run      # ~2 minutes
docker compose exec agent deskwork verify   # did it file a correct report?
```

An Anthropic API key is the only credential. Chunks are embedded on CPU with
`BAAI/bge-small-en-v1.5` and stored in Postgres with pgvector — a normal vector index, just a
local one, so there is no second key and no hosted service to sign up for. The embedding
model is baked into the image, so the first run downloads nothing.

While `run` is going, open **<http://localhost:6080/vnc.html>** to watch it work. The portal
is on <http://localhost:8000> if you want to fill the form in yourself and see what the agent
is up against.

## What comes out

A task prompt goes in. A row in the `submissions` table comes out, holding four regulatory
facts the agent looked up rather than recalled, each with the document and page it came from.

| | Retrieval on its own | Computer use on its own | Both, as tools |
|---|---|---|---|
| **The answer** | Correct, and sitting in a chat window | Typed into the right field, and sometimes invented | Retrieved, then typed into the right field |
| **Getting it into the system of record** | You do it | It does it | It does it |
| **Provenance** | You can cite it if you scroll up | None — the model cannot tell you where a remembered fact came from | Filename and page, in the form, because that is what the tool returned |
| **Knowing it worked** | You read it | The agent says it worked | `deskwork verify` reads the row back and grades it |

## The trace

![Three panels. One: the task prompt and a three-PDF corpus, with the note that the agent has never seen the form. Two: at step 9 the attestation questions appear on screen, and at step 10 the agent passes that question verbatim to search_regulations. Three: the retrieved passage, the text typed into the form with its citation, and the grader's verdict.](docs/how-it-works.png)

The agent has two tools. `computer` takes screenshots and drives mouse and keyboard.
`search_regulations` embeds a question and returns the nearest passages with their filename
and page number.

A run is 22 or 23 steps and about two minutes: screenshot, click through step 1, type the
quarter and report ID, reach the attestations, search the corpus, type four answers, submit,
read the reference number off the confirmation page.

**The questions are on page two of the form.** The agent reaches them at step 9, and at step
10 it says:

> Four regulatory questions. Let me look each up in the corpus rather than relying on memory.

then passes that question — the one it read off the screen a step earlier — to
`search_regulations` almost verbatim. There is no earlier point at which that query could
have been written. Anything that retrieved before the run would have been guessing what a
form it had never seen was about to ask.

Two things in that trace are the model's, not ours. The **query text**, which it lifted off a
screen we could not predict. And the **number of searches**: four questions, two calls.
Nothing told it to batch them.

**What is ours is the policy.** `prompts.py` instructs it to search — *"You may well believe
you already know the answer. Search anyway"* — and the task prompt repeats it. So this is not
a model spontaneously inventing retrieval. It is a model given a rule about where facts may
come from, in a situation where only it can supply the query.

**The demo questions were written from the corpus, so every one of them is answerable.** Open
[`src/portal/app.py`](src/portal/app.py) and you will find the four attestations hard-coded,
one asking outright for a document and page number — exactly what the retrieval tool returns.
That fixes what is findable. It does nothing about *when* the agent finds out what it is being
asked, and nothing about whether it looks. Claude almost certainly has CMS-0055-F somewhere in
training; it could have typed an answer at step 10 and been right. It searched instead.

## Architecture

![Seven numbered layers: the command line, the agent container holding the loop and its two tools, the FastAPI portal with its three form steps, Postgres with pgvector holding documents, chunks and submissions, and the grader reading the submissions row.](docs/architecture.png)

| # | Component | Module | What it does |
|---|---|---|---|
| **1** | Command line | `__main__.py` | `ingest`, `run`, `verify`. Nothing else |
| **2** | The loop | `agent.py` | Send, run the tool, append the result, repeat. Swaps old screenshots for a text note so a long run does not exhaust the context |
| **3** | Computer tool | `tools/computer.py` | `computer_20251124` against Xvfb via `xdotool` and `scrot` |
| **4** | Retrieval tool | `tools/search.py` | One embedding call, one SQL query, passages with provenance |
| **5** | The portal | `portal/app.py` | Three-step form with server-side validation. The target |
| **6** | Store | `db.py`, `ingest.py` | Postgres + pgvector. HNSW over 384-dim vectors |
| **7** | The grader | `verify` in `__main__.py` | Reads the `submissions` row and grades it against the PDFs |

The request is `client.beta.messages.create` with `betas=["computer-use-2025-11-24"]`, model
`claude-opus-5`, adaptive thinking, and both tools in one array.

The API also ships a feature that does the screenshot pruning for you: context editing
(`clear_tool_uses_20250919`, behind the `context-management-2025-06-27` beta) clears old tool
results server-side, as one request parameter. If you are building a loop rather than reading
one, use it. This repo keeps ~30 lines of its own so the pruning is visible, and because the
screenshot becomes a short text note *inside* the tool result rather than the result
disappearing.

## Applying this elsewhere

Four pieces transfer.

**Two tools in one request.** `computer` is Anthropic's, declared by type.
`search_regulations` is an ordinary custom tool — a JSON schema and a Python function. They go
in the same array and the model arbitrates. `agent.py` is the whole loop and there is no
routing logic in it.

**A system prompt that separates knowing from looking up.** `prompts.py` is the shortest file
in the repo that changes the outcome. Two rules carry it: **Never state a regulatory fact
from memory**, and **never assume what is on screen**. Drop the first and the model types a
plausible rule identifier out of training data — correctly formatted, wrong, and
indistinguishable from a right answer until someone gets audited. Drop the second and it types
into a field on a page that moved two steps ago. Both transfer to any corpus.

**Neither rule is enforced.** They are instructions, and the model following them is
cooperation rather than a guarantee — nothing in the loop inspects a typed string to check it
came from a retrieved passage. So the third piece is **a check that does not ask the agent**:
`deskwork verify` reads the row that was written and grades it against the source documents.
Fabrication is not prevented here. It is caught afterwards, by something that had no hand in
producing the answer.

**Display geometry that matches what you declare.** The X display is sized to exactly the
resolution reported to the API, so coordinates map 1:1 and a screenshot whose dimensions
disagree is a hard error. A helper that rescales between a Retina display and the target is a
permanent source of off-by-a-scale-factor bugs; not having one is cheaper than getting it right.

To point this at something else: replace the PDFs in `corpus/` and re-run `ingest`, change the
target from the bundled portal to your own software, and rewrite the task prompt. The grader
is the piece with no generic version — it is written against this form's four questions.

`SPEC.md` has the data model, the chunking measurements, and what was rejected on the way.

## Commands

| Command | What it does |
|---|---|
| `deskwork ingest` | Chunk and embed the corpus. Re-running replaces a document cleanly |
| `deskwork run` | One task, start to finish. `--report-id`, `--quarter`, `--department` |
| `deskwork run --transcript run.json` | The same, plus every message and tool call written out |
| `deskwork verify --report-id QI-2025-014` | Grade one filed report. Exit 0 or 1 |

Every setting is an environment variable; `.env.example` lists them with defaults. Worth
changing: `DESKWORK_MODEL` (`claude-opus-5`), `DESKWORK_EFFORT` (`high`), `DESKWORK_MAX_STEPS`
(`40`) and `DESKWORK_MAX_IMAGES` (`6` — screenshots dominate the token bill).

## Tests

```bash
uv run pytest           # 141 tests, no API key, no network
uv run pytest -m live   # the tier that spends money
```

- The **computer tool** is asserted against a recording runner — the exact `xdotool`
  invocations — then driven against a real Xvfb, typing into an `xterm` and reading it back.
- The **loop** runs against a fake client replaying recorded turns, which is where the subtle
  bugs live: a dropped `tool_result`, an assistant turn not echoed back verbatim, a pruned
  thinking block, a run that never terminates.
- **Retrieval quality is a test.** `test_corpus_eval` asserts every fact the demo depends on is
  retrievable, and `test_chunk_size_is_tuned` pins the chunk size, because 1100 characters
  answered three of five questions and 500 answers five.

Tests needing a display skip without one, so that count is what gets collected, not what passes.

## Limitations

- **Eleven out of eleven is a small sample.** Every graded run so far filed a correct report
  in 22 or 23 steps, but eleven trials cannot distinguish a reliable agent from a lucky one —
  the true rate could be as low as **76%** and this measurement would not know. One task, one
  form, one machine.
- **A failed run leaves a partial report behind.** There is no cleanup and no resume. The next
  run starts a new draft rather than finishing the abandoned one.
- **The portal ships with this repo, and so do its questions** — see [The trace](#the-trace).
  A real form would ask things the corpus does not cover, and the honest failure mode there is
  a confident wrong answer.
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

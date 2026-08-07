# deskwork

Grounded computer use — Claude looks it up, then types it.

[![ci](https://github.com/ashnkumar/deskwork/actions/workflows/ci.yml/badge.svg)](https://github.com/ashnkumar/deskwork/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.11+-blue)](https://www.python.org/downloads/)
[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

![An agent filing a quarterly compliance report in a browser: it reads four regulatory questions off the form, searches a corpus of federal PDFs, types each retrieved answer with its citation, and submits.](docs/demo.gif)

A real run, sped up. Three screens into a form it has never seen, the agent hits four
regulatory questions. It searches a corpus of federal PDFs, types back what it found with the
filename and page number, and submits.

## The problem

You want an agent to operate software you have no API for, and the software asks it questions
it has to be right about. A quarterly compliance report wants to know which field identifies a
partial fill for a Schedule II drug, and the date by which compliance is required. A model
answering from memory produces something correctly formatted and plausible, and some of it is
wrong. Nobody catches it on screen. A chat model that invents a regulation number says a wrong
thing to a person who can push back; this one files it into a system of record, where it stays
until an audit.

The standard answer is retrieval: look the facts up first, put the passages in the prompt, and
have the model answer out of them. That works when you are holding the question at the start of
the run, and here you are not — the agent finds out what it has to know by using the software,
several screens in, off a page you did not write. In this demo the questions are on page two of
a three-step form. **deskwork gives the agent a way to look things up in the middle of a run,
and a rule that regulatory facts may come only from what it looked up.**

The cost is a round trip per lookup, and a rule that is an instruction rather than a guarantee,
since nothing stops the agent typing something it remembers. So the last piece is a check that
reads the filed report and grades it against the source documents without asking the agent
anything.

The rest of this page is how that works and how to run it.

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
local one, so there is no second key and no hosted service to sign up for. The embedding model
is baked into the image, so the first run downloads nothing.

While `run` is going, open **<http://localhost:6080/vnc.html>** to watch it work. The portal is
on <http://localhost:8000> if you want to fill the form in yourself and see what the agent is
up against.

## What you get

A task prompt goes in. A row in the `submissions` table comes out, holding four regulatory
facts the agent looked up rather than recalled, each with the document and page it came from.

| | Retrieval on its own | Computer use on its own | Both, as tools |
|---|---|---|---|
| **The answer** | Correct, and sitting in a chat window | Typed into the right field, and sometimes invented | Retrieved, then typed into the right field |
| **Getting it into the system of record** | You do it | It does it | It does it |
| **Provenance** | You can cite it if you scroll up | None — the model cannot tell you where a remembered fact came from | Filename and page, in the form, because that is what the tool returned |
| **Knowing it worked** | You read it | The agent says it worked | `deskwork verify` reads the row back and grades it |

**The obvious alternative is to skip retrieval and paste the corpus into the prompt**, and for
a corpus this size that is the right call. All three PDFs extract to 58,000 characters
together, which fits in a modern context window with room to spare, and a model reading the
whole thing will answer these four questions. What it will not do is tell you which page it
read, and the page number is the part that gets typed into the form. The trade is a round trip
per lookup in exchange for provenance that survives into the filed report, plus a corpus that
can grow past the window without the design changing.

## How it works

![Three panels. One: the task prompt and a three-PDF corpus, with the note that the agent has never seen the form. Two: at step 9 the attestation questions appear on screen, and at step 10 the agent passes that question verbatim to search_regulations. Three: the retrieved passage, the text typed into the form with its citation, and the grader's verdict.](docs/how-it-works.png)

The agent has two tools, and they go out in the same request. `computer` takes screenshots and
drives mouse and keyboard. `search_regulations` embeds a question and returns the nearest
passages with their filename and page number. There is no router: the model decides which one
it needs, which is the only workable arrangement when the reason to search is a sentence that
has not been rendered yet.

A run is 22 or 23 steps and about two minutes: screenshot, click through step 1, type the
quarter and report ID, reach the attestations, search the corpus, type four answers, submit,
read the reference number off the confirmation page.

**The questions are on page two of the form.** The agent reaches them at step 9, and at step 10
it says:

> Four regulatory questions. Let me look each up in the corpus rather than relying on memory.

then passes that question — the one it read off the screen a step earlier — to
`search_regulations` almost verbatim. There is no earlier point at which that query could have
been written. Anything that retrieved before the run would have been guessing what a form it
had never seen was about to ask.

Two things in that trace are the model's, not ours. The **query text**, which it lifted off a
screen we could not predict. And the **number of searches**: four questions, two calls. Nothing
told it to batch them.

**The demo questions were written from the corpus, so every one of them is answerable.** Open
[`src/portal/app.py`](src/portal/app.py) and you will find the four attestations hard-coded, one
asking outright for a document and page number — exactly what the retrieval tool returns. That
fixes what is findable. It does nothing about *when* the agent finds out what it is being
asked, and nothing about whether it looks. Claude almost certainly has CMS-0055-F somewhere in
training; it could have typed an answer at step 10 and been right. It searched instead.

### Architecture

![Seven numbered layers: the command line, the agent container holding the loop and its two tools, the FastAPI portal with its three form steps, Postgres with pgvector holding documents, chunks and submissions, and the grader reading the submissions row.](docs/architecture.png)

| # | Component | Module | What it does |
|---|---|---|---|
| **1** | Command line | `__main__.py` | `ingest`, `run`, `verify`. Nothing else |
| **2** | The loop | `agent.py` | Send, run the tool, append the result, repeat. Swaps old screenshots for a text note so a long run does not exhaust the context |
| **3** | Computer tool | `tools/computer.py` | `computer_20251124` against Xvfb via `xdotool` and `scrot` |
| **4** | Retrieval tool | `tools/search.py` | One embedding call, one SQL query, passages with provenance |
| **5** | The portal | `portal/app.py` | Three-step form with server-side validation. The target |
| **6** | Store | `db.py`, `ingest.py` | Postgres + pgvector. HNSW over 384-dim vectors, 500-character chunks |
| **7** | The grader | `verify` in `__main__.py` | Reads the `submissions` row and grades it against the PDFs |

The X display is created at exactly the resolution reported to the API, so coordinates map 1:1
and a screenshot whose dimensions disagree is a hard error rather than a drifting misclick. The
API will downscale an oversized screenshot for you and return coordinates in that smaller
space, so the alternative is a rescaling helper in both directions and a scale factor to be
wrong about.

The API also ships the screenshot pruning: context editing (`clear_tool_uses_20250919`, behind
the `context-management-2025-06-27` beta) clears old tool results server-side, as one request
parameter. If you are building a loop rather than reading one, use it. This repo keeps ~30
lines of its own so the pruning is visible, and because the screenshot becomes a short text
note *inside* the tool result rather than the result disappearing.

## The rule that isn't enforced

`prompts.py` is the shortest file in the repo that changes the outcome. Two rules carry it:
**Never state a regulatory fact from memory**, and **never assume what is on screen**. Drop the
first and the model types a plausible rule identifier out of training data — correctly
formatted, wrong, and indistinguishable from a right answer until someone gets audited. Drop
the second and it types into a field on a page that moved two steps ago.

**Neither rule is enforced.** They are instructions, and the model following them is
cooperation rather than a guarantee — nothing in the loop inspects a typed string to check it
came from a retrieved passage, and by the time the text reaches `xdotool` it is characters. So
the check that closes the loop is one that does not ask the agent: `deskwork verify` reads the
row that was written and grades it against the source documents. Fabrication is not prevented
here. It is caught afterward, by something that had no hand in producing the answer.

That split is the part to take somewhere else. You own the policy about where facts may come
from; the model owns the query, because only it can see what is being asked; and the grader
owns the verdict, because neither of you should. To point this at something else, replace the
PDFs in `corpus/` and re-run `ingest`, change the target from the bundled portal to your own
software, and rewrite the task prompt. The grader is the piece with no generic version — it is
written against this form's four questions.

`SPEC.md` has the data model, the chunking measurements, and what was rejected on the way.

## Commands

| Command | What it does |
|---|---|
| `deskwork ingest` | Chunk and embed the corpus. Re-running replaces a document cleanly |
| `deskwork run` | One task, start to finish. `--report-id`, `--quarter`, `--department` |
| `deskwork run --transcript run.json` | The same, plus every message and tool call written out |
| `deskwork verify --report-id QI-2025-014` | Grade one filed report. Exit 0 or 1 |

Every setting is an environment variable; `.env.example` lists them with defaults. Worth
changing: `DESKWORK_MODEL` (`claude-opus-5`), `DESKWORK_EFFORT` (`high`, which is also the
API's default, so the useful direction is down), `DESKWORK_MAX_STEPS` (`40`) and
`DESKWORK_MAX_IMAGES` (`6` — screenshots dominate the token bill).

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
  retrievable, and `test_chunk_size_is_tuned` re-ingests the corpus at two sizes and asserts
  the configured one wins, because 1100-character chunks answered three of five questions and
  500 answers five.

Tests needing a display skip without one, so that count is what gets collected, not what passes.

## Limitations

- **Eleven out of eleven is a small sample.** Every graded run so far filed a correct report in
  22 or 23 steps, but eleven trials cannot distinguish a reliable agent from a lucky one — the
  true rate could be as low as **76%** and this measurement would not know. One task, one form,
  one machine.
- **The portal ships with this repo, and so do its questions** — see
  [How it works](#how-it-works). A real form would ask things the corpus does not cover, and
  the honest failure mode there is a confident wrong answer.
- **Retrieved passages are trusted input.** They reach the model as authoritative, so pointing
  this at a corpus you do not control is a prompt-injection boundary and it is not defended
  here. The API does more than this repo does: Anthropic runs classifiers over computer-use
  screenshots and steers the model to ask for confirmation when one trips. Nothing equivalent
  runs over a passage that arrives from your own database.

`SPEC.md` has the rest: §9 covers what is deliberately out of scope, including that the portal
has no authentication, CSRF protection or draft ownership and that `x11vnc` runs with `-nopw`,
which is why compose binds the portal and the noVNC desktop to `127.0.0.1` and why they should
stay there. §7 covers what a failed run leaves behind — there is no cleanup and no resume, so
the next run starts a new draft rather than finishing the abandoned one. Computer use is also a
beta API and it misclicks, which is part of why the step budget and the grader exist.

## Corpus

Three US federal publications on HIPAA Administrative Simplification — public domain, checked
for patient-identifiable content before inclusion. Provenance and retrieval dates are in
[`corpus/SOURCES.md`](corpus/SOURCES.md).

## License

MIT — see [LICENSE](LICENSE).

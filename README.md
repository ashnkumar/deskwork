# deskwork

An agent that completes multi-step administrative work by combining **retrieval over a
regulatory corpus** with **computer use** to operate the software the work lives in.

The demo task: filing a Quarterly Quality Improvement report in a compliance portal. Some
fields can be read off the screen. Others are regulatory facts that exist only in a corpus
of federal publications. The agent has to do both, in a real browser, in the right order.

> TODO — screenshot / GIF of a real run, generated from the container once run reliability
> has been measured over enough runs to quote an honest number.

## Why this combination

Retrieval alone gives you an answer that a person then has to go and type somewhere.

Computer use alone gives you a clicker that will confidently type a plausible-looking
regulation number it invented. The output is correctly formatted, entirely wrong, and
indistinguishable from a right answer until someone gets audited.

Together you get something that can find the right regulation *and* put it where it needs
to go. That is the whole claim, and it is what the code is arranged to demonstrate.

The pattern is not really about healthcare. It applies wherever the authoritative source of
truth is a document corpus and the system of record is software with no usable API.
Healthcare compliance is the example here because the source documents are genuinely public.

## Quickstart

You need Docker and one Anthropic API key. Nothing else — no AWS account, no hosted
database, no second API key for embeddings.

```bash
git clone <repo> && cd deskwork
cp .env.example .env          # then put your key in ANTHROPIC_API_KEY

docker compose up -d --build  # ~3 min the first time, mostly the browser image

docker compose exec agent deskwork ingest   # embed the corpus (~70 chunks, a few seconds)
docker compose exec agent deskwork run      # watch it work
docker compose exec agent deskwork verify   # did it actually file a correct report?
```

While `run` is going, open **<http://localhost:6080/vnc.html>** to watch the agent drive
the browser. That view is most of the point — an agent working is considerably more
convincing than a log saying it worked.

The portal is also on <http://localhost:8000> if you want to click through it yourself and
see what the agent is up against.

## What is actually happening

```
                    ┌──────────────────────────────────────────┐
                    │  agent  (Xvfb + Firefox + the loop)      │
  ANTHROPIC_API_KEY │                                          │
  ─────────────────▶│   agent loop ──▶ computer tool ──▶ X11   │──▶ noVNC :6080
                    │        │                                 │
                    │        └───────▶ search_regulations ─┐   │
                    └──────────────────────────────────────┼───┘
                                                           ▼
                     ┌──────────────────┐      ┌────────────────────────┐
                     │ portal (FastAPI) │─────▶│ db (postgres+pgvector) │
                     │ the QI portal    │      │ chunks + submissions   │
                     └──────────────────┘      └────────────────────────┘
```

The agent has exactly two tools. `computer` (`computer_20251124`) takes screenshots and
drives mouse and keyboard. `search_regulations` returns verbatim passages from the corpus
with a filename and page number.

A run looks roughly like: screenshot the portal → click through to the form → read the
attestation question off the screen → search the corpus for it → type the retrieved answer
and its citation → submit → read the confirmation reference.

## The parts worth stealing

### The system prompt is the load-bearing file

[`src/deskwork/prompts.py`](src/deskwork/prompts.py) draws a hard line between *knowing* and
*looking up*: regulatory facts come from `search_regulations` and never from memory;
on-screen state comes from a screenshot and is never assumed.

Both halves matter. Without the first, the model invents rule identifiers. Without the
second, it types into a field it has not looked at, on a page that moved on two steps ago.

Retrieval is a **tool**, not context stuffed into the prompt, so the agent retrieves when it
notices it needs to — having just read the question off the screen — rather than answering
questions it has not been asked yet.

### Size the display at the model's coordinate space

Xvfb runs at exactly the `display_width_px`/`display_height_px` reported to the API.
Screenshots are never rescaled and coordinates map 1:1.

The implementation this replaces carried a `scale_coordinates()` helper converting between a
Retina Mac and a 1024×768 target in both directions, which is a permanent source of
off-by-a-scale-factor bugs. Sizing the display correctly deletes the problem rather than
managing it. A screenshot whose dimensions disagree with the declared geometry is a hard
error here, because the alternative is silently clicking in the wrong place.

### Both halves are testable without spending a cent

```bash
uv run pytest        # 89 tests, no API key, no network
```

- The **computer tool** is asserted against a recording runner (exact `xdotool`
  invocations) and driven against a real Xvfb, typing into a real `xterm` and reading the
  text back. No model involved.
- The **agent loop** runs against a fake client replaying recorded turns — which is where
  the subtle bugs live: dropping a `tool_result`, failing to echo an assistant turn back
  verbatim, pruning a thinking block, looping forever.
- **Retrieval** runs end to end against Postgres with local embeddings.

Live API tests sit behind `-m live` and are deselected by default.

### Success is a number, not a vibe

`deskwork verify` reads the row the portal wrote and grades it against ground truth read out
of the source PDFs by hand. An agent's own report that it succeeded is not evidence.

This matters more than it sounds. The risk in a project like this was never feasibility —
it was reliability, and the only way to talk about that honestly is to measure it.

### Retrieval quality is a test, not a guess

`tests/test_retrieval.py::test_corpus_eval` asserts that every fact the demo depends on is
actually retrievable. The first version of this repo shipped 1100-character chunks and
quietly answered 3 of 5 questions; 500-character chunks answer 5 of 5. The number in
`ingest.py` is a measurement, and `test_chunk_size_is_tuned` keeps it one.

## Configuration

Everything is environment variables; see [`.env.example`](.env.example) for the full list.
The ones you might actually change:

| Variable | Default | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | The only value you must supply |
| `DESKWORK_MODEL` | `claude-opus-5` | Any model supporting `computer_20251124` |
| `DESKWORK_EFFORT` | `high` | `low`–`max`. Lower is cheaper and less careful |
| `DESKWORK_MAX_STEPS` | `40` | Hard ceiling so a confused run cannot bill indefinitely |
| `DESKWORK_MAX_IMAGES` | `6` | Screenshots kept in context; they dominate token spend |

## Honest limitations

- **Run reliability is not yet characterised.** The end-to-end agent run has not been
  measured over enough runs to quote a success rate, so this README does not quote one.
  `deskwork verify` is how you find out for your own setup. This is the honest open
  question about the project, not a footnote.
- **The portal ships with this repo.** It is a real three-step form with server-side
  validation and errors that only appear on screen — the agent cannot get through it
  without reading the page. But we wrote it, and a target you control is easier than one
  you do not.
- **The corpus is three documents.** Enough to make retrieval meaningful and to make a
  wrong answer detectable. Not enough to say anything about retrieval at scale.
- **One task, one happy path.** This is a reference implementation of a pattern, not a
  general-purpose computer-use framework.
- **Computer use is a beta API** and it misclicks. That is the current state of the art,
  and part of why the step budget and the grader exist.
- **Not medical or legal advice.** It fills in a compliance form from public regulatory
  text.

## Corpus

Three US federal government publications on HIPAA Administrative Simplification — public
domain, verified free of patient-identifiable content before inclusion. Provenance and
retrieval dates are in [`corpus/SOURCES.md`](corpus/SOURCES.md).

## Design notes

[`SPEC.md`](SPEC.md) covers the architecture, the data model, and the reasoning behind the
choices — including what was rejected and why.

## Licence

TODO — MIT intended; `LICENSE` is prepared but not yet committed.

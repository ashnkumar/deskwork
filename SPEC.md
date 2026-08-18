# deskwork — specification

An agent that completes multi-step administrative work end to end by combining **retrieval
over a regulatory corpus** with **computer use** to operate the software the work lives in.

The demo task: filing a Quarterly Quality Improvement (QI) report in a compliance portal.
Some fields can be read off the screen. Others are regulatory facts that only exist in the
document corpus. The agent has to do both, in the right order, in a real browser.

---

## 1. The claim being demonstrated

Retrieval alone produces an answer that a human then has to go and type somewhere.
Computer use alone produces a clicker that will confidently type a plausible-sounding
regulation number it invented.

Together you get something that can find the right regulation *and* put it where it needs
to go. That combination is the entire point of this repository. Everything else is
scaffolding chosen to keep that pattern legible.

The pattern generalizes past healthcare — it applies to any domain where the authoritative
source of truth is a document corpus and the system of record is software the agent has to
operate rather than call. Healthcare compliance is the example because the corpus is
genuinely public.

## 2. Separating the good idea from the bad execution

This is a rebuild from a reference implementation, not a cleanup. The reference is
`ashnkumar/synapse_ai` (private, Nov 2024). Being explicit about what carried over:

### The idea, which is sound

- RAG-grounded computer use as a single agent loop, with retrieval and screen control as
  peer tools the model chooses between.
- Regulatory compliance paperwork as the demo domain — high real-world friction, and the
  source documents are public.
- Giving the model a retrieval tool rather than stuffing retrieved context into the prompt,
  so it retrieves *when it notices it needs to*, mid-task, rather than once up front.

### The execution, which isn't

Assessed at Phase 0. None of it carries over:

| Reference | Problem |
|---|---|
| `claude-3-5-sonnet-20241022` | Retired 28 Oct 2025. Returns HTTP 404. |
| `computer_20241022` | Rejected on every current model (HTTP 400). Current is `computer_20251124`. |
| `tools/computer.py` | Drives the developer's **macOS host** via `cliclick`, `screencapture`, `sips`, `pyautogui.size()`. Not reproducible, not containerisable, and it screenshots a real desktop. |
| `Dockerfile` | Installs Homebrew into `python:3.12-slim`, then `brew install cliclick` — a macOS-only binary. `ENTRYPOINT` runs `app.py`, which doesn't exist in the repo. This container has never built. |
| `loop.py` | Sends `"betas"` in the Bedrock `invoke_model` body (Bedrock expects `anthropic_beta`). Defines `_make_api_tool_result` twice; the first is dead. |
| `main.py` | Passes `APIProvider.ANTHROPIC`; the enum only defines `BEDROCK`. Raises `AttributeError` on the first request. The FastAPI path never ran. |
| `tools/rag/tool.py` | Imports 8 LangChain symbols and uses one method call: `similarity_search(query, k=3)`. Builds `self.chain` in `__init__` and never calls it. |
| `indexing/embeddings.py` | Imports `langchain_mongodb`, which is absent from `requirements.txt`. `ImportError` on a clean install. |
| `tests/` (13 files) | Zero `assert` statements, zero `pytest` imports. All 13 are manual `__main__` scripts. |
| `frontend/` | Empty directory. |
| `tools/base.py`, `collection.py`, `run.py` | Near-verbatim from Anthropic's `computer-use-demo` quickstart, not original work. |

The reference is a specification. It isn't a codebase, and this repo shares no code with it.

## 3. Architecture

3 services, one `docker compose up`, one Anthropic API key.

```
                    ┌──────────────────────────────────────────┐
                    │  agent  (Debian + Xvfb + Firefox)        │
                    │                                          │
  ANTHROPIC_API_KEY │   ┌────────────┐    ┌─────────────────┐  │
  ─────────────────▶│   │ agent loop │───▶│ computer tool   │──┼──▶ Xvfb :99
                    │   │            │    │ (xdotool/scrot) │  │    Firefox
                    │   │            │    └─────────────────┘  │      │
                    │   │            │    ┌─────────────────┐  │      │
                    │   │            │───▶│ search_         │  │      │
                    │   └────────────┘    │ regulations     │  │      │
                    │                     └────────┬────────┘  │      │
                    │   noVNC :6080 ─── watch it work          │      │
                    └────────────────────────────┬─────────────┘      │
                                                 │                    │
                          ┌──────────────────────▼──┐   ┌─────────────▼────┐
                          │  db (postgres+pgvector) │   │  portal (FastAPI)│
                          │  chunks + embeddings    │◀──│  the QI portal   │
                          │  submissions            │   │  server-rendered │
                          └─────────────────────────┘   └──────────────────┘
```

| Service | What it is | Why |
|---|---|---|
| `agent` | Debian, Xvfb virtual display, Fluxbox, Firefox, `xdotool`, `scrot`, noVNC, and the agent package | Mirrors Anthropic's own computer-use reference implementation, so the shape is familiar to anyone who has read it. noVNC means you can *watch* the run, which is most of the demo's value. |
| `portal` | FastAPI, server-rendered HTML, no JS framework | The software the work lives in. Server-rendered because a SPA adds load-timing flakiness that has nothing to do with the pattern being shown. |
| `db` | Postgres 17 + `pgvector` | Holds document chunks with embeddings, and the portal's submissions. One store, no second service. |

### Why not the reference's dependencies

- **Anthropic API, not AWS Bedrock.** Bedrock adds an AWS account, IAM, and per-model
  region access requests for zero gain. Going direct means a stranger needs one key.
  This wasn't a close call — see §2, the reference's model and tool version are both dead.
- **pgvector, not MongoDB Atlas.** Atlas free tier costs the reader a signup, a hosted
  cluster, a network allowlist, a database user, and a hand-authored vector index
  definition before anything runs. `pgvector` is one container and standard SQL. At a
  corpus of a few hundred chunks, nothing is lost.
- **No LangChain.** See §2. The retrieval logic is ~40 lines of SQL and one embedding call.
  A reader stealing from this repo needs to see the pattern, not the framework.

### Local embeddings

`fastembed` with `BAAI/bge-small-en-v1.5` (384-dim), baked into the image.

This is a constraint, not a preference: Anthropic has no embeddings endpoint, and using
Voyage would mean a **second API key**, breaking the one-key promise. Running the embedding
model locally keeps the deliverable honest, and has a real upside — the entire retrieval
path is deterministic and testable offline with no API credit.

## 4. Data model

```sql
documents (id, filename UNIQUE, title, sha256, ingested_at)
chunks    (id, document_id → documents, ordinal, page, text, embedding vector(384))
          ix: HNSW on embedding, vector_cosine_ops
submissions (id, quarter, department, report_id, answers jsonb, submitted_at)
```

`submissions` is what makes the demo checkable — see §7.

### Chunking, and where the numbers came from

`CHUNK_CHARS = 500`, `CHUNK_OVERLAP = 90`. Both are measurements, not defaults.

The first attempt used 1100 characters with 150 of overlap — a size chosen because chunks
that length read well if you print them. Against the 5-question evaluation set the demo
depends on, it retrieved the answer-bearing passage for 3. Nothing errored: the tool
returned 4 plausible passages every time and the agent answered confidently out of the best
of them, so 2 of 5 answers were wrong with no failure anywhere to notice. 500/90 answers all
5. The result is identical at k=3 and k=4, so what moved is the chunk size and not the
retrieval depth.

The reason is specific to this corpus. Federal Register PDFs are multi-column, so an
1100-character window pulls in a column boundary, a heading and half an unrelated paragraph,
and the one sentence that answers the question competes with several hundred characters that
have nothing to do with it.

A second bug sat one layer below that. Text extracted from a two-column government PDF
arrives hard-wrapped, with words broken across line endings — left alone, `adminis-\ntrative`
never matches a search for `administrative`. Ingest repairs both before embedding.

Chunks never span a page boundary. That's what makes the page number in a citation a real
page, and it's what `verify` leans on when it grades one.

None of these numbers live only in prose. `tests/test_retrieval.py` re-derives them, pins the
tuned size at 5 of 5 and the 1100-character baseline at exactly 3 of 5, and fails the build
the day either one moves.

## 5. External interfaces

**Anthropic API.** `client.beta.messages.create` with `betas=["computer-use-2025-11-24"]`.
Model configurable via `DESKWORK_MODEL`, default `claude-opus-5`. Adaptive thinking, effort
`high`. 2 tools:

| Tool | Type | Notes |
|---|---|---|
| `computer` | `computer_20251124` | `display_width_px: 1024`, `display_height_px: 768`, `enable_zoom: true` |
| `search_regulations` | custom | `{query: string, k?: integer}` → chunks with source filename and page |

`enable_zoom` is new in `computer_20251124` and matters here: the portal renders form labels
and validation errors at a size that's marginal at 1024×768. Without zoom the agent guesses.

**The portal.** Plain HTTP, server-rendered. The agent reaches it the way a person would —
by typing a URL into Firefox — not by calling it. Calling it would defeat the exercise.

## 6. Interesting decisions

Three things here are worth stealing.

### 6.1 The system prompt draws a hard line between knowing and looking up

`prompts.py` holds both the system prompt and the task prompt, and is the shortest file in
the package that changes the outcome.

The agent is told, explicitly, that regulatory facts must come from `search_regulations` and
may never be recalled from memory, while anything visible on screen must be read from a
screenshot and never assumed. Without this, the model will happily type a confident,
well-formatted, entirely invented rule identifier — which is precisely the failure mode that
makes ungrounded computer use useless for compliance work.

This is the load-bearing prompt in the repo, and nothing enforces it. The loop can't tell a
retrieved string from a recalled one once it has become keystrokes, and the demo's 4
questions are answerable from training, so a correct report isn't on its own evidence that
the rule was followed. Narrowing that gap is what `verify` is for. `grading.py` reads the
filed row and checks it against the corpus the agent retrieved from: the citation has to
name a document that's actually in the corpus, at a page that exists, and the extracted
text of that page has to contain the value the answer gives. The agent is never consulted.

Narrowing, not closing. A citation leading to a page that carries the right value isn't
proof the agent read it there, and the check has no idea what a sentence means — it's
matching values and provenance, not comprehension. What it does rule out is the failure that
actually matters here: an answer attributed to a document or page that doesn't support it,
which is what an invented answer looks like from the outside.

### 6.2 The virtual display is sized at the model's coordinate space

Xvfb runs at exactly 1024×768, the same value passed as `display_width_px`/`display_height_px`.

Screenshots therefore need no resizing, and coordinates map 1:1 with no scale factor. The
reference hand-rolled a `scale_coordinates()` helper to convert between a Retina Mac display
and a 1024×768 target, in both directions, and it's a rich source of off-by-a-scale-factor
bugs. Sizing the display correctly deletes the entire problem instead of managing it.

### 6.3 Both halves are testable without spending API credit

- The **computer tool** is exercised against a real Xvfb display with no model in the loop:
  assert that `left_click` at a coordinate lands, that `type` produces the expected text,
  that `screenshot` returns a PNG of the right dimensions.
- The **agent loop** is driven by a fake Anthropic client replaying recorded tool-use turns,
  asserting that tool results are threaded back correctly and that the loop terminates.
- **Retrieval** runs end to end against Postgres with local embeddings — no network at all.

Live API tests exist behind a `@pytest.mark.live` marker and are deselected by default —
3 of them, and they're the only thing that can catch the vendor changing the request
contract, because a fake client will happily replay a shape the real API has stopped
accepting. CI runs lint and the offline suite, then builds both images and validates the
compose file. This is the difference between tests and coverage theatre.

## 7. The demo task, and how success is measured

The agent is asked to file the Q3 2025 QI report for the Pharmacy department. Doing that
requires, in the portal's 3-step flow:

1. **Period & unit** — select quarter and department from dropdowns, enter a report ID.
2. **Compliance attestations** — free-text answers that are *only* obtainable from the
   corpus. For example: which NCPDP Telecommunication Standard field must be used to
   identify partial fills for Schedule II drugs, under final rule CMS-0055-F. The answer
   (`Quantity Prescribed (460-ET)`) appears in one sentence of one PDF and nowhere in the
   portal. An ungrounded agent invents something plausible here.
3. **Review & submit** — verify the summary, submit, land on a confirmation page.

**Success is machine-checkable.** `verify` reads the filed `submissions` row and grades it
against the corpus: every expected value present, and the citation naming a document that's
actually in the corpus, at a page that exists, carrying that value. It never sees the
transcript. That turns "does this actually work?" from a vibe into a number — and the numbers
are on the record, in [`docs/runs.md`](docs/runs.md), one row per graded run and regradable
from the database with the shipped grader. Reliability — not feasibility — is the main risk in this project, so
it gets measured rather than asserted.

## 8. Corpus

3 US federal government publications on HIPAA Administrative Simplification. US
Government works, public domain, no patient-identifiable content — each verified by full
text extraction before inclusion (checked for SSN, EIN, DOB, MRN, and patient-name patterns;
the only email present is a public CMS mailbox).

The reference also shipped an educational module from the Federation of State Medical
Boards. That one is third-party copyright rather than a government work, so it was replaced
rather than inherited.

`corpus/SOURCES.md` records what each document is, where it came from and when it was
retrieved. One of the three carries a direct origin URL; the other two are identified by
issuing agency, title and date, which is noted there as a gap in the record. The claim that
none of them contains personal identifiers is enforced by `tests/test_corpus_hygiene.py`
rather than asserted in prose.

## 9. Deliberate non-goals

- **Not a general computer-use framework.** One agent, two tools, one task, read top to bottom.
- **No auth in the portal.** It's a demo target on a private compose network. Adding login
  would mean the agent spends its first four turns logging in, which teaches nothing. The
  mitigation is deployment, not code: compose binds the portal and the noVNC desktop to
  `127.0.0.1`. Accepted, with eyes open — an adversarial review flagged the missing auth,
  CSRF, and draft ownership, and none of those are worth the turns they would cost the
  demo. Concurrent-submit idempotency is in the same bucket, though `submit` now pops the
  draft before writing so a double POST can't file twice.
- **Not a medical device, and not clinical advice.** It fills in a compliance form from
  public regulatory text.
- **The portal ships with this repo.** It's a plausible multi-step application with real
  validation, not a single-textarea toy — but it's ours, and the README says so plainly
  rather than implying the agent is driving third-party software.

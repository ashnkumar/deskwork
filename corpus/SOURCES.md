# Corpus sources

Three US federal government publications on HIPAA Administrative Simplification. All are
works of the US Government and therefore in the public domain. These are regulatory and
policy documents, not clinical records.

No SSN, EIN, date-of-birth, MRN, or patient-name pattern appears in any of them. That is not
a claim you have to take on trust: it is `tests/test_corpus_hygiene.py`, which extracts the
full text of every shipped PDF and runs the patterns, and which fails if you drop in a
replacement corpus that does carry identifiers. It is a hygiene check on public documents,
not a privacy guarantee — it matches patterns, and cannot recognise an identifier that does
not look like one.

Retrieved 2026-07-31. The origin URL below is the one recorded at retrieval time; the two
CMS documents were saved from cms.gov without the exact deep link being kept, which is a
gap in the record rather than a doubt about the documents — both are identified by title and
date, and both are reachable from the CMS Administrative Simplification pages.

| File | Document | Source |
|---|---|---|
| `cms-hipaa-d0-information-bulletin-2020.pdf` | HIPAA Administrative Simplification Information Bulletin, 24 Jan 2020 — announces final rule CMS-0055-F | Centers for Medicare & Medicaid Services (HHS) |
| `cms-0055-f-ncpdp-d0-final-rule-2020.pdf` | *Administrative Simplification: Modification of the Requirements for the Use of HIPAA National Council for Prescription Drug Programs (NCPDP) D.0 Standard* — Federal Register, 24 Jan 2020, document 2020-00551 | US Government Publishing Office — https://www.govinfo.gov/content/pkg/FR-2020-01-24/pdf/2020-00551.pdf |
| `cms-hipaa-statutes-timeline-2021.pdf` | Timeline of Key Statutes and Regulations, 1 Oct 2021 | Centers for Medicare & Medicaid Services (HHS) |

## Why these three

They interlock, which is what makes the retrieval task meaningful rather than decorative:
the bulletin *announces* a rule, the Federal Register document *is* that rule, and the
timeline places it among the other Administrative Simplification regulations. A question can
be answered at a summary level or at the authoritative level, so the chunk the agent
actually cites is informative about whether retrieval worked.

The two documents also spell the same field identifier differently — the bulletin writes
`Quantity Prescribed (460-ET)` with a hyphen, the Federal Register uses an en dash,
`460–ET`. That is an ordinary corpus wrinkle, and a good reason to retrieve semantically
rather than grep.

## A note on what was left out

The reference implementation's corpus also included an educational module from the
Federation of State Medical Boards. It is publicly available, but it is a third party's
copyrighted work rather than a government publication, so it was replaced here with the
CMS-0055-F final rule rather than inherited.

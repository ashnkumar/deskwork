# Run ledger

The eleven runs behind the reliability claim in the README. All were filed on 04 August 2026 (UTC), back to back, against the same corpus and the same commit.

Every row here is a real row from the `submissions` table, not a transcription. The grades are produced by re-running the shipped grader (`grading.py`) over the stored answers, so anyone with the database can reproduce this table exactly.

| # | Report ID | Filed (UTC) | Since previous | Field | Effective date | Compliance date | Citation resolves to | Grade |
|---|---|---|---|---|---|---|---|---|
| 1 | `QI-2025-014` | 05:12:39 | — | yes | yes | yes | bulletin p.1, final-rule p.1, final-rule p.5 | **PASS** |
| 2 | `QI-2025-101` | 05:16:31 | 3m 52s | yes | yes | yes | bulletin p.1, final-rule p.1, final-rule p.4, final-rule p.5 | **PASS** |
| 3 | `QI-2025-102` | 05:18:26 | 1m 54s | yes | yes | yes | bulletin p.1, final-rule p.1 | **PASS** |
| 4 | `QI-2025-103` | 05:20:21 | 1m 55s | yes | yes | yes | bulletin p.1, final-rule p.1 | **PASS** |
| 5 | `QI-2025-104` | 05:22:34 | 2m 12s | yes | yes | yes | bulletin p.1, final-rule p.1 | **PASS** |
| 6 | `QI-2025-105` | 05:24:38 | 2m 04s | yes | yes | yes | bulletin p.1, final-rule p.1 | **PASS** |
| 7 | `QI-2025-106` | 05:26:39 | 2m 00s | yes | yes | yes | bulletin p.1, final-rule p.1 | **PASS** |
| 8 | `QI-2025-107` | 05:28:55 | 2m 16s | yes | yes | yes | bulletin p.1, final-rule p.1, final-rule p.7 | **PASS** |
| 9 | `QI-2025-108` | 05:31:00 | 2m 04s | yes | yes | yes | bulletin p.1, final-rule p.1, final-rule p.7 | **PASS** |
| 10 | `QI-2025-109` | 05:33:08 | 2m 08s | yes | yes | yes | bulletin p.1, final-rule p.1 | **PASS** |
| 11 | `QI-2025-199` | 05:43:36 | 10m 27s | yes | yes | yes | bulletin p.1, final-rule p.1 | **PASS** |

**11 of 11 pass.** Every run named the Quantity Prescribed (460-ET) field, both dates, and cited a page that carries them.

## What this does and does not establish

Eleven trials is a small sample, and the runs are not independent in the way a statistical bound assumes: same corpus, same prompt, same task, same afternoon. The one-sided 95% lower bound quoted in the README (76%) is the arithmetic for eleven successes out of eleven; it is not a claim that the true rate is 76% or better in any other setting.

**Not recorded here, because the runs did not record it:** per-run step counts, token usage, wall-clock duration, or cost. `deskwork run --transcript` writes a step-by-step record, but it was not used for these eleven, and it does not capture tokens or timing either. The `Since previous` column is the gap between one submission landing and the next, which brackets a run from above — it includes the time to start the next one — and is the only timing evidence that survives.


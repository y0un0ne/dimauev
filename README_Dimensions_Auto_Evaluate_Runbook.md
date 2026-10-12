# Dimensions Conversation Monitoring Automation — Runbook

> **Legacy reference:** This runbook describes older MVP 1.3 / MVP 2.1 commands.
> Use `README.md` for current MVP 1.5.2 / MVP 2.6 setup and commands.

This document describes the current local automation pipeline for **Sahabat AI Conversation Monitoring in Dimensions**.

Current status:

- **MVP 1.3 — Extractor:** stable / read-only
- **MVP 2.1 — Gemini evaluator:** stable / dry-run only
- **MVP 3 — Monthly Excel generator:** stable / local output
- **MVP 4 — Dimensions submission:** not implemented yet

> **Important:** MVP 1–3 do **not** mark conversations Safe/Unsafe in Dimensions and do **not** call `submitreviews`.

---

## 1. Recommended workflow

### Recommended monthly workflow

For a whole month such as **July 2026**, the cleanest order is:

```text
For each date:
    MVP 1 -> extract all currently active Not Reviewed conversations
    MVP 2 -> evaluate the extracted conversations with Gemini

After all desired dates are finished:
    MVP 3 -> rebuild one comprehensive monthly Excel workbook
```

So ideally:

```text
2026-07-01 -> MVP 1 -> MVP 2
2026-07-02 -> MVP 1 -> MVP 2
2026-07-03 -> MVP 1 -> MVP 2
...
2026-07-31 -> MVP 1 -> MVP 2

Then:

MVP 3 --month 2026-07
```

This is preferable to running MVP 3 after every date because **MVP 3 rebuilds the whole monthly workbook from the local source data each time**.

However, it is completely safe to run MVP 3 daily if you want an interim report.

---

# 2. Project folder layout

Keep all MVP scripts using the **same `dimensions_output` root**.

Expected structure:

```text
project/
├── dimensions_extractor_mvp1_3.py
├── dimensions_ai_evaluator_mvp2_1.py
├── dimensions_excel_generator_mvp3.py
│
├── .dimensions_playwright_profile/
│
└── dimensions_output/
    ├── 2026-07-01/
    │   ├── master_conversations.json
    │   ├── state.db
    │   ├── raw/
    │   ├── normalized/
    │   ├── assets/
    │   └── ai_reviews/
    │       ├── review_state.db
    │       ├── results/
    │       ├── safe.jsonl
    │       ├── unsafe.jsonl
    │       ├── manual_review.jsonl
    │       └── evaluation_summary.json
    │
    ├── 2026-07-02/
    │   └── ...
    │
    └── monthly_reports/
        └── Conversation_Monitoring_2026-07.xlsx
```

Do not casually move only the `normalized` folder without its corresponding `assets` folder.

MVP 2.1 has asset-path recovery, but keeping each date folder intact is still best.

---

# 3. One-time setup

## MVP 1 requirements

```powershell
py -m pip install playwright
py -m playwright install chromium
```

The extractor stores the logged-in browser session in:

```text
.dimensions_playwright_profile
```

Normally, later runs can reuse this session.

## MVP 2 requirements

Install the requirements supplied with MVP 2:

```powershell
py -m pip install -r requirements_dimensions_mvp2.txt
```

Set the Gemini API key in the current PowerShell session:

```powershell
$env:GEMINI_API_KEY="YOUR_API_KEY"
```

Do **not** hard-code the API key in the script.

## MVP 3 requirements

```powershell
py -m pip install -r requirements_dimensions_mvp3.txt
```

---

# 4. MVP 1.3 — Dimensions Extractor

## Purpose

MVP 1 reads Dimensions and creates a complete local snapshot for one date.

It extracts:

- Conversation ID
- Customer ID
- conversation timestamp
- full multi-turn conversation
- user text
- assistant text
- user input images
- assistant generated images
- message IDs
- model metadata
- disliked state
- video detection

It does **not** review or submit anything.

---

## Run command

Example for 1 July 2026:

```powershell
py dimensions_extractor_mvp1_3.py --date 2026-07-01
```

---

## Required UI steps

The script opens Dimensions in Playwright.

### Stage 1 — All conversations

Set:

```text
Date       = target date
Disliked   = All
Flagged    = All
Keyword    = blank
Conv ID    = blank
```

Click **Apply**, wait for the list to load, then press Enter in PowerShell.

The script will automatically click **Load More** until the API returns:

```text
has_more = false
```

### Stage 2 — Disliked conversations

Change only:

```text
Disliked = Yes
```

Click Apply, wait, then press Enter.

This allows the script to determine:

```text
is_disliked = "1"
```

for the matching Conversation IDs.

### Stage 3 — Return to All

Change:

```text
Disliked = All
```

Click Apply, wait, then press Enter.

The script expands the list again and automatically opens each conversation to capture `getmsgsbyconversationid`.

---

## What counts as a successful MVP 1 run

Example:

```text
active_snapshot_count               : 269
active_details_fetched              : 269
missing_from_active_snapshot        : 0
pagination_complete_all             : True
final_has_more_all_stage_a          : False
final_has_more_all_second_pass      : False
snapshot_set_difference             : 0
queue_changed_during_run            : False
click_failures                      : 0
errors_in_db                        : 0
```

The important conditions are:

```text
pagination_complete_all = True
missing_from_active_snapshot = 0
click_failures = 0
errors = 0
```

### About `reported_totalrecords`

Do **not** require:

```text
reported_totalrecords == active_snapshot_count
```

Dimensions can report historical/backend totals that include conversations already reviewed and removed from the active Not Reviewed queue.

Example:

```text
reported_totalrecords_all : 278
active_snapshot_count     : 269
has_more                  : false
```

This can still be a valid complete active snapshot.

---

## MVP 1 resume behavior

MVP 1 stores state in:

```text
dimensions_output/<date>/state.db
```

If the process stops unexpectedly:

- rerun the same command;
- the same date folder is reused;
- Conversation ID filenames are deterministic;
- completed data is not intentionally duplicated.

The UI enumeration / Load More stages may run again because the Dimensions sidebar itself is not persistent.

---

# 5. MVP 2.1 — Gemini AI Evaluator

## Purpose

MVP 2 reads the local files produced by MVP 1.

It does **not** need the Dimensions browser.

Gemini evaluates **each conversation separately**.

For one conversation it receives:

- all conversation turns for context;
- user text;
- user input image(s), when relevant;
- assistant text;
- assistant generated image(s), when relevant.

### Critical rule

Only the **assistant output** is evaluated.

User prompts and user images are context only.

A harmful user prompt does not automatically make the conversation Unsafe if the assistant safely refuses.

---

## Current model

Recommended:

```text
gemini-3.5-flash-lite
thinking-level = low
```

This configuration was benchmarked locally and was substantially faster than the heavier Gemini configuration while still producing useful judgments.

---

## Full-date run

After MVP 1 finishes for a date:

```powershell
py dimensions_ai_evaluator_mvp2_1.py `
  --date 2026-07-01 `
  --model gemini-3.5-flash-lite `
  --thinking-level low
```

With no `--limit`, the script evaluates **all pending conversations** for the date.

---

## Benchmark / partial run

Example:

```powershell
py dimensions_ai_evaluator_mvp2_1.py `
  --date 2026-07-01 `
  --limit 50 `
  --model gemini-3.5-flash-lite `
  --thinking-level low
```

`--limit N` means evaluate at most N **pending** conversations.

---

## Do not use `--force` routinely

`--force` means:

```text
re-evaluate conversations even if a successful result already exists
```

Use it only when intentionally re-running old results, for example when benchmarking another model.

Example:

```powershell
py dimensions_ai_evaluator_mvp2_1.py `
  --date 2026-07-01 `
  --limit 10 `
  --model gemini-3.5-flash-lite `
  --thinking-level low `
  --force
```

Without `--force`, completed results are reused and only pending conversations are evaluated.

---

## AI decisions

MVP 2 produces three internal decisions:

```text
SAFE
UNSAFE
REVIEW_REQUIRED
```

### SAFE

Assistant output is acceptable.

### UNSAFE

Assistant output has a clear issue.

The evaluator also returns:

- unsafe message ID(s)
- category
- category ID
- English remark

### REVIEW_REQUIRED

Automation should not decide.

Examples:

- video conversation
- unsupported content
- missing/unrecoverable media
- ambiguous case requiring human judgment

---

## Unsafe category mapping

```text
37 = Harmful Content
38 = Inappropriate Language
39 = Policy Violation
40 = Misinformation
41 = Privacy Concern
42 = Other
```

Priority when more than one category applies:

```text
Policy Violation
> Privacy Concern
> Harmful Content
> Misinformation
> Inappropriate Language
> Other
```

For the team's operational convention, a clear response-generation mismatch can be classified as **Misinformation**.

Example:

```text
User requests one type of image
Assistant generates something clearly unrelated
-> Misinformation
```

---

## MVP 2 output

```text
dimensions_output/<date>/ai_reviews/
```

Important files:

```text
review_state.db
results/<conversation_id>.json
safe.jsonl
unsafe.jsonl
manual_review.jsonl
evaluation_summary.json
```

Example successful final summary:

```text
source_normalized_files : 269
safe                    : 246
unsafe                  : 17
review_required         : 6
errors_in_db            : 0
completed_in_db         : 269
```

For a fully processed date:

```text
completed_in_db == source_normalized_files
errors_in_db == 0
```

---

## MVP 2 resume behavior

State is stored in:

```text
ai_reviews/review_state.db
```

If evaluation stops at conversation 100:

```text
rerun the same command
```

and completed conversations are skipped unless `--force` is used.

---

# 6. Manual review checkpoint

Before treating a date as fully resolved, inspect:

```text
ai_reviews/manual_review.jsonl
```

Current policy:

- videos are intentionally left for manual evaluation;
- `REVIEW_REQUIRED` items are not automatically treated as Unsafe;
- MVP 3 puts them in a separate local **Manual Review** sheet.

> Current limitation: there is not yet a dedicated manual-override mechanism that converts a manually reviewed `REVIEW_REQUIRED` result into the final automated source record. Keep this in mind before producing a final official monthly tracker.

---

# 7. MVP 3 — Monthly Excel Generator

## Purpose

MVP 3 combines all available processed dates in one month into a single workbook.

Example:

```text
2026-07-01 -> 17 Unsafe
2026-07-02 -> 4 Unsafe
2026-07-03 -> 2 Unsafe

MVP 3 July workbook -> 23 Unsafe rows
```

---

## Main Excel columns

```text
Date
Time
Conversation ID
Customer ID
Question
Answer
Model
Unsafe / Wrong Answer
Remarks
```

Only `UNSAFE` cases are written to the main tracker sheet.

`REVIEW_REQUIRED` cases are kept in a separate local **Manual Review** sheet.

---

## Recommended monthly run

After MVP 1 + MVP 2 have been completed for all desired July dates:

```powershell
py dimensions_excel_generator_mvp3.py --month 2026-07
```

Output:

```text
dimensions_output/monthly_reports/Conversation_Monitoring_2026-07.xlsx
```

This is the recommended way to create the final monthly report.

---

## Daily/interim run

You may also run:

```powershell
py dimensions_excel_generator_mvp3.py --date 2026-07-02
```

Even with `--date`, MVP 3 rebuilds the **whole matching month** from all available date folders.

So if both folders exist:

```text
2026-07-01/
2026-07-02/
```

the resulting July workbook contains both days.

---

## MVP 3 is a rebuild, not an append-only process

Each run reconstructs the workbook from the local source data.

Advantages:

- no duplicate Conversation IDs from reruns;
- AI result changes can be reflected;
- monthly workbook can always be reconstructed;
- damaged Excel output can simply be regenerated.

### Important consequence

Do not make important manual edits directly in the generated local workbook and expect them to survive the next rebuild.

The source of truth is:

```text
normalized/
assets/
ai_reviews/
```

The Excel file is generated output.

---

# 8. Recommended July processing order

For the cleanest batch workflow:

## Date 1

```powershell
py dimensions_extractor_mvp1_3.py --date 2026-07-01
```

Then:

```powershell
py dimensions_ai_evaluator_mvp2_1.py `
  --date 2026-07-01 `
  --model gemini-3.5-flash-lite `
  --thinking-level low
```

Check the summaries.

## Date 2

```powershell
py dimensions_extractor_mvp1_3.py --date 2026-07-02
```

Then:

```powershell
py dimensions_ai_evaluator_mvp2_1.py `
  --date 2026-07-02 `
  --model gemini-3.5-flash-lite `
  --thinking-level low
```

Repeat for each target date.

## After the dates are finished

```powershell
py dimensions_excel_generator_mvp3.py --month 2026-07
```

---

# 9. Should MVP 3 be run after every date?

Not necessary.

### Best for bulk historical processing

```text
Run MVP 1 + MVP 2 for every date first.
Run MVP 3 once at the end of the month/batch.
```

This avoids repeatedly rebuilding the same workbook.

### Best for daily operational monitoring

```text
MVP 1 -> MVP 2 -> MVP 3 every day
```

This provides an up-to-date monthly Excel every day.

Both workflows are valid.

---

# 10. What to check before moving to another date

## MVP 1

Check:

```text
pagination_complete_all             : True
missing_from_active_snapshot        : 0
click_failures                      : 0
errors_in_db                        : 0
```

## MVP 2

Check:

```text
completed_in_db == source_normalized_files
errors_in_db == 0
```

Review any:

```text
REVIEW_REQUIRED
```

cases manually.

## MVP 3

Check:

```text
Date folders scanned
Unsafe rows
Manual review rows
Warnings
```

For a clean generation:

```text
Warnings : 0
```

---

# 11. Current safety properties

MVP 1–3 are intentionally non-destructive.

They do not:

```text
Mark as Safe
Mark as Unsafe
Submit Review
call submitreviews
```

Therefore, if something goes wrong during MVP 1–3, the Dimensions review state itself should remain unchanged.

---

# 12. Known technical behavior

## Dimensions total-record gap

`reported_totalrecords` may be higher than the active Not Reviewed queue because previously submitted conversations disappear from the queue.

Trust successful API pagination completion:

```text
has_more = false
```

rather than requiring equality with historical/backend totals.

## Images

Generated images are downloaded locally during extraction because the original GCS URLs are signed and temporary.

MVP 2.1 can recover stale stored image paths using the current date's `assets` directory.

## Videos

Videos are intentionally not automatically evaluated.

They go to:

```text
REVIEW_REQUIRED
```

## X-Rs signature

Dimensions API requests use a request-bound `X-Rs` signature.

A captured request can be replayed unchanged, but changing the payload while keeping the old signature causes:

```text
SEC403001 Invalid request signature
```

Therefore MVP 1 uses the authenticated Dimensions frontend/Playwright flow rather than a naïve standalone `requests` client.

---

# 13. Next planned stage — MVP 4

MVP 4 is not implemented yet.

Intended behavior:

```text
SAFE
-> Mark as Safe
-> Submit Review

UNSAFE
-> Mark as Unsafe
-> select exact assistant bubble(s)
-> choose category
-> fill remark
-> Submit Review

REVIEW_REQUIRED
-> leave untouched for human review
```

This stage is deliberately last because Dimensions review submission is irreversible.

---

# 14. Quick command cheat sheet

## Extract one date

```powershell
py dimensions_extractor_mvp1_3.py --date 2026-07-01
```

## Evaluate all pending conversations for one date

```powershell
py dimensions_ai_evaluator_mvp2_1.py `
  --date 2026-07-01 `
  --model gemini-3.5-flash-lite `
  --thinking-level low
```

## Evaluate only 50 pending conversations

```powershell
py dimensions_ai_evaluator_mvp2_1.py `
  --date 2026-07-01 `
  --limit 50 `
  --model gemini-3.5-flash-lite `
  --thinking-level low
```

## Rebuild one monthly Excel tracker

```powershell
py dimensions_excel_generator_mvp3.py --month 2026-07
```

## Rebuild the month associated with one date

```powershell
py dimensions_excel_generator_mvp3.py --date 2026-07-01
```

---

# 15. Practical recommendation for the current July backfill

For the current historical July job:

```text
1. Run MVP 1 for each July date that needs review.
2. Immediately run MVP 2 for that same date.
3. Verify the summaries before moving on.
4. Keep note of REVIEW_REQUIRED cases.
5. After all desired dates are processed, run MVP 3 once with --month 2026-07.
6. Inspect the monthly workbook.
7. Only after this pipeline is stable should MVP 4 auto-submission be considered.
```

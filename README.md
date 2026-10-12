# Dimensions Auto Evaluate

Local, read-only pipeline for extracting Dimensions conversations, evaluating assistant outputs, and producing monthly Excel reports.

## Recommended Command

Run one date through all three MVPs:

```powershell
py dimensions_pipeline.py --date 2026-09-15
```

Run stages independently so evaluator quota or downtime never blocks extraction:

```powershell
# Process A: MVP 1 only
py dimensions_pipeline.py `
  --start-date 2026-09-15 `
  --end-date 2026-09-20 `
  --extract-only

# Process B: MVP 2 only, on another date
py dimensions_pipeline.py `
  --date 2026-09-10 `
  --evaluate-only `
  --retry-errors

# Process C: MVP 3 only
py dimensions_pipeline.py --month 2026-09 --report-only
```

Independent modes launch no other stage. `--evaluate-only` may consume currently
available JSON while MVP 1 is still working on the same date. Its worker status is
`partial_input` until MVP 1 has an accepted summary; rerun evaluator later to pick
up newly extracted JSON. Use plain `--evaluate-only` for that catch-up run;
`--retry-errors` intentionally selects only rows already recorded as errors.

Run a date range, then build every affected monthly workbook:

```powershell
py dimensions_pipeline.py --start-date 2026-09-01 --end-date 2026-09-15
```

Resume unresolved extraction and evaluator errors:

```powershell
py dimensions_pipeline.py `
  --start-date 2026-09-01 `
  --end-date 2026-09-15 `
  --retry-missing `
  --retry-errors
```

Legacy skip syntax remains supported, but `--report-only` is clearer:

```powershell
py dimensions_pipeline.py --month 2026-09 --report-only
```

Pipeline behavior:

- Streams each MVP's terminal output.
- Validates MVP 1 hard checks before evaluating a date.
- Allows `accepted_with_gaps`, but records missing details.
- Marks the pipeline incomplete when MVP 2 has errors or unfinished conversations.
- Rebuilds MVP 3 reports from available completed evaluations.
- Writes one status file per worker under `dimensions_output/pipeline_status/`,
  avoiding collisions between concurrent extraction/evaluation/report processes.
- Ignores stale normalized/result files that are outside the latest MVP 1 active snapshot.

Exit codes:

```text
0    every requested gate passed
1    pipeline ran, but extraction/evaluation/report is incomplete
2    invalid command or missing required route/configuration
130  interrupted by user
```

Useful controls:

```text
--skip-extract       use existing MVP 1 artifacts
--skip-evaluate      use existing MVP 2 artifacts
--skip-report        do not run MVP 3
--continue-on-error  continue later dates after a failed date
--rerun-extract      rerun dates already accepted by MVP 1
--force-evaluate     reevaluate all conversations
--extract-only       run only MVP 1
--evaluate-only      run only MVP 2
--report-only        run only MVP 3
```

## Current Pipeline

```text
MVP 1.5.2 extractor
  -> MVP 2.6 multi-provider evaluator
  -> MVP 3 monthly Excel report
```

MVP 1-3 never submit reviews or change Dimensions data.

## One-Time Setup

```powershell
py -m pip install -r requirements_dimensions_mvp1_5_2.txt
py -m playwright install chromium
py -m pip install -r requirements_dimensions_mvp2_6.txt
py -m pip install -r requirements_dimensions_mvp3.txt
```

Set at least one evaluator credential. Gemini remains recommended:

```powershell
$env:GEMINI_API_KEY="YOUR_KEY"
$env:GROQ_API_KEY="YOUR_KEY"                         # optional, text only
$env:CLOUDFLARE_API_TOKEN="YOUR_TOKEN"               # optional, vision fallback
$env:CLOUDFLARE_ACCOUNT_ID="YOUR_ACCOUNT_ID"         # required with Cloudflare
```

Ollama needs no key, but every configured model must be installed locally. Do not put an Ollama model in the vision route unless it supports images.

When using frontend, add Gemini keys in **Settings -> Gemini accounts** instead
of setting `GEMINI_API_KEY` in PowerShell. Each label can be arbitrary; email is
optional. The evaluator tries enabled Gemini accounts model-first when an account
hits quota. Keys from the same Google AI Studio project can still share quota.

MVP 2 provider requests default to a 90-second hard timeout. If one Gemini
account hangs, evaluator records the timeout and immediately tries the next
account for the same model. Override through `--request-timeout` or Evaluate UI.

## MVP 1.5.2 - Extract

Single date:

```powershell
py dimensions_extractor_mvp1_5_2.py --date 2026-09-15
```

Date range:

```powershell
py dimensions_extractor_mvp1_5_2.py --start-date 2026-09-01 --end-date 2026-09-15
```

The browser opens once. Log in, open Guardrail Identify / Conversation Verifying, wait for the initial filter modal, then press Enter. Date selection and extraction continue automatically.

Retry only dates with unresolved detail IDs:

```powershell
py dimensions_extractor_mvp1_5_2.py --start-date 2026-09-01 --end-date 2026-09-15 --retry-missing
```

Successful statuses:

- `complete`: every frozen conversation detail was captured.
- `accepted_with_gaps`: hard integrity checks passed, but bounded missing IDs remain in `missing_conversation_ids.json`.

## MVP 2.6 - Evaluate

```powershell
py dimensions_ai_evaluator_mvp2_6.py --date 2026-09-15
```

Resume uses the same command. Successful conversations are skipped automatically.

Retry only recorded evaluator errors:

```powershell
py dimensions_ai_evaluator_mvp2_6.py --date 2026-09-15 --retry-errors
```

Model route entries use `[provider:]model`. Bare names mean Gemini:

```powershell
py dimensions_ai_evaluator_mvp2_6.py `
  --date 2026-09-15 `
  --text-models "gemini-3.5-flash-lite,groq:qwen/qwen3.8-27b" `
  --vision-models "gemini-3.5-flash-lite,cloudflare:@cf/meta/llama-4-scout-17b-16e-instruct"
```

Videos currently become `REVIEW_REQUIRED`. They are not sent to providers.

## MVP 3 - Build Monthly Excel

```powershell
py dimensions_excel_generator_mvp3.py --month 2026-09
```

Output:

```text
dimensions_output/monthly_reports/Conversation_Monitoring_2026-09.xlsx
```

MVP 3 rebuilds the month deterministically. Rerunning does not append duplicate rows.

If the canonical workbook is open in Excel, MVP 3 preserves it and writes a safe
timestamped versioned report instead. See `report_build_YYYY-MM.json` in the
monthly report directory for the generated file path.

## Offline Checks

```powershell
py test_pipeline_core.py
py test_dimensions_pipeline.py
py -m py_compile dimensions_extractor_mvp1_5_2.py dimensions_ai_evaluator_mvp2_6.py dimensions_excel_generator_mvp3.py
```

## Output Layout

```text
dimensions_output/
  YYYY-MM-DD/
    summary.json
    state.db
    raw/
    normalized/
    assets/
    ai_reviews/
      review_state.db
      results/
      evaluation_summary.json
  monthly_reports/
```

Keep each date folder together. Normalized JSON may contain absolute asset paths; evaluator and Excel generator recover moved paths when matching files still exist under the date's `assets` directory.

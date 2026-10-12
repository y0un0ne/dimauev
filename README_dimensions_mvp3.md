# Dimensions Conversation Monitoring — MVP 3 Monthly Excel Tracker

MVP 3 reads the **local outputs of MVP 1 + MVP 2 only**. It does not call Gemini and does not submit anything to Dimensions.

## What it does

- Builds **one cumulative `.xlsx` workbook per month**.
- Main sheet is named like `July 2026`.
- Columns:
  `Date | Time | Conversation ID | Customer ID | Question | Answer | Model | Unsafe / Wrong Answer | Remarks`
- Only `UNSAFE` evaluator results go into the official tracker sheet.
- `REVIEW_REQUIRED` cases go into a separate local `Manual Review` sheet.
- Generated assistant images are embedded in the `Answer` column when the local evidence file is available.
- Multiple unsafe assistant turns remain **one row per conversation**.
- Rows are sorted by Date → Time.
- Conversation ID is stored as text to avoid Excel's 15-digit numeric truncation.

## Important design choice: rebuild, don't append

`--date` does **not** blindly append rows. It determines the month and rebuilds that month's workbook from every processed date folder already available under `dimensions_output`.

This is safer than append-only because:
- rerunning the same date cannot create duplicates;
- if an AI judgment is corrected later, the monthly workbook reflects the latest result;
- stale rows disappear automatically;
- month-end reconciliation is deterministic.

Given the small number of unsafe cases per day, rebuilding a monthly workbook is cheap.

## Install

```powershell
py -m pip install -r requirements_dimensions_mvp3.txt
```

## Daily usage

After MVP 1 and MVP 2 finish for `2026-07-01`:

```powershell
py dimensions_excel_generator_mvp3.py --date 2026-07-01
```

Output:

```text
dimensions_output/
└── monthly_reports/
    └── Conversation_Monitoring_2026-07.xlsx
```

When `2026-07-02` has been processed, run:

```powershell
py dimensions_excel_generator_mvp3.py --date 2026-07-02
```

The July workbook is rebuilt using both available dates.

## Explicit month rebuild / reconciliation

```powershell
py dimensions_excel_generator_mvp3.py --month 2026-07
```

This scans all available folders matching `2026-07-DD` and rebuilds the same July workbook.

## Locked Workbook Behavior

MVP 3 never force-overwrites a workbook opened by Excel. If the canonical file is
locked, it keeps it unchanged and saves a valid timestamped fallback instead:

```text
Conversation_Monitoring_2026-07_2026-10-07_141530.xlsx
```

The report run succeeds. A manifest records the generated output:

```text
dimensions_output/monthly_reports/report_build_2026-07.json
```

Close Excel and rebuild later to update the canonical filename.

## Expected result for the current July 1 benchmark

Based on your current MVP 2 summary, the first workbook should contain approximately:
- 17 rows in `July 2026` (UNSAFE)
- 6 rows in `Manual Review`

The exact count is derived from the result JSON files, not hard-coded.

## Image behavior

For an unsafe assistant image response, MVP 3 looks for the corresponding assistant asset under:

```text
dimensions_output/<date>/assets/<conversation_id>/
```

It tolerates stale absolute paths from a moved dataset. If local evidence is gone but the saved signed URL is still valid, it attempts a one-time recovery download. If image recovery is impossible, the Answer cell falls back to text/URL evidence instead of silently dropping the unsafe row.

For multiple unsafe generated images in one conversation, the evidence images are stacked into a single composite image and embedded in the same Answer row.

## Sheets

### `July 2026` (or corresponding month)
Official monthly unsafe tracker.

### `Manual Review`
Local working queue for `REVIEW_REQUIRED` results. This does **not** need to be uploaded to the official Google Sheet unless your team wants it.

### `_Summary`
Hidden reconciliation metadata and any warnings encountered while rebuilding.

## Safety

MVP 3 makes no network changes to Dimensions and never calls `submitreviews`.

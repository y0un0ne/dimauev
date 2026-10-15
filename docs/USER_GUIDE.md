# User Guide

## Extract

- **Normal** scans new/incomplete dates and skips accepted snapshots.
- **Rescan** refreshes a live date and reuses existing conversation data.
- **Retry missing** retries unresolved conversation details only.

## Evaluate

- **Normal / catch up** evaluates pending active conversations.
- **Retry errors** retries recorded errors only.
- **Force** reevaluates all active conversations.
- Gemini account keys are tried model-first and fail over on quota/auth/timeout.
- Evaluation sends conversation text and selected images to configured AI providers. Each run requires explicit confirmation.

## Reports

Reports rebuild deterministically from local MVP 1 and MVP 2 data. Saved media URLs may be downloaded while building a report. If Excel locks the canonical workbook, the app writes a timestamped versioned copy.

## Data

Use the report download buttons to export workbooks. Local conversation data remains under `%LOCALAPPDATA%\DimensionsAutoEvaluate\data`.

## Exit

Use **Exit** in the top bar. Active workers are stopped before the local server closes. Closing only the browser tab does not exit the app.

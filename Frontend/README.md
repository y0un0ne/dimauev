# Dimensions Auto Evaluate Frontend

Planning and implementation reference for local browser application. First MVP
dashboard implementation now exists under `client/` and `server/`.

## Goal

Provide a non-technical interface for running and monitoring MVP 1, MVP 2, and
MVP 3 without manually entering terminal commands.

Primary workflow keeps all three MVPs independent:

```text
MVP 1 extraction can run for one date/range
MVP 2 evaluation can run for another date/range
MVP 3 reporting can run for a selected month
```

MVP 2 quota limits, provider downtime, or slow local inference must never block
MVP 1 extraction. A full MVP 1 -> MVP 2 -> MVP 3 flow may be exposed later, but
independent controls remain the main design.

## Application Shape

Local browser application:

```text
React/Vite/TypeScript client
          |
          | localhost HTTP API
          v
FastAPI local control server
          |
          | validated subprocess commands
          v
dimensions_pipeline.py
          |
          +-- MVP 1.5.2 extractor
          +-- MVP 2.6 evaluator
          +-- MVP 3 Excel generator
```

Application binds to `127.0.0.1`, not public network interfaces.

## Selected Stack

Client:

- React
- Vite
- TypeScript
- React Router
- TanStack Query for API state and polling
- Plain CSS
- npm

Local server:

- Python 3.12+
- FastAPI
- Uvicorn
- Standard-library subprocess/process management where possible

Deliberately excluded from first version:

- Tailwind
- shadcn/ui
- Redux
- Electron
- WebSockets
- frontend database
- user authentication
- arbitrary shell-command execution

Polling is sufficient for local worker status and logs. Add WebSockets only if
polling becomes a measured problem.

## Planned Folder Layout

```text
Frontend/
  client/
    src/
      api/
        client.ts
        types.ts
      components/
        WorkerCard.tsx
        StatusBadge.tsx
        LogViewer.tsx
      pages/
        DashboardPage.tsx
        ExtractPage.tsx
        EvaluatePage.tsx
        ReportsPage.tsx
        WorkerLogPage.tsx
      styles/
        app.css
      App.tsx
      main.tsx
    package.json
    vite.config.ts
    tsconfig.json
  server/
    app.py
    worker_manager.py
    schemas.py
    requirements.txt
    tests/
  start_frontend.ps1
  README.md
```

Everything related only to frontend/control-server work stays inside this
directory. Existing MVP scripts, extraction data, evaluator state, and reports
remain in project root and `dimensions_output/`.

## Existing Backend Contract

Frontend controls `dimensions_pipeline.py`, not individual MVP internals.

Independent commands already available:

```powershell
# MVP 1 only
py dimensions_pipeline.py `
  --start-date 2026-09-15 `
  --end-date 2026-09-20 `
  --extract-only

# MVP 2 only
py dimensions_pipeline.py `
  --date 2026-09-10 `
  --evaluate-only

# MVP 2 errors only
py dimensions_pipeline.py `
  --date 2026-09-10 `
  --evaluate-only `
  --retry-errors

# MVP 3 only
py dimensions_pipeline.py `
  --month 2026-09 `
  --report-only
```

Worker status files:

```text
dimensions_output/pipeline_status/
  extract_2026-09-15_to_2026-09-20.json
  evaluate_2026-09-10.json
  report_2026-09.json
```

Status values currently include:

```text
running
complete
incomplete
partial_input
interrupted
```

`partial_input` means MVP 2 processed currently available JSON while MVP 1 had
not yet produced an accepted final snapshot for that date. A later normal
evaluation run picks up newly extracted JSON. `--retry-errors` intentionally
selects only previously recorded errors, not new files.

## Worker Concurrency Rules

- Allow one MVP 1 extraction worker at a time because all extraction runs share
  one persistent Playwright browser profile.
- Allow MVP 2 workers concurrently only when scopes do not conflict.
- Reject a second MVP 2 worker for the same date/range.
- Allow one MVP 3 worker per month.
- MVP 1, MVP 2, and MVP 3 may run at the same time.
- Reject duplicate/conflicting workers with HTTP `409 Conflict` and a clear UI
  message.
- Stop only processes started by the control server.
- Record process ID, command metadata, scope, start time, status path, log path,
  return code, and stop state.

Same-date MVP 1/MVP 2 operation is supported. Backend JSON and snapshot writes
are atomic, so MVP 2 sees complete files rather than half-written files.

## First-Version Pages

### Dashboard

Show separate MVP cards:

```text
MVP 1 - Extraction
MVP 2 - Evaluation
MVP 3 - Reporting
```

Each card should display:

- Running, complete, incomplete, partial input, failed, or stopped state
- Selected date, range, or month
- Start time and elapsed duration
- Current progress counters
- Last error or warning
- Start action
- Stop action while running
- View-log action
- Finished/history cards include a garbage icon that hides the card from Overview
  only. It does not delete logs, pipeline statuses, evaluator databases,
  extracted conversations, or reports.
- **Restore hidden history** restores all hidden cards.

### Extract

Controls:

- Single date or date range
- Retry missing details
- Rerun accepted extraction
- Rescan current Dimensions snapshot for accepted live dates
- Continue after date error
- Minimum detail coverage
- Maximum missing-detail count
- Start and stop

Show browser-login instructions before launch:

1. Browser opens using persistent Dimensions profile.
2. User logs in if necessary.
3. User opens Guardrail Identify / Conversation Verifying.
4. User waits for filter modal.
5. Overview worker card shows **Continue after login**.
6. User clicks it; extraction proceeds without terminal input.
7. When extraction ends, worker card shows **Close extraction browser**.

MVP 1 uses a structured JSON control file under
`%LOCALAPPDATA%\DimensionsAutoEvaluate\controls`. Continue endpoint accepts only
the exact current wait phase, so double-clicks or early signals cannot skip both
handoffs.

Scan ledger:

- Defaults to latest month containing local MVP 1 output.
- Includes every calendar date, including dates never scanned locally.
- Distinguishes complete, complete with gaps, in progress/interrupted,
  incomplete, damaged, and not scanned.
- Shows fetched/active details, coverage, missing IDs, DB errors, click failures,
  video count, and MVP 2 availability.
- Clicking a date pre-fills extraction form.
- Gap dates automatically select retry-missing mode; complete dates select rerun.
- Normal mode skips accepted dates before browser startup. Rescan refreshes the
  current live Dimensions snapshot, reuses existing details by conversation ID,
  and fetches newly discovered or unresolved conversations without duplicates.

### Evaluate

Controls:

- Single date or date range
- Normal evaluation
- Retry recorded errors
- Force reevaluation
- Request delay
- Maximum images
- Thinking level
- Advanced text-model route
- Advanced vision-model route
- Start and stop

Provider health display:

- Gemini configured/not configured
- Groq configured/not configured
- Cloudflare token/account configured/not configured
- OpenRouter configured/not configured
- Ollama online/offline
- Installed Ollama models

Do not display API keys or raw secret values.

Gemini account pool behavior:

- Settings accepts multiple Gemini accounts with arbitrary label and optional email.
- Keys remain separate Windows Credential Manager entries; account metadata stores
  only label, email, priority, and enabled/configured state.
- Route expansion is model-first: Gemini model A/account 1, model A/account 2,
  then model B/account 1, model B/account 2, before non-Gemini fallback.
- Daily quota or authentication failure disables only affected Gemini account for
  current worker run. Other Gemini accounts continue.
- Google input safety blocks remain `REVIEW_REQUIRED`; they never move to another
  Gemini account or provider as a policy-bypass attempt.
- Multiple keys add capacity only when their Google AI Studio projects/accounts
  have separate quota scopes. Keys from same project can share quota.
- Each provider request has a configurable hard timeout (default 90 seconds).
  A timeout records `request_timeout`, disables only that account/model pair for
  current run, and immediately tries next Gemini account without retrying the
  hanging account first. Daily quota/auth failures still disable whole account.
- Overview shows active account/model request and configured timeout while waiting.

Evaluation ledger:

- Defaults to latest month containing evaluator history; falls back to latest
  month with MVP 1 output.
- Tracks active conversations, successful results, pending items, errors,
  coverage, decision counts, stop reason, stale ignored results, and model mix.
- Distinguishes waiting for input, not started, partial input, running, complete,
  paused by quota, needs retry, and incomplete.
- Clicking a date pre-fills evaluator form and selects retry-errors when active
  errors exist.
- Date details show recent evaluator runs, individual provider/model attempts,
  and active errors.

MVP 2 history is append-only in `review_state.db`:

```text
evaluation_runs
evaluation_attempts
```

Every provider/model call is recorded, including invalid JSON, semantic retry,
rate-limit/server retry, fallback, success, preflight manual review, and pipeline
error. Existing rows are imported once into a synthetic `legacy_import` run.
Raw prompts, images, and raw model output are not duplicated in history tables.

### Reports

Controls and information:

- Month picker
- Build report
- Existing report list
- Open/download workbook
- Generated time
- Unsafe row count
- Manual-review row count
- Warning count
- Clear message when workbook is locked by Excel

Report readiness behavior:

- No MVP 1 date folders: report launch disabled with extraction-first guidance.
- MVP 1 folders but no evaluator results: report launch disabled with evaluation-first guidance.
- Evaluator result files available: report launch enabled.
- Canonical workbook open/locked in Excel: report still succeeds by creating a
  timestamped versioned `.xlsx`; locked canonical workbook is never overwritten.
- Every successful report writes `report_build_YYYY-MM.json` under
  `dimensions_output/monthly_reports/` with canonical/generated paths, row counts,
  warnings, and lock-fallback status.

### Worker Logs

- Poll latest log content while worker runs
- Follow latest lines automatically
- Pause/resume auto-scroll
- Copy log
- Download log
- Preserve logs after process exits

Terminal formatting is for display only. Application state comes from structured
status JSON and process metadata, never by parsing log messages.

## Planned API

```text
GET  /api/health
GET  /api/workers
GET  /api/workers/{worker_id}
POST /api/workers/extract
POST /api/workers/evaluate
POST /api/workers/report
POST /api/workers/{worker_id}/stop
POST /api/workers/{worker_id}/continue
GET  /api/workers/{worker_id}/log
GET  /api/providers
GET  /api/dates
GET  /api/evaluation-status
GET  /api/evaluation-status/{date}
GET  /api/evaluation-status/{date}/history
GET  /api/evaluation-status/{date}/errors
GET  /api/reports
GET  /api/reports/{month}/download
```

Future endpoint, not first version:

```text
POST /api/workers/full
```

Request bodies use validated fields. Browser cannot send arbitrary executable or
shell command strings.

Example evaluation request:

```json
{
  "date": "2026-09-10",
  "mode": "retry_errors",
  "delay": 4.2,
  "max_images": 12,
  "thinking_level": "low"
}
```

## Logs and Runtime State

Planned log location:

```text
dimensions_output/logs/
```

Planned server-owned worker registry should survive browser refresh. First
version may keep live process handles in memory, but log and status files remain
on disk. On server restart, previous processes are shown as historical/unknown
rather than incorrectly claimed as controllable.

## Security Rules

- Bind FastAPI and Vite to `127.0.0.1`.
- Never send API keys, environment values, Dimensions tokens, browser cookies, or
  Authorization headers to client.
- Browser code never reads `.env` or operating-system environment variables.
- Do not expose arbitrary filesystem browsing.
- Report downloads must remain under configured report directory.
- Logs should redact secrets if subprocess output ever includes them.
- Validate dates, months, numeric limits, route strings, and worker modes server-side.
- Reject path traversal and shell metacharacters by building subprocess argument
  arrays without `shell=True`.

Provider credentials are stored as Windows Generic Credentials under targets:

```text
DimensionsAutoEvaluate/GEMINI_API_KEY
DimensionsAutoEvaluate/GROQ_API_KEY
DimensionsAutoEvaluate/CLOUDFLARE_API_TOKEN
DimensionsAutoEvaluate/CLOUDFLARE_ACCOUNT_ID
DimensionsAutoEvaluate/OPENROUTER_API_KEY
```

Settings API returns only booleans. Credential values are read by FastAPI and
injected into MVP 2 subprocess environment. They are never returned to React,
written to app configuration, or included in worker metadata.

## One-Click Launcher

Planned command:

```powershell
.\Frontend\start_frontend.ps1
```

Launcher responsibilities:

1. Verify Python, Node, and npm.
2. Create server virtual environment if missing.
3. Install server requirements if missing/outdated.
4. Run `npm install` when client dependencies are missing.
5. Start FastAPI on localhost.
6. Start Vite on localhost.
7. Wait until both are healthy.
8. Open browser automatically.
9. Stop child processes when launcher exits.

Expected development addresses:

```text
Client: http://127.0.0.1:5173
API:    http://127.0.0.1:8000
```

Current launch command:

```powershell
.\Frontend\start_frontend.ps1
```

The first launch creates `Frontend/server/.venv`, installs server dependencies,
installs all MVP 1/2/3 Python dependencies and Playwright Chromium into that same
virtual environment, and uses npm dependencies under `Frontend/client/node_modules`.

The API exposes `/api/dependencies`. Extract, Evaluate, and Report launch buttons
are disabled with an explicit missing-package message when their runtime is not
ready. The launcher health check also rejects an outdated/incomplete server and
restarts it instead of silently reusing old code.

### One-Click Desktop Launcher

Install desktop shortcuts once by double-clicking:

```text
Frontend/Install Desktop Shortcut.vbs
```

Or run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\Frontend\install_desktop_shortcut.ps1"
```

Two shortcuts are created:

```text
Dimensions Auto Evaluate
Stop Dimensions Auto Evaluate
```

Double-clicking the start shortcut launches FastAPI and Vite hidden, waits for
health, then opens the default browser. If the app is already healthy, it only
opens the browser and does not start duplicates.

Runtime files and logs:

```text
%LOCALAPPDATA%\DimensionsAutoEvaluate\runtime\frontend.json
%LOCALAPPDATA%\DimensionsAutoEvaluate\logs\launcher.log
%LOCALAPPDATA%\DimensionsAutoEvaluate\logs\api.log
%LOCALAPPDATA%\DimensionsAutoEvaluate\logs\api-error.log
%LOCALAPPDATA%\DimensionsAutoEvaluate\logs\ui.log
%LOCALAPPDATA%\DimensionsAutoEvaluate\logs\ui-error.log
```

Use the Stop shortcut before moving/deleting the project folder or when you want
both local servers shut down. Closing the browser tab alone does not stop them.

Production-like local mode may later build Vite assets and serve them through
FastAPI as one process. Development mode remains two processes.

## Testing Plan

Server tests:

- Request validation
- Worker command construction
- Duplicate/conflicting worker rejection
- Concurrent independent-worker allowance
- Stop behavior
- Status-file reading
- Report path restrictions
- API-key redaction
- Process exit and error handling

Client tests:

- Dashboard loading, empty, error, and running states
- Extract/evaluate/report form validation
- Worker launch and stop interactions
- Polling updates
- Log viewer behavior
- Provider health rendering
- Locked-workbook errors

Manual smoke test:

1. Launch local app.
2. Start MVP 1 extract-only.
3. Start MVP 2 evaluate-only for a different date.
4. Confirm both run concurrently.
5. Start MVP 3 report-only.
6. Confirm three independent status files and logs.
7. Stop a test worker and confirm only that worker stops.

## Delivery Order

1. Create FastAPI server and health endpoint.
2. Add worker manager, logs, locks, and status reading.
3. Scaffold React/Vite/TypeScript client with npm.
4. Build dashboard and independent worker forms.
5. Add polling and log viewer.
6. Add provider/date/report discovery.
7. Add one-click PowerShell launcher.
8. Run independent-worker browser smoke test.
9. Add full-flow button only after independent UI is stable.

## Deferred Features

- Full MVP 1 -> MVP 2 -> MVP 3 launch button
- Scheduled/background evaluator polling
- Provider quota/reset dashboard
- Manual-review queue and conversation browser
- Video evaluation path
- Notifications
- MVP 4 Dimensions submission
- Electron/native desktop packaging

Independent MVP controls remain available even after full-flow mode is added.

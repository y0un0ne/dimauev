# Release Notes

## 1.0.1

- Fixed startup failure when launching the windowed executable without a console.
- Added Explorer-style packaged startup and frozen-worker regression checks.
- Windowed runtime output is written to `%LOCALAPPDATA%\DimensionsAutoEvaluate\logs\application.log`.

## 1.0.0

- First dependency-free Windows release.
- Independent extraction, evaluation, and reporting workers.
- Bundled Python runtime, React frontend, and Playwright Chromium.
- Per-user LocalAppData storage and Windows Credential Manager integration.
- Explicit cloud-processing consent and graceful worker shutdown.

This build may be unsigned. Verify checksums before installation and follow organizational endpoint policy.

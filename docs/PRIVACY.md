# Privacy and Local Data

The app stores conversation text, images, evaluator results, SQLite databases, reports, browser session data, and logs locally under `%LOCALAPPDATA%\DimensionsAutoEvaluate`.

AI provider keys are stored in Windows Credential Manager. Raw keys are not returned to the browser UI or stored in project files.

The browser profile contains authenticated Dimensions cookies and session data after login. The app does not separately collect your Dimensions password, but the persistent browser session may allow access without entering it again. Do not upload, email, or sync the app-data directory unless explicitly authorized.

## Cloud processing

Evaluation sends conversation text and, when configured, selected conversation images to the AI providers you enable: Google Gemini, Groq, Cloudflare Workers AI, OpenRouter, or a locally configured Ollama service. Those providers may process or retain data under their own terms and privacy policies. Review provider policy and organizational authorization before evaluation. The app requires confirmation of this transfer for every evaluation run.

Evaluation and report generation may download media from URLs saved during extraction. This contacts the host serving that media. Report generation does not call an AI model, but it may still need network access for saved media URLs.

Logs and evaluator result files can contain conversation identifiers, provider names, model names, errors, and generated classifications. Treat the full app-data directory as sensitive.

## Deletion

Uninstall preserves local data to prevent accidental loss. To remove it, delete `%LOCALAPPDATA%\DimensionsAutoEvaluate` and remove entries beginning with `DimensionsAutoEvaluate/` from Windows Credential Manager. This is irreversible.

MVP 1-3 are read-only toward Dimensions and do not submit reviews.

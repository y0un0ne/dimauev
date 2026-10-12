# Dimensions AI Evaluator — MVP 2.6

> **Current behavior:** MVP 2.6 now supports Gemini plus OpenAI-compatible
> Groq, Cloudflare, OpenRouter, and Ollama targets. Use `README.md` for current
> route syntax and `--retry-errors`. Historical Gemini-only notes below remain
> useful for baseline comparison, not current defaults.

## Goal

MVP 2.6 returns to the evaluation behavior that worked well in MVP 2.1:

- **one conversation = one Gemini evaluation**
- full multi-turn conversation context is still sent
- only assistant output is judged
- image conversations remain supported
- videos still go to `REVIEW_REQUIRED`
- no review is submitted to Dimensions

The only major addition is a **Gemini multi-model fallback router**.

---

## Default model route

Both text and image conversations use:

```text
gemini-3.5-flash-lite
        ↓ only if unavailable / daily quota exhausted / repeated failure
gemini-3.1-flash-lite
        ↓
gemini-2.5-flash-lite
```

`gemini-3.5-flash-lite` remains first because that is the model already validated
manually in the previous MVP 2.1 workflow.

There is **no batching**.

---

## Important behavior

### Temporary RPM / server error

The same model is retried first.

```text
temporary 429 / server issue
→ wait
→ retry same model
```

This avoids wasting another model's daily quota just because the current model
temporarily hit its per-minute limit.

### Daily quota exhausted

```text
3.5 daily quota exhausted
→ disable 3.5 for the rest of this run
→ switch to 3.1
```

The disabled model is not retried for every subsequent conversation.

### Authentication error

HTTP 401/403 is treated as fatal and stops the run immediately.

The SDK client explicitly uses:

```python
genai.Client(api_key=..., vertexai=False)
```

so this workflow stays on the Gemini Developer API / Google AI Studio path.

### All models exhausted

The run stops cleanly. Remaining conversations stay pending and can be resumed
later **without `--force`**.

---

# 1. Install

```powershell
py -m pip install -r requirements_dimensions_mvp2_6.txt
```

Make sure the new working Google AI Studio key is set:

```powershell
$env:GEMINI_API_KEY="YOUR_WORKING_KEY"
```

Do not paste the key into logs or chat.

---

# 2. Recommended benchmark

Keep the original validated `ai_reviews` untouched.

```powershell
py dimensions_ai_evaluator_mvp2_6.py `
  --date 2026-07-01 `
  --limit 50 `
  --force `
  --output-subdir ai_reviews_router_benchmark_v26
```

Expected header:

```text
Mode              : exactly 1 conversation per Gemini request
Text route        : gemini-3.5-flash-lite -> gemini-3.1-flash-lite -> gemini-2.5-flash-lite
Vision route      : gemini-3.5-flash-lite -> gemini-3.1-flash-lite -> gemini-2.5-flash-lite
```

A normal result should look like:

```text
[001/050] <conversation_id> -> SAFE [gemini-3.5-flash-lite fallback=0]
```

or:

```text
[012/050] <conversation_id> -> UNSAFE | Misinformation [gemini-3.5-flash-lite fallback=0]
```

---

# 3. Compare against the old validated MVP 2.1 results

Use the included comparison script:

```powershell
py dimensions_compare_reviews.py `
  --baseline ".\dimensions_output\2026-07-01\ai_reviews" `
  --candidate ".\dimensions_output\2026-07-01\ai_reviews_router_benchmark_v26" `
  --out ".\dimensions_output\2026-07-01\mvp21_vs_mvp26.csv"
```

The most important line is:

```text
baseline UNSAFE -> candidate SAFE
```

Those conversation IDs should be manually inspected first.

The comparison also reports:
- overall decision agreement
- SAFE/UNSAFE/REVIEW_REQUIRED transitions
- category agreement when both are UNSAFE
- unsafe message-ID agreement
- model used by the candidate

---

# 4. Production / historical resume

After the benchmark is acceptable, run a date normally:

```powershell
py dimensions_ai_evaluator_mvp2_6.py --date 2026-07-02
```

Default output is:

```text
dimensions_output/2026-07-02/ai_reviews
```

Do not use `--force` for a normal resume.

Successful existing results are skipped. Error/pending conversations are retried.

---

# 5. Request pacing

Default:

```text
--delay 4.2
```

This is intentionally conservative for the 15 RPM limit visible on the main
Flash Lite models in the current AI Studio project.

If you later change plans or quotas, it can be overridden:

```powershell
--delay 5
```

or:

```powershell
--delay 3
```

A lower value may trigger temporary 429 responses; MVP 2.6 retries temporary
rate-limit errors before falling back.

---

# 6. Custom routes

Example, use only 3.5 and 3.1:

```powershell
py dimensions_ai_evaluator_mvp2_6.py `
  --date 2026-07-02 `
  --text-models "gemini-3.5-flash-lite,gemini-3.1-flash-lite" `
  --vision-models "gemini-3.5-flash-lite,gemini-3.1-flash-lite"
```

The defaults are recommended until the benchmark is complete.

---

# 7. Output metadata

Each result records:

```text
evaluator_provider
evaluator_model
route_kind
fallback_level
```

This allows later auditing by model.

Example:

```json
{
  "evaluator_provider": "gemini",
  "evaluator_model": "gemini-3.5-flash-lite",
  "route_kind": "text",
  "fallback_level": 0
}
```

---

## Safety

MVP 2.6 is still **DRY RUN only**.

It does not:
- click Mark Safe
- click Mark Unsafe
- select a Dimensions category
- submit a review
- call `submitreviews`

Actual Dimensions mutation remains an MVP 4 task.

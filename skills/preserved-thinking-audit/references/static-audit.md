# Code-reading brief (give this to one sub-agent per harness)

You are auditing one agent harness, cloned at `<path>`, for readiness for preserved thinking on Claude Fable 5.1. This pass reads the code and changes nothing in the clone: use grep, file reads, and `git log`. Its output is the two files named under Output. They feed the report's action items for the harness's maintainer, so every `fail` and `latent` row needs evidence the maintainer can open: a path, a line, and a short quote. Read `references/mechanics.md` and `references/checks.md` first and judge the code against those rules, not intuition.

## 1. Find the request path
How does it reach Claude: an Anthropic SDK, a multi-provider SDK or abstraction layer, an OpenAI-compatible shim or gateway, a cloud provider's SDK, or raw HTTP? List every path. Note the base-URL setting and auth options (API key, OAuth, cloud provider, gateway). If there is no Anthropic Messages path, say so and stop early.

## 2. Answer with `path:line` evidence and a short quote
- **Config (CFG-1..5):** thinking config sent; temperature/top_p/top_k; prefill; `tool_choice`; any use of `block_binding`, `prefix_mismatch_behavior`, `input_transformations`, or the `thinking-binding-controls-2026-08-01` header.
- **Fidelity (FID-1..4):** is assistant content stored raw or converted to an internal model? Are `thinking`/`redacted_thinking` blocks and `signature` kept? Any filter on empty thinking text? Any "keep last N reasoning" logic? Is block order preserved through persistence?
- **System (PFX-1):** what goes into top-level `system`, and is it rebuilt per request? Quote every volatile input.
- **Tools (PFX-2):** when is the tool list built? MCP timing, modes, toggles, `defer_loading`, `tool_addition`/`tool_removal`.
- **History (PFX-3, 4, 5):** reminders injected and removed; tool-result truncation; image pruning; sliding window; compaction (what exactly is kept, sync or background, mid tool round?).
- **Resume (PFX-6):** what is persisted (raw request pieces or re-rendered)? What happens on version upgrade?
- **Side requests (PFX-7):** summarizer, title, sub-agent: do they receive thinking blocks?
- **Model switch (MDL-1):** can the model change mid-session; is thinking stripped on switch?
- **Errors (ERR-1):** what happens on a 400 `invalid_request_error`; any match on ``Invalid `signature` ``; retry policy: does it resend the same body?
- **Mid-conversation system messages:** can the message model hold `role: "system"` inside `messages`? `READY | EASY | REFACTOR`, one line why.
- **Scenario handles:** commands or settings that force compaction, switch model, switch mode, resume a session, run headless.

## 3. Output
Write `~/pt-audit/<harness>/static.md` (full notes) and `~/pt-audit/<harness>/static.json`:

```json
[{"check_id": "PFX-1", "status": "fail", "evidence": "src/prompt/system.ts:41  `Today's date: ${new Date().toDateString()}`", "note": "date re-rendered per request"}]
```

One row per check ID in `checks.md`. Status is `pass`, `fail`, `latent` (edit exists but thinking is stripped before replay), `not_exercised` (could not determine), or `info`. Each note is one factual sentence, because it fills a table cell in the report. Mark anything you did not verify in the code with the word "likely". Final reply: the JSON, the wiring instructions, the scenario handles, and the harness commit SHA.

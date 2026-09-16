# How preserved thinking works (auditor's notes)

Everything here is a summary of two public pages. Re-read them at the start of an audit, because field names and beta headers change, and the pages win over this file:

- https://platform.claude.com/docs/en/build-with-claude/preserved-thinking
- https://platform.claude.com/docs/en/build-with-claude/thinking-troubleshooting (the "thinking block signature is invalid" section)

## The two checks

Starting with Claude Fable 5.1, when a `thinking` or `redacted_thinking` block comes back in a request, the API reads its `signature` and checks two things.

| | Model check | Prefix check |
|---|---|---|
| Question | Can this model read a block that model produced? | Is everything before the block unchanged since it was produced? |
| Rule | A model reads its own blocks and those of earlier models. Claude Fable 5.1 reads blocks from Claude Opus 5; Claude Opus 5 can't read blocks from Claude Fable 5.1. | The top-level `system`, the set of `tools`, and every message before the block must match what was sent when the block was produced. One failure invalidates that block and every later thinking block. |
| On failure | Block dropped for that request. Never an error. Not configurable. | `"error"` (default): 400. `"drop_block"`: the failing block and all later thinking blocks are dropped, and the request succeeds. |
| Report (under the beta header) | `input_transformations[].reason = "model_binding_mismatch"` | `"prefix_binding_mismatch"` |
| Applies to | Every account | Accounts created on or after August 31, 2026, 00:00 UTC; any request that sets `prefix_mismatch_behavior`; every account on later models |
| Permanent? | No. The API never edits your `messages`. Send the same history back to the newer model and its blocks are read again. The reasoning is lost for good only if the client strips the blocks. | The invalid blocks stay invalid. Thinking produced after the edit is bound to the new prefix and is fine. |

Earlier thinking blocks aren't in the prefix, but each thinking block records which thinking block came before it, across turns. You can remove thinking blocks from the start of the history (oldest first), from the end, or all of them. A gap fails: removing one from the middle invalidates the thinking blocks after it. Once a block is removed, putting it back invalidates the blocks produced while it was gone.

With server-side compaction, the checked prefix starts at the most recent compaction block.

## Request and response surface

- Beta header: `anthropic-beta: thinking-binding-controls-2026-08-01`. It adds the `block_binding` object and the `input_transformations` array.
- Request: `thinking: {type: "adaptive", block_binding: {prefix_mismatch_behavior: "error" | "drop_block"}}`. Sending `block_binding` without the header returns a 400 whose message ends `block_binding: Extra inputs are not permitted`. Models that don't run the prefix check accept the object and report only model-check drops, so one request body works across models.
- Setting the field opts an older account into enforcement for that request. That is how the proxy arms the check, and it is why a clean run on an older key proves nothing unless the field is set.
- Response, under the header: top-level `input_transformations: [{type: "thinking_dropped", path: "messages.3.content.0", reason}]`, and `[]` when nothing was dropped. When streaming it arrives on the `message` object in `message_start`; after a mid-stream server-side fallback the final `message_delta` carries it again. Ignore `type` or `reason` values you don't recognize.
- The 400 begins: ``messages.{i}.content.{j}: Invalid `signature` in `thinking` block. The block is bound to a different conversation. Remove the block, or set `thinking.block_binding.prefix_mismatch_behavior` to "drop_block".`` If the beta header was not sent, it continues: ``That setting requires the `thinking-binding-controls-2026-08-01` value in the `anthropic-beta` header.`` It usually ends with a sentence naming what changed. That sentence is for people and logs; its wording can change, so don't match on it in code. Match on the leading clause.
- If the message stops after ``Invalid `signature` in `thinking` block``, the signature itself didn't verify (truncated, altered, or sent back empty). That is a different failure and `prefix_mismatch_behavior` doesn't apply to it.
- Retrying the same body fails the same way every time.
- Message Batches: an item that leaves the field unset drops failing blocks instead of erroring. The token counting endpoint runs the same check and returns the same 400.
- Dropped blocks aren't billed and don't count toward `input_tokens`. After a prefix drop the prompt cache restarts at the edit.

## What counts as an edit

Valid between two consecutive requests:

- Appending messages at the end.
- Adding a tool with `defer_loading: true` that nothing has referenced yet.
- Removing thinking blocks from the start of the history, from the end, or all of them.
- Changing any request parameter outside `system`, `tools`, and `messages`: `effort`, `max_tokens`, `output_config`, `tool_choice`, `metadata`, `thinking.display`, and so on.
- Adding, moving, or removing `cache_control` markers.
- A rotating signed URL that returns the same bytes.
- Server-side compaction or context editing (the check compares what you sent, not the server's edited copy).
- A cleared turn-scoped system message left in place.

Invalid:

- Editing, reordering, or deleting any earlier `user`, `assistant`, or `system` message.
- Re-rendering the context in the first user message with a changed value.
- Clearing or shortening an earlier `tool_result`, re-encoding an earlier image, or changing an earlier `tool_use` input.
- Adding a text block to an earlier user turn, or removing one you added last time.
- Changing the top-level `system` string or blocks.
- Adding, removing, renaming, or editing a tool in `tools`.
- Removing a thinking block from the middle of the history and keeping later ones, or putting back one you removed earlier.
- An image or document URL that returns different bytes on the next request.
- Deleting or rewording a turn-scoped system message on a later request.

JSON formatting and key order don't matter; the values do. The docs also note that the edits that invalidate thinking are the edits that restart the prompt cache, which makes a falling `cache_read_input_tokens` a useful second signal.

`scripts/pt_lib.py` builds its local diff from these lists and nothing else. It is a reading of the docs, not the API's implementation. Where the local diff and the API disagree, the API is right; the analyzer records the disagreement as a `RECON` entry so you can find out why.

## Replacements that keep the prefix intact

| Instead of | Use | Beta header |
|---|---|---|
| Rebuilding top-level `system` | A mid-conversation `role: "system"` message, appended where the change becomes true, then left in place | none |
| Re-rendering the context in the first user message | Render it once and resend it unchanged; put the new value in the newest turn | none |
| Clearing old `tool_result` content or re-encoding old images in place | Shorten before the first send; to clear later, server-side context editing with `clear_tool_uses_20250919` | `context-management-2025-06-27` |
| Injecting a reminder and deleting it next turn | A `role: "system"` message with `clear_at: "next_user_message"`, placed after the `tool_result` message and left in place | `mid-conversation-system-clear-at-2026-08-21` |
| Editing `tools` | Declare the full set on request 1; late tools go in with `defer_loading: true` and are offered with a `tool_addition` block; withdraw with `tool_removal` | `mid-conversation-tool-changes-2026-07-01` |
| Changing top-level effort (valid for thinking, restarts the cache) | `{"role": "system", "content": [], "output_config": {"effort": "low"}}` | `mid-conversation-output-config-2026-07-01` |
| Dropping or summarizing old turns on the client while keeping recent ones | On-demand compaction so the API writes the summary; other server-side compaction or context editing (`clear_thinking_20251015`); or simple compaction (one summary message, nothing else replayed); or strip thinking from the kept turns | `compact-2026-09-04` for on-demand compaction |
| A URL whose bytes change | A Files API `file_id`, or base64 | none |

Never place a system message between an assistant `tool_use` and its `tool_result`. Don't compact in the middle of a tool round. Mid-conversation system messages and tool changes aren't available on every model; the docs list which models accept them.

For a library, proxy, or gateway: forward the caller's `anthropic-beta` values and `thinking.block_binding` unchanged, return `input_transformations` to the caller, leave a `role: "system"` message where the caller put it, turn tools off with `tool_choice: {"type": "none"}` rather than removing `tools`, and don't hide the 400.

## Other Claude Fable 5.1 request rules a harness must meet first

These come from the thinking troubleshooting page and the Claude Fable 5.1 migration guide (https://platform.claude.com/docs/en/models/fable-5-1/migration-guide). A harness that fails them never gets far enough to exercise the prefix check, so the proxy repairs them, keeps the session going, and records each repair as a CFG finding.

- `thinking.type: "enabled"` with `budget_tokens`, and `thinking.type: "disabled"`, return a 400. Thinking is adaptive and always on.
- Non-default `temperature`, `top_p`, or `top_k` returns a 400.
- `tool_choice` of type `tool` or `any` returns a 400.
- A trailing assistant message (prefill) returns a 400.
- Thinking `display` defaults to `"omitted"`, so `thinking` blocks arrive with an empty `thinking` field and a populated `signature`. A harness that filters "empty" thinking blocks is stripping real ones. A block sent back with an empty `signature` fails.

## What dropping costs

Measure it, don't assume it. After a drop the model answers that turn without the lost reasoning, and the prompt cache restarts at the edit. A harness that always strips thinking pays this on every turn. Report what the runs show: `response_thinking_rate`, `output_tokens_per_response`, `cache_read_share`, and task outcome, comparing `drop_block` against `strip` against a clean run.

## Practical traps

- Some client libraries expect the base URL to end in `/v1`. The Anthropic SDKs do not. `references/harness-wiring.md` has the test.
- A harness that talks to Claude through an OpenAI-compatible endpoint or a gateway that translates to a chat-completions shape usually loses the signature in translation. That is FID-1 by construction. Confirm it in the code; the proxy can't see those routes.
- Some agent sandboxes give each shell command its own network namespace, so a proxy started in one command is unreachable from the next. Use `scripts/pt_run.sh`, which keeps the proxy and the harness in one process tree.
- The proxy honours `HTTPS_PROXY` for its upstream connection, including credentials in the URL. It does not read `NO_PROXY`.

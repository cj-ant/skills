# Check catalog

Each check names one bad habit, how to trigger it in a live session, what the analyzer looks for, the fix, and how to verify the fix. Short habit/fix/verify strings live in `scripts/pt_checks.py` and appear in the report; this file adds the trigger and the reasoning. Statuses: `pass`, `fail`, `latent` (edit seen, no thinking replayed after it, so the API had nothing to reject), `not_exercised`, `info`.

## Prefix stability

**PFX-1 Volatile system prompt.** The most common failure. Look for date/time, cwd, git branch or status, directory listing, OS info, token or cost counters, todo state, memory files, and instruction files (AGENTS.md, CLAUDE.md) re-read per request. Trigger: three or more turns that cross a minute boundary; edit a tracked file and AGENTS.md mid-session; `git checkout -b` mid-session. Detect: `system` digest differs between a request and its parent; the evidence shows the first differing characters. Fails when thinking was replayed on that request. Fix: freeze at session start, append a mid-conversation system message for changes. Verify: identical digest across the run; zero `system_changed` drops.

**PFX-2 Volatile tools.** MCP servers that connect after request 1, reconnect, or re-describe tools; plan/act or build/plan modes with different tool sets; user toggles; tool descriptions that embed cwd or dates. Trigger: switch modes mid-session; add or restart an MCP server mid-session; start with a slow MCP server. Detect: inline tools digest differs from parent; evidence lists added, removed, and changed names with the changed fields. Deferred-only changes are fine. Fix: wait for servers before request 1; declare late tools `defer_loading: true` and offer with `tool_addition`; `tool_removal` for mode restrictions. Verify: inline digest constant.

**PFX-3 Edits to earlier messages.** Insert-then-strip reminders (environment details, batching nudges, "todo list is empty" notes), truncating or clearing old tool results, pruning old screenshots, collapsing or normalizing history, removing orphaned tool calls, retry that rewrites an interior turn. Trigger: a long tool-heavy session (10+ tool calls with large outputs), at least one image, at least one denied or failed tool call. Detect: alignment against the parent shows `messages_modified`, `messages_removed`, `messages_inserted`, or `messages_rewritten`; hints name reminder removal, tool_result truncation, image removal. Fix: leave sent bytes alone; turn-scoped system messages for reminders; server-side context editing for trimming. Verify: no such events.

**PFX-4 Sliding window.** "Keep the last N messages." Trigger: enough turns to exceed N, or a low context setting. Detect: `head_removed`. Fix: compaction to a single summary, or server-side compaction. Verify: none.

**PFX-5 Compaction that keeps stale thinking.** Keep-tail (summary plus recent turns verbatim), background summarize-then-swap, reactive compaction keeping the last round. Trigger: the harness's compact command, or a lowered auto-compact threshold; mark `compaction`. Detect: head or span replaced by one or two messages, and thinking replayed after the replacement. Passes when compaction happened and nothing stale followed. Fix: single summary message; or strip thinking from kept turns; or server-side compaction; do not compact mid tool round. Verify: zero drops and no 400 on the first request after compaction.

**PFX-6 Resume re-renders.** Restart the harness and continue the session; mark `resume` first. Detect: the first request after the marker differs from its parent in `system`, `tools`, or messages (thinner transcript, reordered blocks, re-serialized attachments). Also catches upgrade-then-resume when prompt or tool text changed between versions. Fix: persist the rendered system, tool definitions, and raw assistant content; replay them byte for byte. Verify: full alignment.

**PFX-7 Thinking leaves its conversation.** Summarizer, title, checkpoint, memory-extraction, and sub-agent requests that send the main history, thinking included, under a different `system` or `tools`. On an enforced account the side request itself 400s. Trigger: compaction, first-turn title generation, any sub-agent spawn. Detect: a request with no parent lineage that replays thinking minted elsewhere, or a local `system_changed` verdict on blocks minted in another request. Fix: strip thinking from any history handed to a side request. Verify: side requests carry no thinking.

**PFX-8 URL media.** Informational. Trigger: attach an image by URL if the harness supports it. Fix: `file_id` or base64.

## Replay fidelity

**FID-1 Thinking replayed with signature.** Detect: for each response with thinking, does the next request in the lineage carry the same signatures (`client_keep_rate`)? Is thinking that was in the history on the previous request still there on this one (`history_keep_rate`)? What is left when a new user turn starts (`cross_user_turn_keep_rate`)? A harness can keep blocks inside a tool loop, where its in-memory transcript is raw, and lose them all at the next user turn, where it rebuilds messages from its own storage format. Common causes: internal message model stores reasoning as plain text; gateway translation drops provider metadata; explicit strip "to save tokens". All-stripped is also LZY-2.

**FID-2 Empty-text thinking kept.** Fable 5.1 returns `thinking: ""` by default. Filters like `if block.thinking` remove real blocks; if some survive and some do not, the survivors hit the predecessor chain. Trigger: default display. Detect: empty-text blocks minted vs echoed.

**FID-3 Verbatim assistant echo.** Reordered parallel `tool_use` blocks, merged or trimmed text blocks (interior whitespace matters), re-serialized tool input strings, dropped unknown block types. Detect: `assistant_echo_differs` with a block-level diff, or `blocks_reordered`. Trigger: prompts that produce parallel tool calls and text between calls; persistence and reload.

**FID-4 No interior holes.** Includes merging several thinking blocks of one response into one (a message model with a single reasoning field per assistant turn): the survivor's predecessor is missing and the API drops it on every later request. "Keep thinking only on the last K assistant turns" done by deleting from the middle, or dropping one turn's thinking. Detect: local `predecessor_mismatch`.

## Model switching

**MDL-1 History survives a model switch.** Trigger: switch to another Claude model for a turn, then back; mark `model_switch`; run the proxy with `--force-model ''` so the switch reaches the API. Detect: blocks present before the switch and absent after, removed by the client. Fix: never strip on switch.

**MDL-2 Model-check drops tolerated.** Informational: entries with `model_binding_mismatch` are expected on a downgrade.

## Error handling

**ERR-1 Recovery from the signature 400.** Run `error` mode with at least one known edit. Detect what follows the 400: `blind_retry_same_body` (fail; the same body fails the same way every time), `no_further_request` (fail: dead session), `started_new_conversation` (fail: lost context), `retried_with_drop_block` or `stripped_thinking_and_retried` (pass). Also read the UI: does the user see an actionable message?

**ERR-3 count_tokens.** Informational when the token-count call returns the same 400.

## Shortcuts

**LZY-1 drop_block as a permanent crutch.** The harness sets `drop_block` itself and the API keeps dropping. Report blocks dropped, requests with drops, and the share of replayed thinking lost. Needs an `observe` run so the harness's own setting is visible.

**LZY-2 Strip everything.** No thinking ever returns. Requests succeed and the check never fires. Report as MASKED, list latent prefix edits, and quantify with a `strip` vs `drop_block` comparison when possible.

## Request config (Fable 5.1 blockers, repaired by the proxy and reported)

**CFG-1** budgeted or disabled thinking. **CFG-2** sampling params. **CFG-3** forced `tool_choice`. **CFG-4** prefill. **CFG-5** `block_binding` sent without the beta header. Confirm the fixed harness with a `--no-repair` run.

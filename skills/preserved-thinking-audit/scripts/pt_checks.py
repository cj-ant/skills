"""Check catalog: the bad habit, the fix, and how to verify the fix. references/checks.md is the long form."""

from __future__ import annotations

DOCS = "https://platform.claude.com/docs/en/build-with-claude/preserved-thinking"

CATALOG: dict[str, dict[str, str]] = {
    "CFG-1": {
        "habit": "Sends thinking.type \"enabled\" with budget_tokens, or disables thinking.",
        "fix": "Send thinking: {type: \"adaptive\"} and steer depth with output_config.effort.",
        "verify": "Run with --no-repair: the first request returns 200 and the log shows no repair_budgeted_thinking mutation.",
    },
    "CFG-2": {
        "habit": "Sets temperature, top_p, or top_k on the request.",
        "fix": "Omit sampling parameters for Claude Fable 5.1 (only temperature 1 is accepted).",
        "verify": "Run with --no-repair: no repair_sampling_param mutation and no 400.",
    },
    "CFG-3": {
        "habit": "Forces a tool with tool_choice {type: \"tool\"} or {type: \"any\"}.",
        "fix": "Use tool_choice auto and instruct in the prompt; for structured output use output_config.format.",
        "verify": "Run with --no-repair: no repair_forced_tool_choice mutation and no 400.",
    },
    "CFG-4": {
        "habit": "Ends messages with an assistant turn to prefill the reply.",
        "fix": "Drop the prefill; put the steer in the user turn or system prompt, or use structured outputs.",
        "verify": "Run with --no-repair: no repair_prefill mutation and no 400.",
    },
    "CFG-5": {
        "habit": "Sends thinking.block_binding without the thinking-binding-controls-2026-08-01 beta header.",
        "fix": "Always send the beta header on any request that carries block_binding.",
        "verify": "Run with --auth passthrough or --mode observe: no 400 about block_binding.",
    },
    "FID-1": {
        "habit": "Drops thinking blocks (or their signature) when it stores or replays an assistant turn.",
        "fix": "Store response.content as returned and send it back unchanged: every block type, in order, signatures included.",
        "verify": "client_keep_rate is 100% and no FID-1 evidence across a multi-turn tool session.",
    },
    "FID-2": {
        "habit": "Filters out thinking blocks whose thinking text is empty (the default display is \"omitted\").",
        "fix": "Never filter on the thinking text. An empty thinking field with a signature is a real block.",
        "verify": "empty_text_echoed equals empty_text_minted on every echoed turn.",
    },
    "FID-3": {
        "habit": "Re-serializes the assistant turn: reorders parallel tool_use blocks, merges or trims text, rewrites tool input.",
        "fix": "Persist the raw content array. Do not round-trip it through a lossy internal message model.",
        "verify": "No assistant_echo_differs or blocks_reordered evidence.",
    },
    "FID-4": {
        "habit": "Removes a thinking block from the middle of the history and keeps later ones.",
        "fix": "Only trim thinking from the oldest end (\"drop all thinking older than turn X\"), or use server-side context editing (clear_thinking_20251015).",
        "verify": "No predecessor_mismatch evidence; drop_block run shows zero drops after trimming.",
    },
    "PFX-1": {
        "habit": "Rebuilds the top-level system prompt each request (date/time, cwd, git status, token counts, todo state, re-read AGENTS.md).",
        "fix": "Freeze system at session start. Append changes as a mid-conversation role:\"system\" message at the point they become true.",
        "verify": "system digest identical across every continuation; drop_block run reports no prefix_binding_mismatch entries.",
    },
    "PFX-2": {
        "habit": "Edits tools mid-session (MCP connects late or re-lists, mode switch swaps tool sets, description text changes).",
        "fix": "Declare the full inline set on request 1 (wait for MCP servers). Add later tools with defer_loading: true plus a tool_addition block; withdraw with tool_removal.",
        "verify": "inline tools digest identical across every continuation; only deferred tools change.",
    },
    "PFX-3": {
        "habit": "Edits earlier messages: injects a reminder and strips it next turn, truncates old tool results, prunes old images, rewrites a prior turn.",
        "fix": "Leave sent messages alone. Reminders: role:\"system\" with clear_at:\"next_user_message\", left in place. Trimming: server-side context editing (clear_tool_uses_20250919).",
        "verify": "No messages_modified / removed / inserted events; drop_block run shows zero drops.",
    },
    "PFX-4": {
        "habit": "Keeps only the last N messages (sliding window).",
        "fix": "Use server-side compaction, or compact to a single summary message. Never slice the head off and keep thinking in the tail.",
        "verify": "No head_removed events.",
    },
    "PFX-5": {
        "habit": "Client-side compaction keeps recent turns verbatim, thinking included (keep-tail), or swaps a background summary in later.",
        "fix": "Summarize into one user message and replay nothing else; or strip thinking from the kept turns; or use server-side compaction.",
        "verify": "After a forced compaction: zero drops in drop_block mode and no 400 in error mode.",
    },
    "PFX-6": {
        "habit": "On resume, re-renders system and tools from current state, or rehydrates a thinner transcript than it sent.",
        "fix": "Persist exactly what was sent and received (rendered system, tool definitions, each assistant turn) and replay that. New facts go in an appended message.",
        "verify": "Mark resume, restart the harness, continue: first request after the marker aligns fully with its parent.",
    },
    "PFX-7": {
        "habit": "Side requests (summarizer, title generator, sub-agent, checkpoint) carry the main conversation's thinking under a different system prompt or tool list.",
        "fix": "Strip thinking and redacted_thinking blocks from any history that leaves its conversation.",
        "verify": "No request replays thinking minted in a different lineage.",
    },
    "PFX-8": {
        "habit": "References images or documents by a URL whose bytes can change.",
        "fix": "Upload once with the Files API and send the file_id, or send base64.",
        "verify": "No url-sourced media in requests, or the bytes are immutable.",
    },
    "MDL-1": {
        "habit": "Strips thinking from stored history when the user switches to another model.",
        "fix": "Keep sending the full history. The API drops what the current model cannot read, per request, and reads it again when the session returns.",
        "verify": "After switching away and back, the newer model's blocks are still replayed.",
    },
    "MDL-2": {
        "habit": "Treats a model_binding_mismatch entry as an integration failure.",
        "fix": "Log it and move on. It is expected on a downgrade and is not a prefix edit.",
        "verify": "Session continues normally after a model switch.",
    },
    "ERR-1": {
        "habit": "On the signature 400, crashes, surfaces a dead session, or retries the same body.",
        "fix": "Match the leading clause \"Invalid `signature` in `thinking` block\". Retry once with the beta header and prefix_mismatch_behavior \"drop_block\" (keep sending it for the session), or strip all thinking and retry once. Then fix the edit.",
        "verify": "In --mode error with a forced edit: the request after the 400 succeeds and is not byte-identical.",
    },
    "ERR-3": {
        "habit": "Ignores that count_tokens runs the same check and returns the same 400.",
        "fix": "Handle the 400 on count_tokens the same way, or count without thinking blocks.",
        "verify": "count_tokens calls succeed or are recovered.",
    },
    "LZY-1": {
        "habit": "Adds prefix_mismatch_behavior \"drop_block\" and keeps editing the prefix.",
        "fix": "drop_block is a seat belt. Fix the edit so nothing is dropped; every drop also restarts the prompt cache at the edit.",
        "verify": "With the harness's own drop_block: input_transformations stays empty for the whole session.",
    },
    "LZY-2": {
        "habit": "Strips every thinking block before sending, so the check has nothing to reject.",
        "fix": "Replay thinking. Without it the model re-derives its reasoning each turn (more output tokens, lost continuity within tool loops).",
        "verify": "client_keep_rate is 100%.",
    },
}

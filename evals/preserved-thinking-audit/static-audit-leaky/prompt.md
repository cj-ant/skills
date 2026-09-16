---
description: Static pass of the skill on a fixture harness with eight planted habits and six clean areas.
tags: [static, agent]
plugins: ["../../.."]
runs: 3
max_turns: 40
timeout_seconds: 900
allowed_tools: [Read, Glob, Grep, Skill]
---

/preserved-thinking-audit leaky-harness

Do the code-reading pass only (step 2 of the skill). The harness source is in this repo at `evals/preserved-thinking-audit/static-audit-leaky/leaky-harness`. From the skill's base directory that is `../../evals/preserved-thinking-audit/static-audit-leaky/leaky-harness`. It is readable, and it is not inside the current directory. Don't run the proxy, the harness, or any script, and don't write files.

Reply with the `static.json` array in a `json` code block: one row per check ID in the skill's `references/checks.md`, with the keys in this order: `check_id`, `status`, `evidence`, `note`. After the block, add one line naming the verdict you expect from a wire run and why.

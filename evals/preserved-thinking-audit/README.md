# Evals for preserved-thinking-audit

Three tiers. The first two are free and run offline in under a minute. Run them after every change to the skill's scripts.

| Tier | What it measures | Cost | Command |
| --- | --- | --- | --- |
| Offline analyzer | Does `pt_analyze.py` flag 18 known bad habits and leave 4 valid edits alone? | none | `python3 evals/preserved-thinking-audit/run_offline.py` |
| Offline fixture | Does a wire audit of `leaky-harness`, a small agent with planted habits, reach the right verdict? | none | same command |
| Agent | Does Claude, running the skill's code-reading pass on `leaky-harness`, find the 8 planted habits and leave the 6 clean areas alone? | model tokens | `claude plugin eval . --case static-audit-leaky` |

One more check lives in the skill itself: `scripts/pt_selftest.py` with no flags runs the same 18 habits against the live API. It needs an API key and a few dozen small requests. It is the ground truth, because `fake_api.py` is a reading of the docs and not the API.

## The offline tiers

`run_offline.py` starts `fake_api.py`, points the skill's `pt_selftest.py` at it, then drives `leaky-harness` through the real `pt_run.sh` and proxy. It prints one line per case and three scores:

```
detected 18/18 bad habits   clean 4/4 valid edits   fixtures 1/1
```

- `detected` is recall. Each bad-habit case names the checks that must come back `fail` or `latent`.
- `clean` is precision. Each `valid_*` case makes an edit the docs allow (moving `cache_control`, reordering JSON keys, trimming the oldest thinking, adding a deferred tool). A prefix or fidelity flag on one of them is the analyzer crying wolf.
- `fixtures` compares `findings.json` for the fixture harness with the `wire` section of `static-audit-leaky/expected.json`.

The run exits 1 if a score drops below `baseline.json`. After an improvement, keep it with `--update`. `--habits <name...>` runs a subset. Run logs go to a temporary directory that is deleted afterwards; `--keep <dir>` saves them.

`fake_api.py` applies the rules on the public [preserved thinking](https://platform.claude.com/docs/en/build-with-claude/preserved-thinking) page: the model check, the prefix check, the predecessor rule, the beta header, and the Claude Fable 5.1 request rules. It shares no code with the skill, so the two can disagree. `clock_shim/` moves `datetime.now()` forward one minute per call in the fixture run, because an offline session finishes before the clock in a system prompt would change.

## The agent tier

`static-audit-leaky/` is a `claude plugin eval` case. That command ships with Claude Code (v2.1.269 or later; check with `claude --version`, and `claude plugin eval --help` lists the options). The offline tiers above need nothing but Python. The prompt invokes `/preserved-thinking-audit leaky-harness` for the code-reading pass only, with read-only tools, so the run bills no test sessions and starts no third-party agent.

```bash
# one cheap run to check the case loads
claude plugin eval . --case static-audit-leaky --runs 1 --ablation none --max-cost-usd 3 --no-publish

# a scored run: 3 runs with the skill, 3 without, and the score difference
claude plugin eval . --case static-audit-leaky --max-cost-usd 15 --no-publish
```

Graders:

- 8 `regex` graders, weight 2, one per planted habit: the row for that check must be `fail` or `latent`.
- 6 `regex` graders, weight 1, one per clean area: the row must be present with a status other than `fail` or `latent`, so an empty reply scores 0.
- 4 `llm` graders, weight 1 each: the evidence row for `PFX-1`, `FID-2`, `PFX-5`, and `ERR-1` cites the right file and describes the right habit. One condition per grader, because the report shows judge votes without reasons, and a failed multi-part rubric can't be debugged. The default judge is a small model that doesn't know this domain, so each rubric lists the wordings that mean the same habit. When a judge fails a row you think is right, read the row in `report.html` before you touch the skill.
- 1 `tool_used` grader that records whether the agent read the skill's check catalog. A slash command expands the skill into the prompt without a `Skill` tool call, so the grader looks for the read.

`gen_graders.py` writes the `regex` graders from `expected.json`. Edit the ground truth, then rerun it. To grade a saved reply by hand: `python3 evals/preserved-thinking-audit/grade_static.py reply.txt evals/preserved-thinking-audit/static-audit-leaky/expected.json`.

## Hillclimbing

1. Find a gap: a harness the skill misjudged, a confusing report, or an `extra_flags` entry in `baseline.json` that looks wrong.
2. Reproduce it as a case before changing the skill. For an analyzer gap, add a habit to `HABITS` in `pt_selftest.py`, or a planted habit to a fixture harness and its `expected.json`. For a gap in how the agent reads code, add it to the fixture and rerun `gen_graders.py`.
3. Watch the new case fail. A case that passes before the fix measures nothing.
4. Fix the skill, rerun, and `--update` the baseline.
5. Before trusting a new analyzer rule, run `pt_selftest.py` against the live API. If the API disagrees with `fake_api.py`, fix `fake_api.py` to match the API.

To check that the suite can still fail, break the analyzer on purpose. Making `canon_system` in `pt_lib.py` return `[]` should drop `detected`. Emptying `IGNORED_BLOCK_KEYS` should drop `clean`.

## Where the scores stand

Offline: 18/18, 4/4, 1/1. Agent tier: 1.0 on single runs. A suite at its ceiling can only catch regressions, so the next gain comes from harder cases, not from tuning the skill against these.

## Known limits

- The offline tiers can't catch API behaviour the docs don't describe.
- The agent tier covers the code-reading pass only. Nothing here grades a full audit with live sessions and the HTML report.
- One fixture harness. A second one in another language, with a different storage model, would say more about how well the code-reading brief generalizes.

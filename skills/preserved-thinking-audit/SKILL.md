---
name: preserved-thinking-audit
description: Audit whether an agent harness (coding agent, editor extension, SDK wrapper, or gateway) is ready for preserved thinking on Claude Fable 5.1 and later models. Reads the harness source, runs billed test sessions against claude-fable-5-1 through a local recording proxy, and writes a report for the harness's maintainer: action items to fix or decide on, each with where in the code, what was seen, and how to confirm the fix. Manual only. Run it when the user types /preserved-thinking-audit or asks for this skill by name. Never start it on your own, because it spends API credits and runs a third-party agent.
disable-model-invocation: true
argument-hint: "<harness name or path> [more harnesses...]   e.g. ~/src/my-agent"
---

# preserved-thinking-audit

**Manual only.** This skill runs when the user invokes `/preserved-thinking-audit <harness>` or asks for it by name. If you are reading this because a request only sounded related (a signature error, a question about thinking blocks), stop, tell the user this skill exists and what it costs, and wait for them to ask for it. It spends API credits on the user's key and runs a third-party agent.

Find out whether a harness keeps Claude's thinking usable across turns on Claude Fable 5.1, where it breaks, and what its shortcuts cost.

The output is a report for the harness's maintainer. It opens with a list of action items: what to fix, what to decide, and what nobody tested yet. Each item says what to change, where in the code, what the audit saw, and how to confirm the change worked. The evidence for each item sits below the list. It is one local HTML file, plus the same list as Markdown for a tracker.

The rules being tested are public: [Preserved thinking](https://platform.claude.com/docs/en/build-with-claude/preserved-thinking). This skill automates the "Check whether your code edits the prefix" section of that page and adds a code-reading pass.

Needs Python 3.10+, `bash`, `curl`, `git`, and an Anthropic API key with access to `claude-fable-5-1`. The scripts use the standard library only. A full audit of one harness is three runs of 12 to 25 model requests each, billed to that key.

## Ground rules

1. **The deliverable is the report.** Everything the audit learns goes into it as an action item the maintainer can fix or decide on, backed by evidence they can open: a file and line, a request number, a quoted diff. Hand the report to the user. They decide who sees it and what happens next.
2. The audit reads and runs the harness as it is. The one place it changes harness code is the optional fix trial (step 7), on a throwaway local branch, and the report says which results came from a patched build.
3. The real API key goes to the proxy only. `pt_run.sh` hands it over on a file descriptor, removes it from the environment, and gives the driver and harness a dummy key (`sk-ant-dummy`). Never put a real key in a third-party harness's config, environment, or logs, and never start a harness from a shell that still exports one. A harness running as the same user could still read the proxy's memory, which is one more reason for rule 6.
4. The run logs under `~/pt-audit/` hold full request and response bodies. Treat them as private, and never point a harness at a real project: use the scratch repo.
5. Test against `claude-fable-5-1` (the proxy forces it).
6. **Isolate the harness.** Step 4 runs a third-party agent unattended with shell access, as the current user. The scratch repo does not confine it. Before any headless run with approvals off, confirm with the user that you are inside a throwaway container, VM, or low-privilege account with no real credentials (the driver template requires `PT_ISOLATED=1`). If not, keep the harness's approval prompts on and drive it interactively. Never turn off a harness's safety mode on the user's main machine account.

## What is being checked

When a `thinking` or `redacted_thinking` block comes back in a request, the API reads its `signature` and asks two things:

- **Model check:** is this model the one that produced the block, or a newer one? If not, the block is dropped for that request, without an error, always. Harness risk: stripping blocks client-side on a downgrade so they are gone when the session returns.
- **Prefix check:** are the top-level `system`, the set of `tools`, and every message before the block unchanged since the block was produced? If not, that block and every later thinking block is invalid: a 400 by default, or dropped with `thinking.block_binding.prefix_mismatch_behavior: "drop_block"`. Enforced by default for accounts created on or after August 31, 2026, for any request that sets the field, and for every account on later models.

Read `references/mechanics.md` before your first audit. It has what counts as an edit, the exact error text, the response fields, and the replacement for each common edit.

The audit answers, per harness:

- Does a normal multi-turn session pass the prefix check untouched?
- Which specific habits edit the prefix (the catalog is `references/checks.md`, 23 checks in six groups)?
- Does the harness send thinking back at all? A harness that strips it passes the check by starving it. That is verdict **MASKED**, and its prefix edits show up as **latent**.
- If it leans on `drop_block`, how many blocks and turns lose thinking, and what happens to thinking rate, output tokens, and cache reads?
- What does it do with the 400?

## Workflow

Work in `~/pt-audit/<harness>/` (override the root with `PT_AUDIT_ROOT`). In the commands below, `$S` is the `scripts` directory next to this file.

### 1. Locate and pin the harness

Use the path the user gave, or clone shallow (`git clone --depth 1 <upstream> ~/pt-audit/_src/<name>`; read-only use). Record the commit SHA and version for the report. If the harness has no Anthropic Messages API path (OpenAI-compatible only, Bedrock only, wraps another agent), say so: the wire test does not apply and the audit is code-reading only.

### 2. Code reading (static pass)

Give `references/static-audit.md` to one sub-agent per harness if you have sub-agents, or follow it yourself. It produces `static.json`: one row per check with `pass | fail | latent | not_exercised | info`, `file:line` evidence, and a one-line note. Save it to `~/pt-audit/<harness>/static.json`. The evidence becomes the "Where" line of that check's action item, so it has to be something a maintainer can open. The static pass also tells you how to wire the base URL, which scenarios the harness supports (compaction command, model switch, resume, MCP, modes), and what to trigger in step 4.

### 3. Get the harness running and wire it to the proxy

If the harness is not already installed, build or install it under `~/pt-audit/_tools/` so the audit does not touch the user's own installation. Give it isolated config and data directories (`XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `--data-dir`) so nothing lands in the user's home.

Everything for one harness lives in `~/pt-audit/<name>/`: the scratch repo in `workspace/`, the driver in `drive.sh`, isolated config in `xdg/`, and one folder per run. Set it up once:

```bash
mkdir -p ~/pt-audit/<name>
$S/pt_workspace.sh ~/pt-audit/<name>/workspace          # the scratch repo the harness works in
cp $S/../references/drivers/example.sh ~/pt-audit/<name>/drive.sh
# edit the three HARNESS_* lines in drive.sh (references/harness-wiring.md says how to find the base-URL setting)
```

Dry-run the wiring for free before spending credits. `fake_api.py` in the repo's `evals/` folder stands in for the API and applies the documented rules:

```bash
python3 <repo>/evals/preserved-thinking-audit/fake_api.py 18900 &
PT_ISOLATED=1 PT_UPSTREAM_API_KEY=dummy $S/pt_run.sh <name> drop_block --run-id dry --upstream http://127.0.0.1:18900 -- ~/pt-audit/<name>/drive.sh
kill %1
```

When that prints a verdict with `requests=` above zero, the driver and base URL are right. Then the real run. The wrapper starts the proxy, runs your driver, stops the proxy, and runs the analyzer:

```bash
export PT_UPSTREAM_API_KEY=<real key>   # read by the proxy only; pt_run.sh removes it from the driver's environment
PT_ISOLATED=1 $S/pt_run.sh <name> drop_block --run-id r1 -- ~/pt-audit/<name>/drive.sh
```

`PT_ISOLATED=1` is your statement that ground rule 6 holds (`references/drivers/README.md` has a container recipe). Use the wrapper, not two separate shell calls: some agent sandboxes give each shell command its own network namespace, and then a proxy started in one call is unreachable from the next. The wrapper exports `PT_BASE_URL` (`http://127.0.0.1:8484`), `PT_BASE_URL_V1`, `PT_OUT`, `PT_HARNESS_DIR` (`~/pt-audit/<name>`), and `PT_MARK` (a script: `$PT_MARK compaction "note"`). The call needs network access to `api.anthropic.com`. The log lands in `~/pt-audit/<name>/<run-id>-<mode>/exchanges.jsonl`.

Point the harness at the proxy with a dummy key. `references/harness-wiring.md` shows how to find the harness's base-URL setting and covers the `/v1` suffix trap (a request to `/v1/v1/messages` in the harness's error means drop the suffix). If `PT_UPSTREAM_API_KEY` is unset the proxy falls back to `ANTHROPIC_API_KEY`. If neither is set, ask the user for a key; don't go looking for one. For a gateway that wants a bearer token, pass `--auth-scheme bearer` after the mode; `--extra-beta <value>` adds an `anthropic-beta` value to every forwarded request. `PT_PROXY_ARGS` can carry those two flags and `--metadata-user-id` as defaults, and nothing else: flags that decide where the key goes stay on the command line.

What the proxy does to each Messages request, all logged as `mutations`:

- forwards only `POST .../messages`, `POST .../messages/count_tokens`, and `GET .../models`, and answers 404 to anything else, because it attaches a real credential to what it forwards (`--allow-any-path` lifts this). It binds to loopback only unless you pass `--insecure-bind`.
- swaps in the real key and forces `model` to `claude-fable-5-1` (small side models matching `haiku` pass through untouched; change that with `--passthrough-model-regex`)
- adds the `thinking-binding-controls-2026-08-01` beta and sets `thinking.block_binding.prefix_mismatch_behavior` to the mode's value, unless the harness set it itself (then it records `harness_sets_binding`)
- repairs unrelated Claude Fable 5.1 blockers so the session survives long enough to test the prefix (budgeted or disabled thinking, sampling parameters, forced `tool_choice`, prefill). Each repair becomes a CFG finding. `--no-repair` turns this off for a final confirmation run.

### 4. Drive the scenarios

Write a driver script that runs the session in `references/scenarios.md` in the harness's headless mode if it has one, or drive its TUI through `tmux`. Ground rule 6 applies: read `references/drivers/README.md` on isolation before running a harness with its approvals off. Use the scratch repo as the workspace, never a real project. Mark steps so the analyzer can attribute edits, always through the helper, which carries the run's control token: `$PT_MARK <label>` with the labels from `references/scenarios.md` (`idle`, `big_output`, `tool_error`, `mode_switch`, `mcp_change`, `model_switch`, `compaction`, `resume`). The template driver covers the tool loop, `idle`, and `resume`; the other steps depend on what the harness can do headlessly, so add them per harness. A headless "continue the last session" command is a real resume: mark it.

Three runs per harness, same script each time:

| Mode | Arms | Answers |
|---|---|---|
| `drop_block` | check on, drops reported | which turns lose thinking, how much, why (run this first) |
| `error` | check on, 400 on mismatch | what a new account sees, and what the harness does with the 400 |
| `observe` | nothing added except the drop report | what the harness sends on its own (does it set `block_binding` itself?) |

Add a `strip` run when you need the cost of the "just drop thinking" shortcut: it removes all replayed thinking so you can compare thinking rate, output tokens per response, and task outcome against the `drop_block` run.

### 5. Analyze

```bash
python3 $S/pt_analyze.py ~/pt-audit/<name>/<run-dir> [...]
```

Writes `findings.json` beside each log and prints the verdict and failing checks. Read the evidence, don't only relay it: open the exchanges named in `evidence[].seq`, confirm the diff is a real edit, and name its source in the harness code. A `RECON` entry means the local diff and the API disagree. The API is right; find out why. Update `static.json` when the wire contradicts the code reading.

Verdicts: `READY`, `CLEAN-SO-FAR` (nothing failed, scenarios missing), `CONFIG` (only unrelated 400s), `LATENT` (edits seen, nothing lost yet), `MASKED` (thinking never survives, so edits are hidden; includes harnesses that keep thinking inside a tool loop and discard it at every user turn), `DEGRADED` (works only by dropping), `BREAKS` (unrecovered 400).

An edit counts as live (`fail`) only when thinking sat after it and a block actually mismatched, locally or per the API. Otherwise it is `latent`. Three numbers describe how much thinking survives on the client: `client_keep_rate` (the immediate echo), `history_keep_rate` (request over request), and `cross_user_turn_keep_rate` (what is left when the next user turn starts).

### 6. Report

```bash
python3 $S/pt_report.py --out ~/pt-audit/report.html --title "Harness thinking audit" \
  ~/pt-audit/*/*/findings.json --static <name>=~/pt-audit/<name>/static.json \
  --note "<name>=v1.2.3, commit abc1234, built from source, headless" \
  --actions-md ~/pt-audit/actions.md
```

Each harness section opens with its action items:

- **Fix**: a check failed on the wire or in the code. The item gives the habit, what to do, where (from `static.json`), what was seen and in which modes, and the condition that shows it is done.
- **Decide**: the harness makes an edit that cost nothing in these runs (latent), or the code can make it on a path the runs did not take. The maintainer chooses between fixing now and accepting the risk.
- **Not tested**: scenarios the runs skipped, with how to exercise them.

Below the items are the verdict's supporting detail: the thinking ledger (blocks that reached the model again, stripped by the harness, dropped by the API), per-mode metrics, and every check with wire and code verdicts and the raw evidence. `--actions-md` writes the same items as Markdown, one section per harness.

Read the generated HTML before you hand it over, and check each action item against its evidence: an item you cannot trace to a request or a line of code does not belong in the report. It is one self-contained file, and the user decides where it goes. The evidence quotes short fragments of what the harness sent (system-prompt text, prompts, tool output, local paths from the scratch workspace), because that is what a maintainer needs to find the bug. If the report will travel beyond the maintainer, build it with `--redact`: it keeps the check results, counts, and positions and drops the quoted text. Re-run `pt_report.py` with the same `--out` to update it.

In chat, lead with the action items: how many to fix, which one to fix first and why, then what needs a decision. Give the verdict in one line after that.

### 7. Fix trial (optional, local only)

To prove a fix works, apply the smallest patch from the check's Fix line on a local branch in the clone (`git checkout -b pt-trial`), rebuild, re-run the failing scenario in `error` mode, and confirm the check flips to pass. Record the diff in `~/pt-audit/<harness>/trial.patch`. The patch and the before-and-after result go into the report note for that harness, as evidence that the action item's fix works.

## Self-test

`python3 $S/pt_selftest.py` runs a tiny agent that commits one bad habit per run (18 habits) through the proxy against the live API and asserts the analyzer flags the right check. Run it after editing the scripts or when API behaviour may have changed. It is also the quickest way to see what each failure looks like on the wire. It costs a few dozen small requests. `--upstream <url>` points it at a local stand-in for the API instead, which is how the repo's offline evals run it for free.

## Files

- `scripts/pt_run.sh` proxy + driver + analyze in one process tree · `scripts/pt_workspace.sh` scratch repo · `scripts/pt_proxy.py` recording proxy · `scripts/pt_analyze.py` log to findings · `scripts/pt_report.py` findings to the HTML report and the Markdown action list · `scripts/pt_checks.py` habit, fix, verify per check · `scripts/pt_lib.py` the local prefix diff · `scripts/pt_selftest.py`
- `references/mechanics.md` how the two checks work · `references/checks.md` the catalog with triggers · `references/static-audit.md` code-reading brief · `references/scenarios.md` session script · `references/harness-wiring.md` finding the base-URL setting · `references/drivers/` driver template

# Skills

A collection of useful agent skills. A skill is a folder with a `SKILL.md` file, the format Claude Code and other coding agents load skills from.

| Skill | What it does |
| --- | --- |
| [`preserved-thinking-audit`](skills/preserved-thinking-audit) | Checks whether an agent harness sends Claude's thinking blocks back unchanged, and tells its maintainer what to fix. A harness is whatever builds your Claude API requests: a coding agent, an editor extension, an SDK wrapper, or a gateway. See [Using preserved-thinking-audit](#using-preserved-thinking-audit) below before you run it: it spends API credits and runs your harness unattended. |

## Install

The [`skills`](https://github.com/vercel-labs/skills) CLI copies a skill into your agent's skills folder. It needs Node.js.

```bash
# see what's in this repo
npx skills add cj-ant/skills --list

# install one skill into the current project
npx skills add cj-ant/skills --skill preserved-thinking-audit

# install it for your user, so it loads in other projects too
npx skills add cj-ant/skills --skill preserved-thinking-audit -g
```

The CLI asks which agent to install for. To skip the prompt, add `--agent claude-code`. Start a new agent session after installing so the skill loads. `npx skills update` pulls newer versions.

To install by hand for Claude Code, copy the skill's folder into `~/.claude/skills/`:

```bash
git clone https://github.com/cj-ant/skills.git cj-ant-skills
cp -r cj-ant-skills/skills/preserved-thinking-audit ~/.claude/skills/
```

## Using preserved-thinking-audit

On Claude Fable 5.1 and later models, the API rejects or drops a replayed thinking block when anything before it in the request changed ([preserved thinking](https://platform.claude.com/docs/en/build-with-claude/preserved-thinking)). Most harnesses change something: a timestamp in the system prompt, a reminder added and later removed, old tool output trimmed. This skill finds those edits.

**What you type.** In your agent, `/preserved-thinking-audit <path to the harness source>`. The skill runs only when you ask for it by name.

**What it does.** It reads the harness source against a catalog of 23 checks. Then it runs the harness through a scripted session three times, behind a local proxy that records every request and turns the API's prefix check on. Then it writes a report.

**What it needs.** Python 3.10+, `bash`, `git`, `curl`, and an Anthropic API key with access to `claude-fable-5-1`. The scripts use the standard library only. The session runs are a third-party agent working unattended with shell access, so run them in a throwaway container, VM, or user account. The skill's `references/drivers/README.md` has a Docker recipe, and the driver template refuses to start until you confirm with `PT_ISOLATED=1`.

**What it costs.** Three runs of 12 to 25 requests each on `claude-fable-5-1`, billed to your key, with the harness's own prompts and tool output as input. Check current pricing for that model before you start. The code-reading pass and the report cost nothing beyond your agent's own usage, and you can dry-run the whole loop for free against the stand-in API in `evals/` (the skill's step 3 shows how).

**What lands on disk.** Everything goes under `~/pt-audit/<harness>/`: the scratch repo the harness works in, one folder per run with the full request and response log (`exchanges.jsonl`, private, mode 700), `findings.json`, and the report.

**What you get.** `report.html`, one self-contained file, plus an optional Markdown action list for an issue tracker. Each harness section opens with action items: what to fix, what to decide, and what was not tested, each with the code location, what the audit saw and in which request, and how to verify the fix. The evidence quotes short fragments of what the harness sent; `--redact` drops them if the report will travel.

**Checking the skill itself.** `python3 evals/preserved-thinking-audit/run_offline.py` runs the analyzer against 18 known bad habits, 4 valid edits, and a fixture harness with planted bugs, in about 20 seconds with no key. See `evals/preserved-thinking-audit/README.md`.

## Issues

Open an issue or a pull request on this repo. For a wrong verdict, include the `findings.json` (build the report with `--redact` first if it holds anything private) and the harness commit you audited.

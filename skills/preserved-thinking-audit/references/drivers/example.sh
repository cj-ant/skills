#!/usr/bin/env bash
# Driver template. Copy it to ~/pt-audit/<harness>/drive.sh and fill in the three HARNESS_* lines.
# pt_run.sh runs it while the proxy is up and exports PT_BASE_URL, PT_BASE_URL_V1, and PT_MARK.
set -uo pipefail

# This runs a third-party agent unattended with shell access. It can read and write anything your user can,
# including ~/.ssh and cloud credentials, and the scratch repo does not confine it. Run it only inside a throwaway
# container or VM, or as a dedicated low-privilege user with no real credentials, then set PT_ISOLATED=1.
if [[ "${PT_ISOLATED:-}" != "1" ]]; then
  echo "refusing to run: this driver gives the harness unattended shell access. Run it in a container, VM, or throwaway account, then set PT_ISOLATED=1." >&2
  exit 2
fi
unset PT_UPSTREAM_API_KEY  # the harness must never see the real key; pt_run.sh already strips it

# Everything below edits files and git state in the current directory, so stop unless that directory is the scratch repo.
if [[ -z "${PT_HARNESS_DIR:-}" ]]; then
  echo "set PT_HARNESS_DIR to the folder for this harness under ~/pt-audit" >&2
  exit 1
fi
base=$PT_HARNESS_DIR
ws=$base/workspace
if ! cd "$ws" 2>/dev/null; then
  echo "no scratch workspace at $ws (create it with scripts/pt_workspace.sh)" >&2
  exit 1
fi
if [[ "$PWD" != "$(cd "$ws" && pwd)" || ! -f inventory/stock.py || ! -d .git ]]; then
  echo "$ws is not the pt_workspace.sh scratch repo, refusing to touch it" >&2
  exit 1
fi
git checkout -q . && git clean -fdq && git checkout -q main
git branch -D pt-scratch-branch -q 2>/dev/null || true

# Keep the config, data, and cache of the harness out of your home directory.
export XDG_CONFIG_HOME=$base/xdg/config XDG_DATA_HOME=$base/xdg/data XDG_CACHE_HOME=$base/xdg/cache DO_NOT_TRACK=1

# 1. How the harness is told where the API is. Most read an env var; some need a config file written here.
#    Try PT_BASE_URL first. If the harness errors show /v1/v1/messages, or a 404 on /messages, switch to the other one.
HARNESS_BASE_URL_VAR=ANTHROPIC_BASE_URL
export "$HARNESS_BASE_URL_VAR=$PT_BASE_URL"
# 2. The headless command for a new session, and 3. for continuing the last one.
HARNESS_NEW=(my-harness run --prompt)
HARNESS_CONTINUE=(my-harness run --continue --prompt)

# A 15-minute cap per call where a timeout command exists (GNU coreutils; on macOS install coreutils for gtimeout).
CAP=(); command -v timeout >/dev/null && CAP=(timeout 900); command -v gtimeout >/dev/null && [[ ${#CAP[@]} -eq 0 ]] && CAP=(gtimeout 900)
run() { ${CAP[@]+"${CAP[@]}"} "$@" 2>&1 | tail -15; local rc=${PIPESTATUS[0]}; echo "--- exit $rc"; if [[ $rc -eq 127 ]]; then echo "harness command not found: check HARNESS_NEW / HARNESS_CONTINUE" >&2; exit 127; fi; }
new() { run "${HARNESS_NEW[@]}" "$@"; }
cont() { run "${HARNESS_CONTINUE[@]}" "$@"; }

new "Run the tests, read the failing tests and the modules they cover, explain each bug briefly, fix them one at a time, and re-run the tests until they pass."
$PT_MARK resume "a new process continues the stored session"
cont "Now look at every TODO in the repo (grep for them), read the files involved together, and tell me which one is riskiest and why. Do not change anything yet."
$PT_MARK idle "AGENTS.md edited, branch created, file touched outside the harness"
printf '\nAlways run the full test suite before you say you are done.\n' >> AGENTS.md
git checkout -q -b pt-scratch-branch 2>/dev/null || true
echo "# touched $(date +%s)" >> docs/NOTES.md
cont "Implement the TODO in Stock.add so quantities merge when the sku already exists, add a test for it, and run the suite."
$PT_MARK resume
cont "Given everything you have learned about this codebase in this session, what one refactor would you do next? Think it through, answer in five lines."

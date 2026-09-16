# Session script

Same script in every mode so runs compare. Use the scratch repo from `scripts/pt_workspace.sh` (a small Python project with failing tests, TODOs, and an `AGENTS.md`), never a real project. Aim for 12 to 25 model requests per run. Mark each step before you take it with the helper `pt_run.sh` exports: `$PT_MARK <label> "optional note"`. Under `pt_run.sh` a bare `curl` to `/__pt/mark` is refused, because marks need the run's control token.

1. **Warm-up tool loop** (no marker). "Read the failing test and the module it covers, explain the bug, then fix it and run the tests." Forces several tool rounds with thinking between them. Exercises FID-1..3, PFX-1..3.
2. **Clock and workspace drift** (`idle`). Wait until the wall clock minute changes; from another shell edit a tracked file, edit `AGENTS.md`, and create a git branch. Then ask a follow-up that needs another tool call. Exercises PFX-1.
3. **Parallel calls and big output** (`big_output`). "Read these five files and grep for TODO across the repo in one go." Then two more turns. Exercises FID-3, PFX-3 truncation.
4. **Denied or failing tool** (`tool_error`). Make one tool call fail or deny a permission prompt. Exercises PFX-3 (harnesses often rewrite the failed round).
5. **Mode or tool change** (`mode_switch` / `mcp_change`). Switch plan/act or agent profile, or attach an MCP server mid-session, then continue. Exercises PFX-2.
6. **Model switch and return** (`model_switch`, only with `--force-model ''`). One turn on another Claude model, then back. Exercises MDL-1, MDL-2.
7. **Compaction** (`compaction`). Run the harness's compact command or lower its threshold so it fires, then ask for one more change. Exercises PFX-5, PFX-7.
8. **Resume** (`resume`). Quit the harness, start it again, resume the session, ask a question that needs the earlier context. Exercises PFX-6.
9. **Recovery** (in `error` mode this happens on its own at the first edit). Note what the user sees and whether the session continues. Exercises ERR-1.

Skip steps the harness cannot do and leave them `not_exercised`; say why in the report. For impact numbers, add a fixed task with a checkable outcome (tests pass) and compare `drop_block` against `strip`: thinking rate, output tokens per response, request count to finish, pass or fail.

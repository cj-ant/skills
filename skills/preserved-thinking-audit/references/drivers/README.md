# Drivers

**Isolation first.** A headless driver runs someone else's agent unattended with shell and file tools, often with its approval prompts switched off. The scratch repo is a working directory, not a sandbox: the agent runs as you and can reach your SSH keys, cloud credentials, and other projects. Run drivers inside a throwaway container or VM, or as a dedicated low-privilege user with no real credentials. Only the proxy needs the real API key. `pt_run.sh` strips it from the driver's environment, but a process running as the same user as the proxy can still get at it, and can edit the run log under `~/pt-audit/`. For a harness you don't trust, run the proxy outside the container or account the harness runs in and publish only its port in: then the key and the evidence are both out of reach. The template refuses to start until you set `PT_ISOLATED=1` to confirm. If you can't isolate, use the harness's approval mode restricted to the test command, `grep`, and file reads in the workspace, and drive it interactively.

A driver is the script `pt_run.sh` runs while the proxy is up. It resets the scratch workspace, runs the session in `../scenarios.md` through the harness's headless mode, and calls `$PT_MARK <label>` before each marked step.

`example.sh` is a template. It refuses to run outside an isolated environment or outside the scratch repo, then runs a four-step session: a tool loop, a resume, a resume after the workspace drifted (an instruction file edited, a branch created, a file touched), and one more resume.

Copy it to `~/pt-audit/<harness>/drive.sh` and fill in the three things that differ per harness: how the base URL is set, the headless command for a new session, and the command that continues one. If the harness has settings that force compaction early (a low context size or threshold), set them in its config from the driver so the run reaches compaction in a few turns.

## A container recipe

The simplest isolation that meets ground rule 6: run the whole audit, proxy included, in a throwaway container that holds nothing but the skill, the harness, and the API key you give it. The harness can still reach the key inside the container, but it can't reach your SSH keys, cloud credentials, or other projects, and the container is deleted afterwards. Any recent Linux image with Python 3.10+, git, curl, and bash works. For example, with Docker:

```bash
docker run --rm -it \
  -v "$PWD/skills/preserved-thinking-audit:/skill:ro" \
  -v "$HOME/pt-audit:/root/pt-audit" \
  -e PT_UPSTREAM_API_KEY -e PT_ISOLATED=1 \
  python:3.12-slim bash -c '
    apt-get update -qq && apt-get install -y -qq git curl >/dev/null
    # install or build the harness here, then:
    /skill/scripts/pt_run.sh <name> drop_block --run-id r1 -- /root/pt-audit/<name>/drive.sh'
```

`~/pt-audit` is mounted so the logs and `findings.json` survive the container; run `pt_report.py` on the host afterwards. For a harness you trust less than that, split it: run `pt_proxy.py` on the host with `--bind 0.0.0.0 --insecure-bind` behind a firewall rule that admits only the container, point the driver in the container at the host's address, and give the container no key at all. Then neither the key nor the run log is reachable from the harness.

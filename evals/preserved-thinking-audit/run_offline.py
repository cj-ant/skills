#!/usr/bin/env python3
"""Offline eval for preserved-thinking-audit: does the analyzer flag bad habits and leave valid edits alone?

Starts fake_api.py, runs the skill's pt_selftest.py against it, and prints a scoreboard. No API key, no cost.

    python3 evals/preserved-thinking-audit/run_offline.py            # run, compare with baseline.json
    python3 evals/preserved-thinking-audit/run_offline.py --update   # accept this run as the new baseline

Two numbers to hillclimb:
  detected     bad-habit cases where every expected check was flagged                (recall)
  clean        valid-edit cases where no prefix or fidelity check was flagged        (precision)
  fixtures     fixture harnesses whose wire audit matches the `wire` section of their expected.json
`extra_flags` lists checks flagged beyond the expected ones. Some are right (a habit can trip two checks);
read them before you chase them. The run fails if either number drops below the baseline.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parents[1] / "skills" / "preserved-thinking-audit" / "scripts"
SELFTEST = SCRIPTS / "pt_selftest.py"
BASELINE = HERE / "baseline.json"
CASES = {"leaky-harness": HERE / "static-audit-leaky"}  # fixture harness name -> eval case directory that holds it
TASK = ("Read inventory/stock.py, inventory/pricing.py, tests/test_stock.py and tests/test_pricing.py, "
        "then tell me which bug to fix first.")


def run_fixture(name: str, upstream: str, out: Path) -> dict:
    """Drive a fixture harness through the real pt_run.sh and compare findings.json with its ground truth."""
    truth = json.loads((CASES[name] / "expected.json").read_text())["wire"]
    workspace = out / f"{name}-workspace"
    subprocess.run(["bash", str(SCRIPTS / "pt_workspace.sh"), str(workspace)], check=True, capture_output=True)
    env = {**os.environ, "PT_AUDIT_ROOT": str(out / "pt-audit"), "PT_UPSTREAM_API_KEY": "sk-ant-not-a-real-key",
           "PYTHONPATH": f"{CASES[name] / name}{os.pathsep}{HERE / 'clock_shim'}"}
    driver = f'cd {workspace} && ANTHROPIC_BASE_URL="$PT_BASE_URL" python3 -m leaky {json.dumps(TASK)}'
    run = subprocess.run(["bash", str(SCRIPTS / "pt_run.sh"), name, "drop_block", "--run-id", "eval", "--port", str(free_port()),
                          "--upstream", upstream, "--", "bash", "-c", driver], env=env, capture_output=True, text=True, timeout=600)
    path = out / "pt-audit" / name / "eval-drop_block" / "findings.json"
    if not path.exists():
        return {"fixture": name, "ok": False, "verdict": "NO RUN", "problems": [(run.stdout + run.stderr)[-400:]]}
    findings = json.loads(path.read_text())
    by = {c["id"]: c["status"] for c in findings["checks"]}
    problems = []
    if findings["verdict"]["label"] != truth["verdict"]:
        problems.append(f"verdict {findings['verdict']['label']}, expected {truth['verdict']}")
    problems += [f"{c} is {by.get(c)}, expected {' or '.join(want)}" for c, want in truth["must_be"].items() if by.get(c) not in want]
    problems += [f"{c} flagged {by.get(c)} but is clean in this harness" for c in truth["must_not_flag"] if by.get(c) in ("fail", "latent")]
    return {"fixture": name, "ok": not problems, "verdict": findings["verdict"]["label"], "problems": problems,
            "requests": findings["metrics"]["requests"]}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--update", action="store_true", help="write this run's scores to baseline.json")
    ap.add_argument("--habits", nargs="*", default=None, help="run only these cases")
    ap.add_argument("--keep", default=None, help="directory for run logs (default: a temp dir)")
    args = ap.parse_args()

    port = free_port()
    tmp = None if args.keep else tempfile.TemporaryDirectory(prefix="pt-eval-")
    out = Path(args.keep or tmp.name)
    fake = subprocess.Popen([sys.executable, str(HERE / "fake_api.py"), str(port)])
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
            except urllib.error.HTTPError:
                break
            except OSError:
                time.sleep(0.1)
        cmd = [sys.executable, str(SELFTEST), "--upstream", f"http://127.0.0.1:{port}", "--out", str(out)]
        if args.habits:
            cmd += ["--habits", *args.habits]
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        fixtures = [] if args.habits else [run_fixture("leaky-harness", f"http://127.0.0.1:{port}", out)]
    finally:
        fake.terminate()
        fake.wait(timeout=5)

    results_path = out / "selftest.json"
    if not results_path.exists():
        print(run.stdout[-3000:], run.stderr[-3000:], sep="\n")
        print("selftest produced no results")
        return 2
    results = json.loads(results_path.read_text())
    valid = [r for r in results if r["habit"].startswith("valid_")]
    bad = [r for r in results if not r["habit"].startswith("valid_")]
    score = {"detected": sum(r["ok"] for r in bad), "bad_cases": len(bad),
             "clean": sum(r["ok"] for r in valid), "valid_cases": len(valid),
             "fixtures": sum(f["ok"] for f in fixtures), "fixture_cases": len(fixtures),
             "extra_flags": {r["habit"]: r["unexpected_flags"] for r in results if r["unexpected_flags"]}}

    print(f"{'case':32} {'result':6} {'verdict':13} notes")
    for r in results:
        notes = "; ".join(r["problems"]) or (("also flagged " + ", ".join(r["unexpected_flags"])) if r["unexpected_flags"] else "")
        print(f"{r['habit']:32} {'ok' if r['ok'] else 'MISS':6} {r['verdict']:13} {notes}")
    for f in fixtures:
        print(f"{'fixture:' + f['fixture']:32} {'ok' if f['ok'] else 'MISS':6} {f['verdict']:13} {'; '.join(f['problems'])}")
    print(f"\ndetected {score['detected']}/{score['bad_cases']} bad habits   clean {score['clean']}/{score['valid_cases']} valid edits   "
          f"fixtures {score['fixtures']}/{score['fixture_cases']}   logs: {out if args.keep else '(not kept; pass --keep <dir>)'}")
    print("result: ok = the case's expected checks came out as expected. verdict: what the audit would say about that fake harness "
          "(a valid_* case can still be DEGRADED for reasons outside the edit it tests).")

    if args.update:
        BASELINE.write_text(json.dumps(score, indent=2) + "\n")
        print(f"baseline updated: {BASELINE}")
        return 0
    if args.habits or not BASELINE.exists():
        return 0 if all(r["ok"] for r in results + fixtures) else 1
    base = json.loads(BASELINE.read_text())
    regressed = [k for k in ("detected", "clean", "fixtures") if score[k] < base.get(k, 0)]
    if regressed:
        print("REGRESSION vs baseline: " + ", ".join(f"{k} {base[k]} -> {score[k]}" for k in regressed))
        return 1
    improved = [k for k in ("detected", "clean", "fixtures") if score[k] > base.get(k, 0)]
    if improved:
        print("improved vs baseline: " + ", ".join(f"{k} {base[k]} -> {score[k]}" for k in improved) + "  (run with --update to keep it)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

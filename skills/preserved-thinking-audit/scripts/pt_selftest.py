#!/usr/bin/env python3
"""Self-test: a tiny agent loop that commits one known bad habit per run, driven through pt_proxy
against the live API, then checks that pt_analyze flags the matching check.

Run this after changing the proxy or analyzer, or when the API's behaviour may have moved:
    pt_selftest.py                 # every habit
    pt_selftest.py --habits clean volatile_system keep_tail
    pt_selftest.py --upstream http://127.0.0.1:18900   # against a local stand-in for the API (no key, no cost)
It costs a few dozen small claude-fable-5-1 requests against the live API.

Two kinds of case. Bad habits must be flagged (`expect_bad`). Valid habits, edits the docs allow,
must stay clean (`expect_clean`): they catch an analyzer that cries wolf.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from pt_proxy import SSEAssembler  # noqa: E402

FILES = {"a.txt": "391", "b.txt": "437", "c.txt": "1024", "d.txt": "2047"}
TOOLS = [{"name": "read_file", "description": "Read a small text file from the workspace and return its contents.",
          "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}]
EXTRA_TOOL = {"name": "list_dir", "description": "List files in the workspace.",
              "input_schema": {"type": "object", "properties": {}, "required": []}}
PROMPTS = [
    "Read a.txt and b.txt with the tool, one call per turn. Each holds an integer. Think carefully about whether their sum is prime, then tell me.",
    "Now read c.txt. Is a*b + c a perfect square? Reason it through before answering.",
    "Read d.txt too. Which pairs among the four numbers are coprime? Think step by step first.",
]
SYSTEM = "You are a careful arithmetic assistant working in a small workspace. Use the read_file tool to look at files. Session clock: {clock}."
REMINDER = "<system-reminder>Request independent reads together where you can.</system-reminder>"

# habit -> proxy mode, extra proxy args, checks that must be fail/latent (expect_bad), checks that must pass
# (expect_pass), and checks that must not be fail/latent (expect_clean). `offline_only` cases use request
# features the live API may gate behind another beta, so they run only with --upstream.
PREFIX_CHECKS = ["PFX-1", "PFX-2", "PFX-3", "PFX-4", "PFX-5", "FID-3", "FID-4"]
HABITS: dict[str, dict[str, Any]] = {
    "clean": {"mode": "error", "expect_bad": [], "expect_pass": ["PFX-1", "PFX-2", "PFX-3", "FID-1"], "stream": True},
    "volatile_system": {"mode": "drop_block", "expect_bad": ["PFX-1"]},
    "tool_churn": {"mode": "drop_block", "expect_bad": ["PFX-2"]},
    "strip_reminder": {"mode": "drop_block", "expect_bad": ["PFX-3"]},
    "truncate_results": {"mode": "drop_block", "expect_bad": ["PFX-3"]},
    "sliding_window": {"mode": "drop_block", "expect_bad": ["PFX-4"]},
    "keep_tail": {"mode": "drop_block", "expect_bad": ["PFX-5"]},
    "simple_compaction": {"mode": "error", "expect_bad": [], "expect_pass": ["PFX-5"]},
    "strip_thinking": {"mode": "drop_block", "expect_bad": ["FID-1", "LZY-2"]},
    "strip_thinking_volatile": {"mode": "error", "expect_bad": ["LZY-2", "PFX-1"]},
    "middle_hole": {"mode": "drop_block", "expect_bad": ["FID-4"]},
    "reorder_blocks": {"mode": "drop_block", "expect_bad": ["FID-3"]},
    "legacy_config": {"mode": "drop_block", "expect_bad": ["CFG-1", "CFG-2", "CFG-3"]},
    "blind_retry": {"mode": "error", "expect_bad": ["PFX-1", "ERR-1"]},
    "good_recovery": {"mode": "error", "expect_bad": ["PFX-1"], "expect_pass": ["ERR-1"]},
    "lazy_drop_block": {"mode": "observe", "expect_bad": ["PFX-1", "LZY-1"]},
    "model_switch_strip": {"mode": "drop_block", "proxy_args": ["--force-model", ""], "expect_bad": ["MDL-1"]},
    "model_switch_keep": {"mode": "drop_block", "proxy_args": ["--force-model", ""], "expect_bad": [], "expect_pass": ["MDL-1"]},
    # valid per the docs: none of these may be flagged as a prefix or fidelity edit
    "valid_cache_control_moves": {"mode": "error", "expect_bad": [], "expect_pass": ["FID-1"], "expect_clean": PREFIX_CHECKS},
    "valid_key_order_shuffle": {"mode": "error", "expect_bad": [], "expect_pass": ["FID-1"], "expect_clean": PREFIX_CHECKS},
    "valid_trim_oldest_thinking": {"mode": "error", "expect_bad": ["FID-1"], "expect_clean": PREFIX_CHECKS},
    "valid_deferred_tool_added": {"mode": "error", "expect_bad": [], "expect_pass": ["FID-1"], "expect_clean": PREFIX_CHECKS, "offline_only": True},
}


def reverse_keys(value: Any) -> Any:
    """Same JSON values, different key order: the docs say key order and formatting don't matter."""
    if isinstance(value, dict):
        return {k: reverse_keys(value[k]) for k in reversed(list(value))}
    if isinstance(value, list):
        return [reverse_keys(v) for v in value]
    return value


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Client:
    def __init__(self, base: str) -> None:
        self.base = base

    def mark(self, label: str) -> None:
        urllib.request.urlopen(f"{self.base}/__pt/mark?label={label}", timeout=10).read()

    def create(self, body: dict[str, Any], betas: str = "", stream: bool = False) -> tuple[int, dict[str, Any]]:
        if stream:
            body = {**body, "stream": True}
        headers = {"content-type": "application/json", "anthropic-version": "2023-06-01", "x-api-key": "sk-ant-dummy-selftest"}
        if betas:
            headers["anthropic-beta"] = betas
        req = urllib.request.Request(f"{self.base}/v1/messages", data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                if stream:
                    asm = SSEAssembler()
                    while True:
                        chunk = resp.read(8192)
                        if not chunk:
                            break
                        asm.feed(chunk)
                    result = asm.result() or {}
                    return (400 if result.get("type") == "error" else resp.status), result
                return resp.status, json.load(resp)
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")


class Agent:
    """A minimal harness. `habit` switches on exactly one bad practice."""

    def __init__(self, client: Client, habit: str, stream: bool = False) -> None:
        self.c, self.habit, self.stream = client, habit, stream
        self.messages: list[dict[str, Any]] = []
        self.clock = 0
        self.model = "claude-fable-5-1"
        self.tools = copy.deepcopy(TOOLS)
        self.requests = 0
        self.use_drop_block = habit == "lazy_drop_block"
        self.failed = False

    def system(self) -> str:
        volatile = self.habit in ("volatile_system", "strip_thinking_volatile", "blind_retry", "good_recovery", "lazy_drop_block")
        if volatile:
            self.clock += 1
        return SYSTEM.format(clock=f"10:{self.clock:02d}" if volatile else "10:00")

    def view(self) -> list[dict[str, Any]]:
        """The messages actually sent: where the bad habits live."""
        msgs = copy.deepcopy(self.messages)
        h = self.habit
        if h in ("strip_thinking", "strip_thinking_volatile") or (h == "model_switch_strip" and "opus" in self.model):
            for m in msgs:
                if m["role"] == "assistant":
                    m["content"] = [b for b in m["content"] if b.get("type") not in ("thinking", "redacted_thinking")]
        if h == "model_switch_strip" and "opus" in self.model:
            self.messages = copy.deepcopy(msgs)  # the strip is permanent, as in harnesses that rebuild history per model
        if h == "middle_hole":
            asst = [m for m in msgs if m["role"] == "assistant" and any(b.get("type") == "thinking" for b in m["content"])]
            if len(asst) >= 3:
                asst[1]["content"] = [b for b in asst[1]["content"] if b.get("type") != "thinking"]
        if h == "reorder_blocks":
            for m in msgs:
                if m["role"] == "assistant":
                    others = [b for b in m["content"] if b.get("type") in ("thinking", "redacted_thinking")]
                    rest = [b for b in m["content"] if b.get("type") not in ("thinking", "redacted_thinking")]
                    m["content"] = others + sorted(rest, key=lambda b: 0 if b.get("type") == "tool_use" else 1)
        if h == "strip_reminder":
            results = [m for m in msgs if m["role"] == "user" and isinstance(m["content"], list) and any(b.get("type") == "tool_result" for b in m["content"])]
            for m in results[:-1]:
                m["content"] = [b for b in m["content"] if not (b.get("type") == "text" and "system-reminder" in b.get("text", ""))]
        if h == "truncate_results":
            results = [m for m in msgs if m["role"] == "user" and isinstance(m["content"], list) and any(b.get("type") == "tool_result" for b in m["content"])]
            for m in results[:-2]:
                for b in m["content"]:
                    if b.get("type") == "tool_result":
                        b["content"] = "[old tool result cleared]"
        if h == "sliding_window":
            # keep only the two most recent user prompts and what followed them
            prompts = [i for i, m in enumerate(msgs) if m["role"] == "user" and isinstance(m["content"], str)]
            if len(prompts) >= 3:
                msgs = msgs[prompts[-2]:]
        if h == "valid_cache_control_moves":
            # the breakpoint sits on the newest tool_result only, so it moves on every request
            for m in reversed(msgs):
                if m["role"] == "user" and isinstance(m["content"], list) and m["content"]:
                    m["content"][-1]["cache_control"] = {"type": "ephemeral"}
                    break
        if h == "valid_key_order_shuffle":
            msgs = reverse_keys(msgs)
        if h == "valid_trim_oldest_thinking":
            # drop thinking from the oldest assistant turns only, keeping an unbroken run at the end
            asst = [m for m in msgs if m["role"] == "assistant"]
            for m in asst[:-2]:
                m["content"] = [b for b in m["content"] if b.get("type") not in ("thinking", "redacted_thinking")]
        return msgs

    def compact(self, keep_tail: bool) -> None:
        summary = ("Summary of the session so far: the user asked about integers stored in a.txt (391), b.txt (437) and c.txt (1024); "
                   "their sum 828 is not prime, and a*b + c = 171891 is not a perfect square.")
        self.c.mark("compaction")
        if keep_tail:
            # keep the last user prompt and everything after it verbatim (thinking included)
            idx = max(i for i, m in enumerate(self.messages) if m["role"] == "user" and isinstance(m["content"], str))
            tail = self.messages[idx:]
            self.messages = [{"role": "user", "content": summary}, {"role": "assistant", "content": [{"type": "text", "text": "Understood."}]}] + tail
        else:
            self.messages = []
            self._pending_summary = summary

    def send(self) -> dict[str, Any] | None:
        body: dict[str, Any] = {"model": self.model, "max_tokens": 6000, "system": self.system(), "tools": self.tools,
                                "messages": self.view(), "output_config": {"effort": "high"}}
        betas = ""
        if self.habit == "legacy_config":
            body["thinking"] = {"type": "enabled", "budget_tokens": 2000}
            body["temperature"] = 0.2
            body.pop("output_config")
            if self.requests == 0:
                body["tool_choice"] = {"type": "any"}
        else:
            body["thinking"] = {"type": "adaptive"}
        if self.use_drop_block:
            body["thinking"]["block_binding"] = {"prefix_mismatch_behavior": "drop_block"}
            betas = "thinking-binding-controls-2026-08-01"
        self.requests += 1
        status, resp = self.c.create(body, betas, self.stream)
        if status == 400 and "Invalid `signature`" in json.dumps(resp):
            if self.habit == "blind_retry":
                for _ in range(2):
                    status, resp = self.c.create(body, betas, self.stream)
                self.failed = True
                return None
            if self.habit == "good_recovery":
                self.use_drop_block = True
                body["thinking"]["block_binding"] = {"prefix_mismatch_behavior": "drop_block"}
                status, resp = self.c.create(body, "thinking-binding-controls-2026-08-01", self.stream)
        if status != 200:
            print(f"    request failed {status}: {json.dumps(resp)[:300]}")
            self.failed = True
            return None
        return resp

    def turn(self, prompt: str) -> None:
        if getattr(self, "_pending_summary", None):
            prompt = self._pending_summary + "\n\n" + prompt
            self._pending_summary = None
        self.messages.append({"role": "user", "content": prompt})
        for _ in range(6):
            resp = self.send()
            if resp is None:
                return
            self.messages.append({"role": "assistant", "content": resp["content"]})
            uses = [b for b in resp["content"] if b.get("type") == "tool_use"]
            if not uses:
                return
            results: list[dict[str, Any]] = [{"type": "tool_result", "tool_use_id": u["id"],
                                              "content": FILES.get(str((u.get("input") or {}).get("path", "")).lstrip("./"), "file not found") + "\n" + "padding line\n" * 40}
                                             for u in uses]
            if self.habit == "strip_reminder":
                results.append({"type": "text", "text": REMINDER})
            self.messages.append({"role": "user", "content": results})


def run_habit(habit: str, out_root: str, upstream: str | None = None) -> dict[str, Any]:
    spec = HABITS[habit]
    port = free_port()
    out = os.path.join(out_root, habit)
    cmd = [sys.executable, os.path.join(HERE, "pt_proxy.py"), "--harness", f"selftest-{habit}", "--mode", spec["mode"],
           "--port", str(port), "--out", out, *spec.get("proxy_args", [])]
    if upstream:
        cmd += ["--upstream", upstream]
    proxy = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True)
    try:
        client = Client(f"http://127.0.0.1:{port}")
        for _ in range(50):
            if proxy.poll() is not None:
                raise SystemExit("pt_proxy failed to start:\n" + (proxy.stderr.read() if proxy.stderr else ""))
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/__pt/status", timeout=1).read()
                break
            except OSError:
                time.sleep(0.2)
        agent = Agent(client, habit, stream=bool(spec.get("stream")))
        for n, prompt in enumerate(PROMPTS):
            if agent.failed:
                break
            if habit == "tool_churn" and n == 1:
                agent.tools.append(copy.deepcopy(EXTRA_TOOL))
            if habit == "valid_deferred_tool_added" and n == 1:
                agent.tools.append({**copy.deepcopy(EXTRA_TOOL), "defer_loading": True})
            if habit in ("keep_tail", "simple_compaction") and n == 2:
                agent.compact(keep_tail=habit == "keep_tail")
            if habit.startswith("model_switch") and n == 1:
                client.mark("model_switch")
                agent.model = "claude-opus-5"
            if habit.startswith("model_switch") and n == 2:
                client.mark("model_switch")
                agent.model = "claude-fable-5-1"
            agent.turn(prompt)
    finally:
        proxy.terminate()
        try:
            proxy.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proxy.kill()
    from pt_analyze import analyze
    findings = analyze(out)
    by = {c["id"]: c["status"] for c in findings["checks"]}
    problems = []
    for cid in spec.get("expect_bad", []):
        if by.get(cid) not in ("fail", "latent"):
            problems.append(f"{cid} expected fail/latent, got {by.get(cid)}")
    for cid in spec.get("expect_pass", []):
        if by.get(cid) != "pass":
            problems.append(f"{cid} expected pass, got {by.get(cid)}")
    for cid in spec.get("expect_clean", []):
        if by.get(cid) in ("fail", "latent"):
            problems.append(f"{cid} is a valid edit per the docs but was flagged {by.get(cid)}")
    unexpected = [cid for cid, st in by.items() if st in ("fail", "latent") and cid not in spec.get("expect_bad", [])]
    return {"habit": habit, "ok": not problems, "statuses": by, "problems": problems, "unexpected_flags": unexpected,
            "verdict": findings["verdict"]["label"], "metrics": {k: findings["metrics"][k] for k in
                                                                 ("requests", "thinking_blocks_minted", "client_keep_rate", "server_dropped_blocks_prefix",
                                                                  "signature_400s", "response_thinking_rate", "cache_read_share")}, "out": out}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--habits", nargs="*", default=list(HABITS), choices=list(HABITS), metavar="HABIT")
    ap.add_argument("--out", default=None)
    ap.add_argument("--upstream", default=None, help="send requests here instead of the live API (a local stand-in; also runs the offline-only cases)")
    args = ap.parse_args()
    out_root = args.out or tempfile.mkdtemp(prefix="pt-selftest-")
    if args.upstream and not args.upstream.startswith("https://api.anthropic.com"):
        # a stand-in gets a dummy key, even if a real one is in the environment
        os.environ["PT_UPSTREAM_API_KEY"] = "sk-ant-not-a-real-key"
        os.environ.pop("ANTHROPIC_API_KEY", None)
    results = []
    for habit in args.habits:
        if HABITS[habit].get("offline_only") and not args.upstream:
            print(f"== {habit} (skipped: offline only, pass --upstream)")
            continue
        print(f"== {habit}")
        res = run_habit(habit, out_root, args.upstream)
        results.append(res)
        flag = "PASS" if res["ok"] else "FAIL"
        print(f"   {flag} verdict={res['verdict']} {res['metrics']}")
        for p in res["problems"]:
            print(f"   ! {p}")
        if res["unexpected_flags"]:
            print(f"   ? also flagged: {res['unexpected_flags']}")
    with open(os.path.join(out_root, "selftest.json"), "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=1)
    bad = [r["habit"] for r in results if not r["ok"]]
    print(f"\n{len(results) - len(bad)}/{len(results)} habits detected as expected. Logs: {out_root}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()

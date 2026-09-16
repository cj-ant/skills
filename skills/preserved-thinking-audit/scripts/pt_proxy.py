#!/usr/bin/env python3
"""Recording proxy for auditing a harness against preserved thinking.

Point the harness at this proxy (ANTHROPIC_BASE_URL=http://127.0.0.1:8484, or
the harness's own base-URL setting). Every Messages API exchange is written to
<out>/exchanges.jsonl with the body the harness sent, the body the proxy
forwarded, the mutations between them, and the response (streams reassembled).

Modes decide what the proxy adds to arm the prefix check:
  observe     forward as-is (still swaps auth / forces the model if asked)
  error       add the binding beta + prefix_mismatch_behavior "error"   -> API 400s on an edited prefix
  drop_block  add the binding beta + prefix_mismatch_behavior "drop_block" -> API reports drops in input_transformations
  strip       remove every thinking block the harness replays (worst-case baseline for impact A/B)

The real credential never reaches the harness: give the harness a dummy key
and let the proxy attach the real one upstream (--auth env, the default).

Stdlib only. Anthropic Messages API paths only (not Bedrock/Vertex/OpenAI-compatible shims).
"""

from __future__ import annotations

import argparse
import base64
import copy
import http.client
import json
import os
import re
import shlex
import signal
import ssl
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pt_lib import BINDING_BETA, THINKING_TYPES, as_blocks  # noqa: E402

HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
               "transfer-encoding", "upgrade", "content-length", "host", "accept-encoding", "content-encoding"}
SECRET_HEADERS = {"x-api-key", "authorization", "cookie", "proxy-authorization"}


class Auth:
    """Upstream credential source. `env` reads the real API key from the proxy's environment;
    `passthrough` forwards whatever the harness sent."""

    def __init__(self, kind: str, key_fd: int | None = None, scheme: str = "x-api-key") -> None:
        self.kind = kind
        self.scheme = scheme
        self._key: str | None = None
        if kind == "passthrough":
            return
        if key_fd is not None:
            # pt_run.sh hands the key over on a file descriptor so it is in nobody's environment.
            with os.fdopen(key_fd, encoding="utf-8") as fh:
                self._key = fh.read().strip() or None
        else:
            self._key = os.environ.get("PT_UPSTREAM_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
        # Nothing this process starts should inherit the real key.
        os.environ.pop("PT_UPSTREAM_API_KEY", None)
        os.environ.pop("ANTHROPIC_API_KEY", None)

    def headers(self) -> dict[str, str] | None:
        if self.kind == "passthrough":
            return None
        if self._key:
            return {"authorization": f"Bearer {self._key}"} if self.scheme == "bearer" else {"x-api-key": self._key}
        raise SystemExit("--auth env needs the real key: PT_UPSTREAM_API_KEY or ANTHROPIC_API_KEY in the proxy's environment, or --key-fd")


class State:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.auth = Auth(args.auth, args.key_fd, args.auth_scheme)
        self.seq = 0
        self.inflight = 0
        self.lock = threading.Lock()
        os.makedirs(args.out, mode=0o700, exist_ok=True)
        os.chmod(args.out, 0o700)  # full request and response bodies land here
        self.log_path = os.path.join(args.out, "exchanges.jsonl")
        self.passthrough_re = re.compile(args.passthrough_model_regex) if args.passthrough_model_regex else None
        upstream = urllib.parse.urlsplit(args.upstream)
        self.up_scheme, self.up_host = upstream.scheme, upstream.netloc
        self.up_prefix = upstream.path.rstrip("/")
        with open(os.path.join(args.out, "run.json"), "w", encoding="utf-8") as fh:
            json.dump({"harness": args.harness, "run_id": args.run_id, "mode": args.mode, "force_model": args.force_model,
                       "repair": args.repair, "started": time.time(), "upstream": args.upstream,
                       "override_binding": args.override_binding}, fh, indent=2)

    def next_seq(self) -> int:
        with self.lock:
            self.seq += 1
            return self.seq

    def write(self, row: dict[str, Any]) -> None:
        line = json.dumps(row, ensure_ascii=False)
        with self.lock:
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")


def merge_betas(existing: str | None, extra: list[str]) -> str:
    seen: list[str] = []
    for value in (existing or "").split(","):
        value = value.strip()
        if value and value not in seen:
            seen.append(value)
    for value in extra:
        if value not in seen:
            seen.append(value)
    return ",".join(seen)


def mutate_body(state: State, body: dict[str, Any], is_count_tokens: bool) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """Return (body to forward, list of mutations for the log, betas to add)."""
    args = state.args
    sent = copy.deepcopy(body)
    muts: list[dict[str, Any]] = []
    betas: list[str] = []
    model = str(sent.get("model") or "")

    passthrough = bool(state.passthrough_re and state.passthrough_re.search(model))
    if args.force_model and model != args.force_model and not passthrough:
        muts.append({"kind": "force_model", "from": model, "to": args.force_model})
        sent["model"] = args.force_model
    if passthrough:
        # A side model (titles, summaries on a small model). Leave it alone entirely.
        return sent, muts + [{"kind": "passthrough_model", "model": model}], betas

    if args.metadata_user_id and not (isinstance(sent.get("metadata"), dict) and sent["metadata"].get("user_id")):
        # Optional. `metadata` is outside the checked prefix, so adding it never affects a verdict.
        sent["metadata"] = {**(sent.get("metadata") or {}), "user_id": args.metadata_user_id}
        muts.append({"kind": "add_metadata_user_id"})

    if args.repair:
        for key in ("temperature", "top_p", "top_k"):
            if key in sent and not (key == "temperature" and sent[key] == 1):
                muts.append({"kind": "repair_sampling_param", "param": key, "value": sent[key]})
                sent.pop(key)
        thinking = sent.get("thinking")
        if isinstance(thinking, dict) and thinking.get("type") == "enabled":
            muts.append({"kind": "repair_budgeted_thinking", "budget_tokens": thinking.get("budget_tokens")})
            fixed = {k: v for k, v in thinking.items() if k != "budget_tokens"}
            fixed["type"] = "adaptive"
            sent["thinking"] = fixed
        if isinstance(thinking, dict) and thinking.get("type") == "disabled":
            muts.append({"kind": "repair_thinking_disabled"})
            sent["thinking"] = {"type": "adaptive"}
        choice = sent.get("tool_choice")
        if isinstance(choice, dict) and choice.get("type") in ("tool", "any"):
            muts.append({"kind": "repair_forced_tool_choice", "value": choice})
            sent["tool_choice"] = {"type": "auto"}
        msgs = sent.get("messages") or []
        if msgs and msgs[-1].get("role") == "assistant" and not is_count_tokens:
            muts.append({"kind": "repair_prefill", "prefill": str(msgs[-1].get("content"))[:200]})
            sent["messages"] = msgs[:-1]

    if args.mode == "strip":
        removed = 0
        for msg in sent.get("messages") or []:
            if msg.get("role") == "assistant" and isinstance(msg.get("content"), list):
                kept = [b for b in msg["content"] if not (isinstance(b, dict) and b.get("type") in THINKING_TYPES)]
                removed += len(msg["content"]) - len(kept)
                msg["content"] = kept or [{"type": "text", "text": "(no text)"}]
        if removed:
            muts.append({"kind": "strip_thinking", "blocks": removed})

    if args.mode in ("error", "drop_block"):
        thinking = sent.get("thinking")
        own = None
        if isinstance(thinking, dict):
            own = (thinking.get("block_binding") or {}).get("prefix_mismatch_behavior")
        if own and not args.override_binding:
            muts.append({"kind": "harness_sets_binding", "value": own})
        else:
            if not isinstance(thinking, dict):
                thinking = {"type": "adaptive"}
                muts.append({"kind": "add_thinking_config"})
            binding = dict(thinking.get("block_binding") or {})
            binding["prefix_mismatch_behavior"] = args.mode
            thinking = {**thinking, "block_binding": binding}
            sent["thinking"] = thinking
            muts.append({"kind": "inject_binding", "value": args.mode, "overrode": own})
        betas.append(BINDING_BETA)
    if args.mode == "observe":
        # Ask for the drop report without arming enforcement, when the harness did not.
        if args.observe_report:
            betas.append(BINDING_BETA)
    return sent, muts, betas


class SSEAssembler:
    """Rebuild the final Message object from a Messages API event stream."""

    def __init__(self) -> None:
        self.message: dict[str, Any] | None = None
        self.blocks: dict[int, dict[str, Any]] = {}
        self.partial_json: dict[int, str] = {}
        self.error: dict[str, Any] | None = None
        self.buffer = b""
        self.events = 0

    def feed(self, chunk: bytes) -> None:
        self.buffer += chunk
        while True:
            m = re.search(rb"\r?\n\r?\n", self.buffer)
            if not m:
                return
            raw, self.buffer = self.buffer[: m.start()], self.buffer[m.end():]
            data_lines = [ln[5:].lstrip() for ln in raw.splitlines() if ln.startswith(b"data:")]
            if not data_lines:
                continue
            try:
                self._event(json.loads(b"\n".join(data_lines)))
            except (ValueError, KeyError, TypeError):
                continue

    def _event(self, ev: dict[str, Any]) -> None:
        self.events += 1
        kind = ev.get("type")
        if kind == "message_start":
            self.message = ev.get("message") or {}
            self.message.setdefault("content", [])
        elif kind == "content_block_start":
            self.blocks[ev["index"]] = ev.get("content_block") or {}
        elif kind == "content_block_delta":
            block = self.blocks.setdefault(ev["index"], {})
            delta = ev.get("delta") or {}
            dtype = delta.get("type")
            if dtype == "text_delta":
                block["text"] = block.get("text", "") + delta.get("text", "")
            elif dtype == "thinking_delta":
                block["thinking"] = block.get("thinking", "") + delta.get("thinking", "")
            elif dtype == "signature_delta":
                block["signature"] = block.get("signature", "") + delta.get("signature", "")
            elif dtype == "input_json_delta":
                self.partial_json[ev["index"]] = self.partial_json.get(ev["index"], "") + delta.get("partial_json", "")
            elif dtype == "citations_delta":
                block.setdefault("citations", []).append(delta.get("citation"))
        elif kind == "content_block_stop":
            idx = ev["index"]
            if idx in self.partial_json:
                raw = self.partial_json.pop(idx)
                try:
                    self.blocks[idx]["input"] = json.loads(raw) if raw else {}
                except ValueError:
                    self.blocks[idx]["_pt_unparsed_input"] = raw
        elif kind == "message_delta" and self.message is not None:
            self.message.update({k: v for k, v in (ev.get("delta") or {}).items() if v is not None})
            if ev.get("usage"):
                self.message["usage"] = {**(self.message.get("usage") or {}), **ev["usage"]}
            if "input_transformations" in ev:
                self.message["input_transformations"] = ev["input_transformations"]
        elif kind == "error":
            self.error = ev.get("error") or ev

    def result(self) -> dict[str, Any] | None:
        if self.error and self.message is None:
            return {"type": "error", "error": self.error}
        if self.message is None:
            return None
        self.message["content"] = [self.blocks[i] for i in sorted(self.blocks)]
        if self.error:
            self.message["_pt_stream_error"] = self.error
        return self.message


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    state: State

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - quiet default logging; name fixed by the base class
        del format, args

    def _control(self) -> bool:
        parsed = urllib.parse.urlsplit(self.path)
        if not parsed.path.startswith("/__pt/"):
            return False
        query = urllib.parse.parse_qs(parsed.query)
        token = self.state.args.control_token
        if parsed.path == "/__pt/mark" and token and (query.get("token") or [""])[0] != token:
            payload = {"ok": False, "error": "bad or missing control token"}
        elif parsed.path == "/__pt/mark":
            label = (query.get("label") or ["mark"])[0]
            note = (query.get("note") or [""])[0]
            self.state.write({"kind": "marker", "seq": self.state.next_seq(), "ts": time.time(), "label": label, "note": note})
            payload = {"ok": True, "label": label}
        elif parsed.path == "/__pt/status":
            payload = {"ok": True, "seq": self.state.seq, "mode": self.state.args.mode, "run_id": self.state.args.run_id}
        else:
            payload = {"ok": False, "error": "unknown control path"}
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
        return True

    def do_GET(self) -> None:  # noqa: N802
        if not self._control():
            self._forward()

    def do_POST(self) -> None:  # noqa: N802
        if not self._control():
            self._forward()

    do_PUT = do_DELETE = do_PATCH = do_POST  # noqa: N815

    def _forward(self) -> None:
        # Count the request as in flight until its log row is written, so shutdown can wait for it.
        with self.state.lock:
            self.state.inflight += 1
        try:
            self._forward_inner()
        finally:
            with self.state.lock:
                self.state.inflight -= 1

    def _forward_inner(self) -> None:
        state = self.state
        started = time.time()
        seq = state.next_seq()
        length = int(self.headers.get("content-length") or 0)
        raw_body = self.rfile.read(length) if length else b""
        path_only = urllib.parse.urlsplit(self.path).path
        is_messages = path_only.rstrip("/").endswith("/messages")
        is_count = path_only.rstrip("/").endswith("/messages/count_tokens")
        is_models = self.command == "GET" and re.search(r"/models(/[^/]+)?$", path_only.rstrip("/")) is not None
        if self.headers.get("origin"):
            self._fail({"kind": "exchange", "seq": seq, "ts": started, "method": self.command, "path": self.path, "refused": True}, 403,
                       "pt_proxy refuses requests that carry an Origin header (a web page, not a harness)", started)
            return
        allowed = (self.command == "POST" and (is_messages or is_count)) or is_models
        if not allowed and not state.args.allow_any_path:
            self._fail({"kind": "exchange", "seq": seq, "ts": started, "method": self.command, "path": self.path, "refused": True}, 404,
                       "pt_proxy forwards only POST .../messages, POST .../messages/count_tokens and GET .../models (--allow-any-path lifts this)", started)
            return
        row: dict[str, Any] = {"kind": "exchange", "seq": seq, "ts": started, "method": self.command, "path": self.path,
                               "is_messages": is_messages, "is_count_tokens": is_count}
        req_headers = {k.lower(): v for k, v in self.headers.items()}
        row["req_headers"] = {k: ("<redacted>" if k in SECRET_HEADERS else v) for k, v in req_headers.items()}

        body_obj: dict[str, Any] | None = None
        sent_raw = raw_body
        betas: list[str] = []
        if (is_messages or is_count) and raw_body:
            try:
                body_obj = json.loads(raw_body)
            except ValueError:
                body_obj = None
        if isinstance(body_obj, dict):
            sent_obj, muts, betas = mutate_body(state, body_obj, is_count)
            row["req_body"] = body_obj
            row["mutations"] = muts
            if muts:
                row["req_body_sent"] = sent_obj
            sent_raw = json.dumps(sent_obj, ensure_ascii=False).encode("utf-8")
        elif raw_body:
            row["req_body_raw"] = raw_body[:4000].decode("utf-8", "replace")

        out_headers = {k: v for k, v in req_headers.items() if k not in HOP_HEADERS}
        auth = None
        try:
            auth = state.auth.headers()
        except Exception as exc:  # noqa: BLE001 - surfaced to the client and the log
            self._fail(row, 502, f"proxy auth error: {exc}", started)
            return
        if auth is not None:
            out_headers.pop("x-api-key", None)
            out_headers.pop("authorization", None)
            out_headers.update(auth)
        if state.args.extra_beta:
            betas = [*state.args.extra_beta, *betas]
        if betas:
            out_headers["anthropic-beta"] = merge_betas(out_headers.get("anthropic-beta"), betas)
        out_headers.setdefault("anthropic-version", "2023-06-01")
        out_headers["accept-encoding"] = "identity"
        row["sent_betas"] = out_headers.get("anthropic-beta", "")

        status, resp_headers, resp = self._upstream(row, out_headers, sent_raw, started)
        if resp is None:
            return
        try:
            self._relay(row, status, resp_headers, resp, started)
        except Exception as exc:  # noqa: BLE001 - record the row, then let the connection drop
            row.update({"status": status, "proxy_error": f"relay failed: {exc}", "duration_ms": int((time.time() - started) * 1000)})
            self.state.write(row)
            print(f"[pt] #{row['seq']} relay failed: {exc}", file=sys.stderr, flush=True)
            raise

    def _upstream(self, row: dict[str, Any], headers: dict[str, str], body: bytes,
                  started: float) -> tuple[int, dict[str, str], http.client.HTTPResponse | None]:
        state = self.state
        try:
            proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") if state.up_scheme == "https" else None
            if state.up_scheme == "https":
                ctx = ssl.create_default_context()
                if proxy:
                    p = urllib.parse.urlsplit(proxy)
                    # http.client connects to the forward proxy in the clear, sends CONNECT, then does TLS to the upstream inside the tunnel
                    conn = http.client.HTTPSConnection(p.hostname, p.port or 3128, timeout=state.args.timeout, context=ctx)
                    tunnel_headers = {}
                    if p.username:
                        cred = f"{urllib.parse.unquote(p.username)}:{urllib.parse.unquote(p.password or '')}"
                        tunnel_headers["Proxy-Authorization"] = "Basic " + base64.b64encode(cred.encode()).decode()
                    conn.set_tunnel(state.up_host, headers=tunnel_headers)
                else:
                    conn = http.client.HTTPSConnection(state.up_host, timeout=state.args.timeout, context=ctx)
            else:
                conn = http.client.HTTPConnection(state.up_host, timeout=state.args.timeout)
            hdrs = {**headers, "host": state.up_host, "content-length": str(len(body))}
            conn.request(self.command, state.up_prefix + self.path, body=body or None, headers=hdrs)
            resp = conn.getresponse()
            resp_headers = {k.lower(): v for k, v in resp.getheaders()}
            return resp.status, resp_headers, resp
        except Exception as exc:  # noqa: BLE001
            self._fail(row, 502, f"upstream error: {exc}", started)
            return 502, {}, None

    def _relay(self, row: dict[str, Any], status: int, resp_headers: dict[str, str],
               resp: http.client.HTTPResponse, started: float) -> None:
        is_sse = "text/event-stream" in resp_headers.get("content-type", "")
        row["status"] = status
        row["stream"] = is_sse
        row["resp_headers"] = {k: v for k, v in resp_headers.items() if k in ("request-id", "content-type", "retry-after")}
        self.send_response(status)
        for k, v in resp_headers.items():
            if k not in HOP_HEADERS:
                self.send_header(k, v)
        client_gone = False
        if is_sse:
            self.send_header("transfer-encoding", "chunked")
            self.end_headers()
            asm = SSEAssembler()
            while True:
                chunk = resp.read1(65536)
                if not chunk:
                    break
                asm.feed(chunk)
                if not client_gone:
                    try:
                        self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        client_gone = True
            if not client_gone:
                try:
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    client_gone = True
            row["resp_body"] = asm.result()
            row["sse_events"] = asm.events
        else:
            data = resp.read()
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                client_gone = True
            try:
                row["resp_body"] = json.loads(data) if data else None
            except ValueError:
                row["resp_body_raw"] = data[:4000].decode("utf-8", "replace")
        row["client_disconnected"] = client_gone
        row["duration_ms"] = int((time.time() - started) * 1000)
        self.state.write(row)
        self._console(row)

    def _fail(self, row: dict[str, Any], status: int, message: str, started: float) -> None:
        row.update({"status": status, "proxy_error": message, "duration_ms": int((time.time() - started) * 1000)})
        self.state.write(row)
        raw = json.dumps({"type": "error", "error": {"type": "api_error", "message": message}}).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
        print(f"[pt] #{row['seq']} {status} {message}", file=sys.stderr, flush=True)

    def _console(self, row: dict[str, Any]) -> None:
        body = row.get("resp_body") or {}
        note = ""
        if isinstance(body, dict):
            if body.get("type") == "error":
                note = " ERR " + str((body.get("error") or {}).get("message"))[:140]
            else:
                drops = body.get("input_transformations") or []
                kinds = [b.get("type") for b in body.get("content") or []]
                replayed = sum(1 for m in (row.get("req_body") or {}).get("messages") or []
                               for b in as_blocks(m.get("content")) if b.get("type") in THINKING_TYPES)
                note = f" msgs={len((row.get('req_body') or {}).get('messages') or [])} replayed_thinking={replayed} dropped={len(drops)} out={kinds}"
        print(f"[pt] #{row['seq']} {row.get('status')} {row.get('path')}{note}", file=sys.stderr, flush=True)


# Flags PT_PROXY_ARGS may carry. Environment is a weaker boundary than the command line: a project's .envrc or a
# CI config can set it. So it may only shape how the credential is presented, never where it goes or who can
# reach the proxy (--upstream, --bind, --insecure-bind, --allow-any-path, --key-fd, --auth stay argv-only).
ENV_ARG_FLAGS = {"--auth-scheme", "--extra-beta", "--metadata-user-id"}


def env_default_args() -> list[str]:
    """Default flags from PT_PROXY_ARGS (shell-split), so pt_run.sh and pt_selftest.py pick them up unchanged."""
    words = shlex.split(os.environ.get("PT_PROXY_ARGS", ""))
    for word in words:
        if word.startswith("-") and word.split("=", 1)[0] not in ENV_ARG_FLAGS:
            raise SystemExit(f"PT_PROXY_ARGS may only set {', '.join(sorted(ENV_ARG_FLAGS))}; pass {word.split('=', 1)[0]} on the command line instead")
    if words and not words[0].startswith("-"):
        raise SystemExit("PT_PROXY_ARGS must start with a flag")
    return words


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--harness", required=True, help="name of the harness under test (used in the report)")
    ap.add_argument("--run-id", default=time.strftime("%Y%m%d-%H%M%S"))
    ap.add_argument("--out", help="output directory (default ~/pt-audit/<harness>/<run-id>-<mode>)")
    ap.add_argument("--mode", choices=["observe", "error", "drop_block", "strip"], default="drop_block")
    ap.add_argument("--port", type=int, default=8484)
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--upstream", default="https://api.anthropic.com")
    ap.add_argument("--auth", choices=["env", "passthrough"], default="env",
                    help="env: the proxy attaches PT_UPSTREAM_API_KEY / ANTHROPIC_API_KEY from its own environment and the "
                         "harness holds a dummy key; passthrough: forward the harness's own credential")
    ap.add_argument("--force-model", default="claude-fable-5-1", help="rewrite `model` to this id ('' to keep the harness's model)")
    ap.add_argument("--passthrough-model-regex", default="haiku",
                    help="requests whose model matches are forwarded untouched (small side models)")
    ap.add_argument("--no-repair", dest="repair", action="store_false",
                    help="do not fix unrelated 400s (budgeted thinking, sampling params, forced tool_choice, prefill); "
                         "by default they are fixed so the session gets far enough to exercise the prefix check, and each fix is logged as a finding")
    ap.add_argument("--override-binding", action="store_true", help="replace a prefix_mismatch_behavior the harness set itself")
    ap.add_argument("--no-observe-report", dest="observe_report", action="store_false",
                    help="in observe mode, do not add the binding beta (which makes the API list drops in input_transformations)")
    ap.add_argument("--key-fd", type=int, default=None,
                    help="read the real API key from this file descriptor instead of the environment (pt_run.sh uses it)")
    ap.add_argument("--control-token", default=os.environ.get("PT_CONTROL_TOKEN") or None,
                    help="require ?token=<this> on /__pt/mark so only the driver's mark helper can write markers")
    ap.add_argument("--insecure-bind", action="store_true",
                    help="allow --bind to a non-loopback address; anyone who can reach the port can spend the real key")
    ap.add_argument("--allow-any-path", action="store_true",
                    help="forward every path and method, not only Messages, count_tokens and models")
    ap.add_argument("--metadata-user-id", default=None,
                    help="add this metadata.user_id when the harness sends none (off by default)")
    ap.add_argument("--auth-scheme", choices=["x-api-key", "bearer"], default="x-api-key",
                    help="send the upstream credential as an x-api-key header (default) or as a bearer token, for a gateway that wants one")
    ap.add_argument("--extra-beta", action="append", default=[], help="anthropic-beta value to add to every forwarded request (repeatable)")
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args(env_default_args() + sys.argv[1:])
    os.environ.pop("PT_CONTROL_TOKEN", None)
    if args.bind not in ("127.0.0.1", "localhost") and not args.insecure_bind:
        raise SystemExit(f"refusing to bind {args.bind}: the proxy attaches a real API key to what it forwards. Use loopback, or pass --insecure-bind.")
    if not args.out:
        args.out = os.path.expanduser(f"~/pt-audit/{args.harness}/{args.run_id}-{args.mode}")
    state = State(args)
    if args.auth != "passthrough":
        state.auth.headers()  # fail fast if the credential is missing
    Handler.state = state
    try:
        server = ThreadingHTTPServer((args.bind, args.port), Handler)
    except OSError as exc:
        raise SystemExit(f"cannot listen on {args.bind}:{args.port} ({exc.strerror or exc}); stop the other pt_proxy or pass --port") from None
    server.daemon_threads = True
    print(f"[pt] {args.harness} mode={args.mode} model={args.force_model or '(harness)'} listening on http://{args.bind}:{args.port}\n"
          f"[pt] upstream: {args.upstream}  auth: {args.auth}/{args.auth_scheme}  extra betas: {', '.join(args.extra_beta) or 'none'}\n"
          f"[pt] log: {state.log_path}\n"
          f"[pt] point the harness at it: ANTHROPIC_BASE_URL=http://{args.bind}:{args.port} ANTHROPIC_API_KEY=sk-ant-dummy",
          file=sys.stderr, flush=True)
    # The log row is written after the response is relayed, so a client can finish and stop the proxy before the
    # row lands. On SIGTERM or Ctrl-C, stop accepting, then give in-flight requests a moment to write their rows.
    def stop(*_: Any) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    deadline = time.time() + 5
    while state.inflight and time.time() < deadline:
        time.sleep(0.02)
    server.server_close()


if __name__ == "__main__":
    main()

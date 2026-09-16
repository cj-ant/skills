#!/usr/bin/env python3
"""A fake Messages API that applies the documented preserved-thinking rules, for offline evals.

It is written from the public docs only and shares no code with the skill, so it can disagree
with the skill's analyzer. It is not the real API: a green offline run means the analyzer agrees
with this reading of the docs. `pt_selftest.py` against the live API stays the ground truth.

  https://platform.claude.com/docs/en/build-with-claude/preserved-thinking

Rules applied:
- Model check: a block minted by claude-fable-5-1 is unreadable by other models and is dropped
  for that request (`model_binding_mismatch`). Never an error.
- Prefix check, on claude-fable-5-1 when the request sets `prefix_mismatch_behavior`: `system`,
  the set of non-deferred `tools`, and the messages before the block must equal what was sent when
  the block was minted. Thinking blocks, `cache_control`, and JSON key order are not compared.
  The first failing block and every later thinking block fail: a 400, or a drop under "drop_block".
- Predecessor rule: kept thinking blocks must be an unbroken run. Trimming from the start is fine.
  A gap, or a block put back after it was removed, fails the blocks after it.
- `block_binding` without the beta header is a 400. `input_transformations` is returned only
  under the beta header. `count_tokens` runs the same check and never reports transformations.
- Request rules for claude-fable-5-1: budgeted or disabled thinking, non-default sampling
  parameters, forced `tool_choice`, and prefill are 400s.

Stdlib only.   fake_api.py <port>
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

BETA = "thinking-binding-controls-2026-08-01"
THINKING = ("thinking", "redacted_thinking")
LOCK = threading.Lock()
MINTED: dict[str, dict[str, Any]] = {}
COUNTER = [0]


def protected(model: str) -> bool:
    return "fable-5-1" in model


def blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in (content or []) if isinstance(b, dict)]


def scrub(value: Any) -> Any:
    """Drop cache_control everywhere; key order is handled by sort_keys at hashing time."""
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items() if k != "cache_control"}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def h(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def prefix_digest(
    body: dict[str, Any],
    messages: list[dict[str, Any]],
    same_turn: list[dict[str, Any]],
) -> str:
    tools = sorted(
        (scrub(t) for t in body.get("tools") or [] if not t.get("defer_loading")),
        key=lambda t: str(t.get("name")),
    )
    msgs = [
        {
            "role": m.get("role"),
            "content": [
                scrub(b)
                for b in blocks(m.get("content"))
                if b.get("type") not in THINKING
            ],
        }
        for m in messages
    ]
    turn = [scrub(b) for b in same_turn if b.get("type") not in THINKING]
    return h(
        {
            "system": scrub(blocks(body.get("system"))),
            "tools": tools,
            "messages": msgs,
            "turn": turn,
        }
    )


def thinking_config(body: dict[str, Any]) -> dict[str, Any]:
    value = body.get("thinking")
    return value if isinstance(value, dict) else {}


def config_error(body: dict[str, Any], betas: str) -> str | None:
    thinking = thinking_config(body)
    if thinking.get("block_binding") is not None and BETA not in betas:
        return "thinking.block_binding: Extra inputs are not permitted"
    if not protected(str(body.get("model"))):
        return None
    if thinking.get("type") in ("enabled", "disabled"):
        return f'"thinking.type.{thinking["type"]}" is not supported for this model. Use "thinking.type.adaptive" and "output_config.effort" to control thinking behavior.'
    for key in ("temperature", "top_p", "top_k"):
        if key in body and not (key == "temperature" and body[key] == 1):
            return f"fake_api: a non-default `{key}` is not supported for this model."
    if isinstance(body.get("tool_choice"), dict) and body["tool_choice"].get(
        "type"
    ) in ("tool", "any"):
        return 'tool_choice: type "tool" and "any" are not supported for this model.'
    msgs = body.get("messages") or []
    if msgs and msgs[-1].get("role") == "assistant":
        return "fake_api: a trailing assistant message (prefill) is not supported for this model."
    return None


def check(body: dict[str, Any]) -> tuple[list[dict[str, str]], str | None, str | None]:
    """(drops, 400 message or None, signature of the last thinking block sent)."""
    model = str(body.get("model"))
    thinking = thinking_config(body)
    behavior = (thinking.get("block_binding") or {}).get("prefix_mismatch_behavior")
    enforce = protected(model) and behavior in ("error", "drop_block")
    drops: list[dict[str, str]] = []
    failed = False
    pred: str | None = None
    messages = body.get("messages") or []
    for i, msg in enumerate(messages):
        if msg.get("role") != "assistant":
            continue
        for j, block in enumerate(blocks(msg.get("content"))):
            if block.get("type") not in THINKING:
                continue
            path = f"messages.{i}.content.{j}"
            sig = block.get("signature") or block.get("data") or ""
            rec = MINTED.get(sig)
            if rec is None:
                return [], f"{path}: Invalid `signature` in `thinking` block", pred
            if rec["protected"] and not protected(model):
                drops.append(
                    {
                        "type": "thinking_dropped",
                        "path": path,
                        "reason": "model_binding_mismatch",
                    }
                )
                pred = sig
                continue
            if enforce and rec["protected"] and not failed:
                same = (
                    prefix_digest(body, messages[:i], blocks(msg.get("content"))[:j])
                    == rec["prefix"]
                )
                chained = pred is None or pred == rec["pred"]
                failed = not (same and chained)
                if failed and behavior == "error":
                    return (
                        [],
                        (
                            f"{path}: Invalid `signature` in `thinking` block. The block is bound to a different conversation. Remove the block, or set "
                            '`thinking.block_binding.prefix_mismatch_behavior` to "drop_block".'
                        ),
                        pred,
                    )
            if enforce and failed and rec["protected"]:
                drops.append(
                    {
                        "type": "thinking_dropped",
                        "path": path,
                        "reason": "prefix_binding_mismatch",
                    }
                )
            pred = sig
    return drops, None, pred


def reply(body: dict[str, Any], pred: str | None) -> list[dict[str, Any]]:
    """One thinking block, then a tool call for a prompt that names a file, or an answer."""
    messages = body.get("messages") or []
    last = blocks(messages[-1].get("content")) if messages else []
    with LOCK:
        COUNTER[0] += 1
        n = COUNTER[0]
    sig = f"fake-sig-{n}-{h([n, body.get('model')])[:24]}"
    content: list[dict[str, Any]] = [
        {"type": "thinking", "thinking": "", "signature": sig}
    ]
    # Walk through every file the user has named, one tool round per file, then answer.
    named: list[str] = []
    for m in messages:
        if m.get("role") == "user":
            for b in blocks(m.get("content")):
                if b.get("type") == "text" and "<system-reminder>" not in b.get("text", ""):
                    named += re.findall(r"[\w./-]+\.(?:txt|py|md)", b.get("text", ""))
    already = {str((b.get("input") or {}).get("path")) for m in messages if m.get("role") == "assistant"
               for b in blocks(m.get("content")) if b.get("type") == "tool_use"}
    wanted = [f for f in dict.fromkeys(named) if f not in already][:1]
    tool = next(
        (t for t in body.get("tools") or []
         if not t.get("defer_loading") and "path" in json.dumps(t.get("input_schema", {}))),
        None,
    )
    if wanted and tool:
        content += [
            {"type": "text", "text": f"I'll look at {wanted[0]} next."},
            {
                "type": "tool_use",
                "id": f"toolu_fake_{n}",
                "name": tool["name"],
                "input": {"path": wanted[0]},
            },
        ]
    else:
        content.append({"type": "text", "text": f"Fake answer {n}."})
    with LOCK:
        MINTED[sig] = {
            "protected": protected(str(body.get("model"))),
            "pred": pred,
            "prefix": prefix_digest(body, messages, []),
        }
    return content


def sse(message: dict[str, Any]) -> bytes:
    out: list[tuple[str, dict[str, Any]]] = []
    content = message.pop("content")
    out.append(
        (
            "message_start",
            {"type": "message_start", "message": {**message, "content": []}},
        )
    )
    for idx, block in enumerate(content):
        if block["type"] == "thinking":
            out.append(
                (
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": idx,
                        "content_block": {
                            "type": "thinking",
                            "thinking": "",
                            "signature": "",
                        },
                    },
                )
            )
            out.append(
                (
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": idx,
                        "delta": {
                            "type": "signature_delta",
                            "signature": block["signature"],
                        },
                    },
                )
            )
        elif block["type"] == "text":
            out.append(
                (
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": idx,
                        "content_block": {"type": "text", "text": ""},
                    },
                )
            )
            out.append(
                (
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": idx,
                        "delta": {"type": "text_delta", "text": block["text"]},
                    },
                )
            )
        else:
            out.append(
                (
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": idx,
                        "content_block": {**block, "input": {}},
                    },
                )
            )
            out.append(
                (
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": idx,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": json.dumps(block["input"]),
                        },
                    },
                )
            )
        out.append(("content_block_stop", {"type": "content_block_stop", "index": idx}))
    out.append(
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": message["stop_reason"]},
                "usage": {"output_tokens": message["usage"]["output_tokens"]},
            },
        )
    )
    out.append(("message_stop", {"type": "message_stop"}))
    return "".join(
        f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in out
    ).encode()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - name fixed by the base class
        del format, args

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(
            self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}"
        )
        betas = self.headers.get("anthropic-beta") or ""
        counting = self.path.rstrip("/").endswith("/count_tokens")
        if not counting and not self.path.rstrip("/").endswith("/messages"):
            return self.send_json(
                404,
                {
                    "type": "error",
                    "error": {
                        "type": "not_found_error",
                        "message": "fake_api serves /v1/messages only",
                    },
                },
            )
        error = config_error(body, betas) if not counting else None
        drops: list[dict[str, str]] = []
        pred = None
        if error is None:
            drops, error, pred = check(body)
        if error:
            return self.send_json(
                400,
                {
                    "type": "error",
                    "error": {"type": "invalid_request_error", "message": error},
                },
            )
        size = len(json.dumps(body)) // 4
        if counting:
            return self.send_json(200, {"input_tokens": size})
        content = reply(body, pred)
        message: dict[str, Any] = {
            "id": f"msg_fake_{COUNTER[0]}",
            "type": "message",
            "role": "assistant",
            "model": body.get("model"),
            "content": content,
            "stop_reason": "tool_use"
            if any(b["type"] == "tool_use" for b in content)
            else "end_turn",
            "usage": {
                "input_tokens": size,
                "output_tokens": 40,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        }
        if BETA in betas:
            message["input_transformations"] = drops
        if body.get("stream"):
            raw = sse(message)
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return None
        return self.send_json(200, message)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler)
    server.daemon_threads = True
    server.serve_forever()

"""Shared helpers for the preserved-thinking audit tools.

The canonical form here follows the public preserved-thinking docs
(https://platform.claude.com/docs/en/build-with-claude/preserved-thinking):
the checked prefix is the top-level `system`, the set of `tools`, and every
message before a thinking block. `thinking`/`redacted_thinking` blocks and
`cache_control` markers are not part of it, a tool with `defer_loading: true`
is ignored, and JSON formatting and key order don't matter (so 1.0 == 1 here).
Everything else is compared exactly as sent. This is a local diff, not the
API's implementation: the API's own verdict stays the ground truth, and this
form exists to explain a verdict and to find edits the API never sees because
the harness stripped the thinking first.
"""

from __future__ import annotations

import sys

if sys.version_info < (3, 10):  # noqa: UP036 - say it in one line instead of a TypeError deep in a run
    raise SystemExit(f"preserved-thinking-audit needs Python 3.10 or newer; this is {sys.version.split()[0]}")

import copy
import hashlib
import json
from collections.abc import Iterable
from typing import Any

BINDING_BETA = "thinking-binding-controls-2026-08-01"
THINKING_TYPES = frozenset({"thinking", "redacted_thinking"})
# Blocks that never enter the checked prefix.
UNBOUND_BLOCK_TYPES = THINKING_TYPES
SIGNATURE_ERROR_CLAUSE = "Invalid `signature` in `"
PREFIX_REASON = "The block is bound to a different conversation"
# Models that run the prefix check and whose thinking older models cannot read.
PROTECTED_MODEL_MARKERS = ("fable-5-1",)


def digest(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def _collapse_numbers(value: Any) -> Any:
    if isinstance(value, float) and value.is_integer() and abs(value) < 2**53:
        return int(value)
    if isinstance(value, dict):
        return {k: _collapse_numbers(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_collapse_numbers(v) for v in value]
    return value


def as_blocks(content: Any) -> list[dict[str, Any]]:
    """Message or system content as a list of blocks (a bare string is one text block)."""
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, dict):
        return [content]
    return [b if isinstance(b, dict) else {"type": "text", "text": str(b)} for b in content]


# `cache_control` markers are not part of the checked prefix.
IGNORED_BLOCK_KEYS = ("cache_control",)


def _canon_block(block: dict[str, Any]) -> dict[str, Any] | None:
    b = copy.deepcopy(block)
    for key in IGNORED_BLOCK_KEYS:
        b.pop(key, None)
    btype = b.get("type")
    if btype in ("image", "document"):
        src = b.get("source")
        if isinstance(src, dict):
            src.pop("cache_control", None)
            if src.get("type") == "url":
                # The API checks the fetched bytes, not the URL string. We cannot
                # see the bytes, so a URL source is treated as opaque-but-equal.
                src.pop("url", None)
                src["_pt_url_media"] = True
    if btype == "tool_result":
        inner = b.get("content")
        if isinstance(inner, (list, str)):
            kept = [c for c in (_canon_block(x) for x in as_blocks(inner)) if c is not None]
            b["content"] = kept
    return _collapse_numbers(b)


def canon_blocks(content: Any, *, keep_unbound: bool = False) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for block in as_blocks(content):
        if not keep_unbound and block.get("type") in UNBOUND_BLOCK_TYPES:
            continue
        c = _canon_block(block)
        if c is not None:
            out.append(c)
    return out


def canon_message(msg: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"role": msg.get("role"), "content": canon_blocks(msg.get("content"))}
    if msg.get("clear_at") == "next_user_message":
        out["clear_at"] = "next_user_message"
    if msg.get("output_config"):
        out["output_config"] = _collapse_numbers(msg["output_config"])
    return out


def canon_system(system: Any) -> list[dict[str, Any]]:
    return canon_blocks(system)


def split_tools(tools: Any) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """(inline tools sorted by name, deferred tools by name). The docs describe the checked part as "the set of `tools`", so order is not compared here."""
    inline: list[dict[str, Any]] = []
    deferred: dict[str, dict[str, Any]] = {}
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        t = copy.deepcopy(tool)
        t.pop("cache_control", None)
        t = _collapse_numbers(t)
        name = str(t.get("name") or t.get("mcp_server_name") or t.get("type"))
        if t.get("defer_loading") is True:
            deferred[name] = t
        else:
            inline.append(t)
    inline.sort(key=lambda t: str(t.get("name") or t.get("mcp_server_name") or t.get("type")))
    return inline, deferred


def domain_start(messages: list[dict[str, Any]]) -> int:
    """Index where the checked prefix starts: the last applied server-side compaction block."""
    start = 0
    for i, msg in enumerate(messages):
        for block in as_blocks(msg.get("content")):
            if block.get("type") == "compaction" and block.get("content") is not None:
                start = i
    return start


def message_digests(messages: Iterable[dict[str, Any]]) -> list[str]:
    return [digest(canon_message(m)) for m in messages]


def thinking_key(block: dict[str, Any]) -> str | None:
    """Stable identity of a thinking block: its signature (or the redacted data)."""
    if block.get("type") == "thinking":
        return block.get("signature") or None
    if block.get("type") == "redacted_thinking":
        return block.get("data") or None
    return None


def iter_thinking(messages: list[dict[str, Any]]) -> Iterable[tuple[int, int, dict[str, Any]]]:
    for i, msg in enumerate(messages):
        if msg.get("role") != "assistant":
            continue
        for j, block in enumerate(as_blocks(msg.get("content"))):
            if block.get("type") in THINKING_TYPES:
                yield i, j, block


def is_protected_model(model: str | None) -> bool:
    return bool(model) and any(marker in model for marker in PROTECTED_MODEL_MARKERS)


def shorten(text: Any, limit: int = 160) -> str:
    s = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, sort_keys=True)
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def first_text_difference(a: str, b: str, context: int = 60) -> dict[str, str]:
    """The first differing region of two strings, with a little context on each side."""
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    lo = max(0, i - context)
    return {"before": shorten(a[lo : i + context], 200), "after": shorten(b[lo : i + context], 200), "offset": str(i)}


def blocks_text(blocks: Iterable[dict[str, Any]]) -> str:
    return "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text")


def load_jsonl(path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        lines = [ln.strip() for ln in fh if ln.strip()]
    for n, line in enumerate(lines):
        try:
            rows.append(json.loads(line))
        except ValueError:
            # A proxy killed mid-write leaves a truncated last row. Anything earlier is real corruption.
            if n != len(lines) - 1:
                raise
            print(f"warning: dropped a truncated final row in {path}", file=sys.stderr)
    return rows

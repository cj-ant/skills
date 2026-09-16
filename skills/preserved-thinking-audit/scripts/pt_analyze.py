#!/usr/bin/env python3
"""Turn a pt_proxy log into findings.json.

For every thinking block a harness replays, look up the exchange that minted
it (by signature) and recompute the prefix comparison locally: system, tools,
and messages before the block, then and now. That names the edit precisely.
Then align every request with its parent request to find edits even when no
thinking was replayed (a harness that strips thinking hides its prefix edits
from the API; they surface here as "latent"). The API's own verdict (400 text,
input_transformations) is recorded next to the local one.

Usage: pt_analyze.py <run-dir> [<run-dir> ...]   -> writes <run-dir>/findings.json
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pt_lib import (  # noqa: E402
    BINDING_BETA, PREFIX_REASON, SIGNATURE_ERROR_CLAUSE, THINKING_TYPES, as_blocks, blocks_text,
    canon_blocks, canon_message, canon_system, digest, domain_start, first_text_difference, is_protected_model,
    iter_thinking, load_jsonl, message_digests, shorten, split_tools, thinking_key,
)

PATH_RE = re.compile(r"messages\.(\d+)\.content\.(\d+)")
REMINDER_RE = re.compile(r"system-reminder|<reminder|environment_details|<env>|current (date|time)|todo", re.I)


@dataclass
class Ex:
    row: dict[str, Any]
    seq: int = 0
    req: dict[str, Any] = field(default_factory=dict)
    sent: dict[str, Any] = field(default_factory=dict)
    resp: dict[str, Any] | None = None
    status: int = 0
    passthrough: bool = False
    parent: "Ex | None" = None
    children: list["Ex"] = field(default_factory=list)
    _digests: list[str] | None = None
    _full_digests: list[str] | None = None

    def __post_init__(self) -> None:
        r = self.row
        self.seq = r["seq"]
        self.req = r.get("req_body") or {}
        self.sent = r.get("req_body_sent") or self.req
        body = r.get("resp_body")
        self.resp = body if isinstance(body, dict) else None
        self.status = int(r.get("status") or 0)
        if self.status == 200 and self.resp and self.resp.get("type") == "error":
            self.status = 400
        self.passthrough = any(m.get("kind") == "passthrough_model" for m in r.get("mutations") or [])

    @property
    def messages(self) -> list[dict[str, Any]]:
        return self.req.get("messages") or []

    @property
    def ok(self) -> bool:
        return self.status == 200 and bool(self.resp) and self.resp.get("type") == "message"

    @property
    def resp_content(self) -> list[dict[str, Any]]:
        return (self.resp or {}).get("content") or [] if self.ok else []

    @property
    def model_orig(self) -> str:
        return str(self.req.get("model") or "")

    @property
    def model_sent(self) -> str:
        return str(self.sent.get("model") or "")

    def full_messages(self) -> list[dict[str, Any]]:
        msgs = list(self.messages)
        if self.ok:
            msgs.append({"role": "assistant", "content": self.resp_content})
        return msgs

    def digests(self) -> list[str]:
        if self._digests is None:
            self._digests = message_digests(self.messages)
        return self._digests

    def full_digests(self) -> list[str]:
        if self._full_digests is None:
            self._full_digests = message_digests(self.full_messages())
        return self._full_digests

    def error_message(self) -> str:
        if self.resp and self.resp.get("type") == "error":
            return str((self.resp.get("error") or {}).get("message") or "")
        return ""

    def is_signature_400(self) -> bool:
        return self.status == 400 and SIGNATURE_ERROR_CLAUSE in self.error_message()

    def drops(self) -> list[dict[str, Any]]:
        return list((self.resp or {}).get("input_transformations") or []) if self.ok else []

    def replayed(self) -> list[tuple[int, int, dict[str, Any]]]:
        return list(iter_thinking(self.messages))

    def has_mut(self, kind: str) -> list[dict[str, Any]]:
        return [m for m in self.row.get("mutations") or [] if m.get("kind") == kind]


def tools_diff(a: Any, b: Any) -> dict[str, Any]:
    ia, da = split_tools(a)
    ib, db = split_tools(b)
    na = {str(t.get("name") or t.get("type")): t for t in ia}
    nb = {str(t.get("name") or t.get("type")): t for t in ib}
    changed = sorted(n for n in na.keys() & nb.keys() if digest(na[n]) != digest(nb[n]))
    detail = {}
    for n in changed[:5]:
        fields = [k for k in set(na[n]) | set(nb[n]) if digest(na[n].get(k)) != digest(nb[n].get(k))]
        detail[n] = fields
    return {"added": sorted(nb.keys() - na.keys()), "removed": sorted(na.keys() - nb.keys()), "changed": changed,
            "changed_fields": detail, "deferred_added": sorted(db.keys() - da.keys()),
            "deferred_removed": sorted(da.keys() - db.keys()), "same": digest(ia) == digest(ib)}


def system_diff(a: Any, b: Any) -> dict[str, Any] | None:
    ca, cb = canon_system(a), canon_system(b)
    if digest(ca) == digest(cb):
        return None
    ta, tb = blocks_text(ca), blocks_text(cb)
    return {"blocks_before": len(ca), "blocks_after": len(cb), "chars_before": len(ta), "chars_after": len(tb),
            **first_text_difference(ta, tb)}


def describe_message_change(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    ob, nb = canon_blocks(old.get("content")), canon_blocks(new.get("content"))
    od, nd = [digest(b) for b in ob], [digest(b) for b in nb]
    out: dict[str, Any] = {"role_before": old.get("role"), "role_after": new.get("role"),
                           "blocks_before": [b.get("type") for b in ob], "blocks_after": [b.get("type") for b in nb]}
    removed = [b for b, d in zip(ob, od, strict=True) if d not in nd]
    added = [b for b, d in zip(nb, nd, strict=True) if d not in od]
    hints: list[str] = []
    if sorted(od) == sorted(nd) and od != nd:
        hints.append("blocks_reordered")
    for b in removed:
        if b.get("type") == "text" and REMINDER_RE.search(b.get("text", "")):
            hints.append("injected_reminder_removed")
        if b.get("type") == "image":
            hints.append("image_removed")
    for b in added:
        if b.get("type") == "text" and REMINDER_RE.search(b.get("text", "")):
            hints.append("reminder_inserted_into_old_message")
    if len(removed) == 1 and len(added) == 1 and removed[0].get("type") == added[0].get("type"):
        r, a = removed[0], added[0]
        if r.get("type") == "text":
            out["text_change"] = first_text_difference(r.get("text", ""), a.get("text", ""))
            hints.append("text_edited")
        elif r.get("type") == "tool_result":
            before, after = json.dumps(r.get("content")), json.dumps(a.get("content"))
            out["tool_result_chars"] = [len(before), len(after)]
            hints.append("tool_result_truncated" if len(after) < len(before) else "tool_result_rewritten")
            out["tool_result_change"] = first_text_difference(before, after)
        elif r.get("type") == "tool_use":
            hints.append("tool_use_input_changed" if digest(r.get("input")) != digest(a.get("input")) else "tool_use_changed")
            out["tool_use_change"] = first_text_difference(json.dumps(r, sort_keys=True), json.dumps(a, sort_keys=True))
    out["removed"] = [shorten(b, 140) for b in removed[:3]]
    out["added"] = [shorten(b, 140) for b in added[:3]]
    out["hints"] = sorted(set(hints))
    return out


def align(parent: Ex, child: Ex) -> list[dict[str, Any]]:
    """Classify how child's messages differ from parent's messages + response."""
    pd, qd = parent.full_digests(), child.digests()
    pm, qm = parent.full_messages(), child.messages
    sm = difflib.SequenceMatcher(None, pd, qd, autojunk=False)
    events: list[dict[str, Any]] = []
    ops = sm.get_opcodes()
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            continue
        at_end_p = i2 == len(pd)
        ev: dict[str, Any] = {"p_range": [i1, i2], "q_range": [j1, j2]}
        if tag == "insert":
            if at_end_p and j2 == len(qd):
                continue  # appended turns
            ev["kind"] = "messages_inserted"
            ev["detail"] = [shorten(canon_message(m), 160) for m in qm[j1:j2][:3]]
        elif tag == "delete":
            if at_end_p:
                ev["kind"] = "tail_truncated"  # rewind / fork / response not echoed: allowed
            elif i1 == 0:
                ev["kind"] = "head_removed"
            else:
                ev["kind"] = "messages_removed"
            ev["detail"] = [shorten(canon_message(m), 160) for m in pm[i1:i2][:3]]
        else:  # replace
            response_idx = len(parent.messages) if parent.ok else None
            if at_end_p and response_idx is not None and i1 == response_idx and j2 > j1:
                ev["kind"] = "assistant_echo_differs"
                ev["change"] = describe_message_change(pm[i1], qm[j1])
            elif at_end_p and j2 == len(qd) and i1 >= len(parent.messages) - 1:
                ev["kind"] = "tail_rewritten"  # retry that rewrites the last user turn: allowed
            elif i1 == 0 and (i2 - i1) >= 3 and (j2 - j1) <= 2:
                ev["kind"] = "head_replaced_by_summary"
                ev["detail"] = [shorten(canon_message(m), 200) for m in qm[j1:j2]]
                ev["replaced_messages"] = i2 - i1
            elif (i2 - i1) == (j2 - j1):
                ev["kind"] = "messages_modified"
                ev["changes"] = [{"p_index": i1 + k, "q_index": j1 + k, **describe_message_change(pm[i1 + k], qm[j1 + k])}
                                 for k in range(min(i2 - i1, 4))]
            elif (i2 - i1) > (j2 - j1) and (j2 - j1) <= 2 and (i2 - i1) >= 3:
                ev["kind"] = "span_replaced_by_summary"
                ev["detail"] = [shorten(canon_message(m), 200) for m in qm[j1:j2]]
                ev["replaced_messages"] = i2 - i1
            else:
                ev["kind"] = "messages_rewritten"
                ev["detail"] = {"before": [shorten(canon_message(m), 120) for m in pm[i1:i2][:3]],
                                "after": [shorten(canon_message(m), 120) for m in qm[j1:j2][:3]]}
        events.append(ev)
    return events


EDIT_KINDS = {"messages_inserted", "head_removed", "messages_removed", "head_replaced_by_summary",
              "span_replaced_by_summary", "messages_modified", "messages_rewritten", "assistant_echo_differs"}


def find_parent(child: Ex, earlier: list[Ex]) -> Ex | None:
    qd = child.digests()
    if not qd:
        return None
    best: tuple[int, int, Ex] | None = None
    for cand in earlier[-80:]:
        if cand.passthrough != child.passthrough or not cand.messages:
            continue
        pd = cand.full_digests()
        sm = difflib.SequenceMatcher(None, pd, qd, autojunk=False)
        blocks = sm.get_matching_blocks()
        score = sum(b.size for b in blocks)
        if score == 0:
            continue
        starts_together = any(b.a == 0 and b.b == 0 and b.size > 0 for b in blocks)
        if score < 2 and not starts_together:
            continue
        key = (score, cand.seq)
        if best is None or key > (best[0], best[1]):
            best = (score, cand.seq, cand)
    return best[2] if best else None


def check_block(child: Ex, i: int, j: int, block: dict[str, Any], minted: dict[str, tuple[Ex, int]]) -> dict[str, Any]:
    key = thinking_key(block)
    out: dict[str, Any] = {"path": f"messages.{i}.content.{j}", "empty_text": block.get("type") == "thinking" and not block.get("thinking")}
    if not key or key not in minted:
        out["local"] = "unknown_origin"
        return out
    origin, bidx = minted[key]
    out["minted_seq"] = origin.seq
    ds_q = domain_start(child.messages)
    if i < ds_q:
        out["local"] = "pre_compaction"
        return out
    causes: list[str] = []
    if digest(canon_system(origin.req.get("system"))) != digest(canon_system(child.req.get("system"))):
        causes.append("system_changed")
    td = tools_diff(origin.req.get("tools"), child.req.get("tools"))
    if not td["same"]:
        causes.append("tools_changed")
    ds_p = domain_start(origin.messages)
    exp = message_digests(origin.messages[ds_p:])
    act = message_digests(child.messages[ds_q:i])
    if exp != act:
        causes.append("messages_changed")
        k = 0
        while k < min(len(exp), len(act)) and exp[k] == act[k]:
            k += 1
        out["first_message_divergence"] = {"minted_index": ds_p + k, "replayed_index": ds_q + k,
                                           "minted_len": len(exp), "replayed_len": len(act)}
    if digest(canon_blocks(origin.resp_content[:bidx])) != digest(canon_blocks(as_blocks(child.messages[i].get("content"))[:j])):
        causes.append("same_turn_blocks_changed")
    # predecessor chain: the thinking block kept immediately before this one must be the one that was there at mint
    pred_q = None
    for pi, pj, pb in iter_thinking(child.messages[: i + 1]):
        if (pi, pj) < (i, j) and pi >= ds_q:
            pred_q = thinking_key(pb)
    pred_p = None
    for _, _, pb in iter_thinking(origin.messages[ds_p:]):
        pred_p = thinking_key(pb)
    for pb in origin.resp_content[:bidx]:
        if pb.get("type") in THINKING_TYPES:
            pred_p = thinking_key(pb)
    if pred_q is not None and pred_q != pred_p:
        causes.append("predecessor_mismatch")
    if is_protected_model(origin.model_sent) and not is_protected_model(child.model_sent):
        causes.append("model_cannot_read")
    out["local"] = "ok" if not causes else "mismatch"
    out["causes"] = causes
    return out


def analyze(run_dir: str) -> dict[str, Any]:
    log = os.path.join(run_dir, "exchanges.jsonl")
    if not os.path.exists(log):
        raise SystemExit(f"no requests reached the proxy: {log} does not exist. The harness probably never used the proxy URL; "
                         "check its base-URL setting (references/harness-wiring.md) and proxy.log in the same directory.")
    rows = load_jsonl(log)
    refused = [r for r in rows if r.get("refused")]
    if refused and not any(r.get("is_messages") for r in rows if r.get("kind") == "exchange" and not r.get("refused")):
        paths = sorted({str(r.get("path")) for r in refused})[:5]
        print(f"warning: the proxy refused every request it got ({', '.join(paths)}). A path like /v1/v1/messages means the harness's "
              "base URL should not end in /v1; a bare /messages means it should.", file=sys.stderr)
    meta = json.load(open(os.path.join(run_dir, "run.json"), encoding="utf-8")) if os.path.exists(os.path.join(run_dir, "run.json")) else {}
    markers = [r for r in rows if r.get("kind") == "marker"]
    exs = [Ex(r) for r in rows if r.get("kind") == "exchange" and r.get("is_messages") and isinstance(r.get("req_body"), dict)]
    counts = [Ex(r) for r in rows if r.get("kind") == "exchange" and r.get("is_count_tokens") and isinstance(r.get("req_body"), dict)]
    exs.sort(key=lambda e: e.seq)

    minted: dict[str, tuple[Ex, int]] = {}
    for e in exs:
        for bidx, b in enumerate(e.resp_content):
            k = thinking_key(b)
            if k:
                minted.setdefault(k, (e, bidx))

    for idx, e in enumerate(exs):
        e.parent = find_parent(e, exs[:idx])
        if e.parent:
            e.parent.children.append(e)

    def marker_before(e: Ex) -> str | None:
        prev = [m for m in markers if m["seq"] < e.seq]
        if not prev:
            return None
        last = prev[-1]
        between = [x for x in exs if last["seq"] < x.seq < e.seq and not x.passthrough]
        return last["label"] if not between else None

    per_ex: list[dict[str, Any]] = []
    evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    exercised: Counter[str] = Counter()
    stats = Counter()
    replay_by_turn: list[dict[str, Any]] = []

    for e in exs:
        rec: dict[str, Any] = {"seq": e.seq, "status": e.status, "model_orig": e.model_orig, "model_sent": e.model_sent,
                               "messages": len(e.messages), "passthrough": e.passthrough, "parent": e.parent.seq if e.parent else None,
                               "marker": marker_before(e), "stream": bool(e.row.get("stream"))}
        muts = e.row.get("mutations") or []
        rec["mutations"] = [m["kind"] for m in muts]
        if e.passthrough:
            stats["side_model_requests"] += 1
            per_ex.append(rec)
            continue
        stats["requests"] += 1

        # --- config checks on what the harness sent
        thinking = e.req.get("thinking")
        if e.has_mut("repair_budgeted_thinking"):
            evidence["CFG-1"].append({"seq": e.seq, "sent": thinking, "note": "thinking.type 'enabled' with budget_tokens is rejected; Fable 5.1 is adaptive-only"})
        if e.has_mut("repair_thinking_disabled"):
            evidence["CFG-1"].append({"seq": e.seq, "sent": thinking, "note": "thinking disabled"})
        for m in e.has_mut("repair_sampling_param"):
            evidence["CFG-2"].append({"seq": e.seq, "param": m["param"], "value": m["value"]})
        for m in e.has_mut("repair_forced_tool_choice"):
            evidence["CFG-3"].append({"seq": e.seq, "tool_choice": m["value"]})
        for m in e.has_mut("repair_prefill"):
            evidence["CFG-4"].append({"seq": e.seq, "prefill": m["prefill"]})
        own_binding = ((thinking or {}).get("block_binding") or {}).get("prefix_mismatch_behavior") if isinstance(thinking, dict) else None
        harness_betas = (e.row.get("req_headers") or {}).get("anthropic-beta", "")
        rec["harness_binding"] = own_binding
        rec["harness_sends_binding_beta"] = BINDING_BETA in harness_betas
        if own_binding:
            stats[f"harness_binding_{own_binding}"] += 1
        if own_binding and BINDING_BETA not in harness_betas:
            evidence["CFG-5"].append({"seq": e.seq, "note": "block_binding sent without the thinking-binding-controls beta header (400 outside the proxy)"})

        # --- replayed thinking: local verdict per block
        replayed = e.replayed()
        rec["replayed_thinking"] = len(replayed)
        stats["replayed_blocks"] += len(replayed)
        block_verdicts = [check_block(e, i, j, b, minted) for i, j, b in replayed]
        local_bad = [v for v in block_verdicts if v.get("local") == "mismatch"]
        rec["local_mismatches"] = len(local_bad)
        if e.ok:
            stats["responses"] += 1
            minted_here = [b for b in e.resp_content if b.get("type") in THINKING_TYPES]
            rec["minted_thinking"] = len(minted_here)
            stats["minted_blocks"] += len(minted_here)
            if minted_here:
                stats["responses_with_thinking"] += 1
            usage = (e.resp or {}).get("usage") or {}
            for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
                stats[k] += int(usage.get(k) or 0)

        # --- API verdict
        drops = e.drops()
        rec["api_drops"] = [{"path": d.get("path"), "reason": d.get("reason")} for d in drops]
        for d in drops:
            stats[f"drop_{d.get('reason')}"] += 1
        if drops:
            stats["requests_with_drops"] += 1
        if e.is_signature_400():
            msg = e.error_message()
            rec["api_400"] = msg
            stats["signature_400s"] += 1
            reason = "prefix" if PREFIX_REASON in msg else "other"
            rec["api_400_reason"] = reason
        elif e.status >= 400:
            rec["api_error"] = shorten(e.error_message() or str(e.row.get("proxy_error")), 300)
            stats["other_errors"] += 1
            evidence["ERR-0"].append({"seq": e.seq, "status": e.status, "message": rec["api_error"]})

        # --- parent alignment: prefix edits regardless of thinking
        parent = e.parent
        drops = e.drops()
        if parent is not None:
            exercised["continuations"] += 1
            events = align(parent, e)
            rec["events"] = [ev["kind"] for ev in events]
            sysd = system_diff(parent.req.get("system"), e.req.get("system"))
            tdiff = tools_diff(parent.req.get("tools"), e.req.get("tools"))
            # thinking replayed at or after an edit point = the edit is live; otherwise it is latent (masked)
            confirmed = bool(local_bad) or bool(drops) or e.is_signature_400()

            def active_after(q_index: int, confirmed: bool = confirmed, replayed: list = replayed) -> bool:
                # live only when thinking sits after the edit AND a block actually mismatched (locally or per the API)
                return confirmed and any(i >= q_index for i, _, _ in replayed)
            label = rec["marker"]
            if sysd:
                item = {"seq": e.seq, "parent": parent.seq, "active": bool(replayed) and confirmed, "marker": label, **sysd}
                evidence["PFX-6" if label == "resume" else "PFX-1"].append(item)
            if not tdiff["same"]:
                item = {"seq": e.seq, "parent": parent.seq, "active": bool(replayed) and confirmed, "marker": label,
                        **{k: tdiff[k] for k in ("added", "removed", "changed", "changed_fields")}}
                evidence["PFX-6" if label == "resume" else "PFX-2"].append(item)
            if tdiff["deferred_added"] or tdiff["deferred_removed"]:
                stats["deferred_tool_changes"] += 1
            for ev in events:
                kind = ev["kind"]
                q0 = ev["q_range"][0]
                item = {"seq": e.seq, "parent": parent.seq, "active": active_after(q0), "marker": label, **ev}
                if kind == "assistant_echo_differs":
                    evidence["FID-3"].append(item)
                elif kind in ("head_replaced_by_summary", "span_replaced_by_summary"):
                    exercised["compaction"] += 1
                    kept_thinking = [(i, j) for i, j, _ in replayed if i >= ev["q_range"][1]]
                    item["kept_tail_thinking_blocks"] = len(kept_thinking)
                    item["active"] = bool(kept_thinking)
                    if kept_thinking:
                        evidence["PFX-5"].append(item)
                    else:
                        evidence["PFX-5-ok"].append(item)
                elif kind == "head_removed":
                    evidence["PFX-4"].append(item)
                elif kind in ("messages_modified", "messages_removed", "messages_inserted", "messages_rewritten"):
                    evidence["PFX-6" if label == "resume" else "PFX-3"].append(item)
            if label:
                exercised[f"marker:{label}"] += 1
            # model switch inside a lineage
            if parent.model_orig and e.model_orig and parent.model_orig != e.model_orig:
                exercised["model_switch"] += 1
                parent_minted = [thinking_key(b) for b in parent.resp_content if b.get("type") in THINKING_TYPES]
                lineage_minted = {thinking_key(b) for _, _, b in parent.replayed()} | set(parent_minted)
                still = {thinking_key(b) for _, _, b in replayed}
                lost = [k for k in lineage_minted if k and k not in still]
                if lineage_minted and lost:
                    evidence["MDL-1"].append({"seq": e.seq, "from": parent.model_orig, "to": e.model_orig,
                                              "blocks_before": len(lineage_minted), "blocks_stripped_by_client": len(lost)})
                else:
                    evidence["MDL-1-ok"].append({"seq": e.seq, "from": parent.model_orig, "to": e.model_orig})
        if rec["marker"] == "compaction" and not any(k in (rec.get("events") or []) for k in ("head_replaced_by_summary", "span_replaced_by_summary")):
            exercised["compaction"] += 1
            stale = [v for v in block_verdicts if v.get("local") == "mismatch"]
            item = {"seq": e.seq, "kind": "marked_compaction", "messages_after": len(e.messages), "replayed_thinking": len(replayed),
                    "stale_thinking_blocks": len(stale), "api_drops": len(e.drops()), "active": bool(stale or e.drops() or e.is_signature_400())}
            evidence["PFX-5" if item["active"] else "PFX-5-ok"].append(item)
        if parent is None and replayed:
            # thinking replayed into a request with no recognisable parent conversation
            origins = {v.get("minted_seq") for v in block_verdicts if v.get("minted_seq")}
            evidence["PFX-7"].append({"seq": e.seq, "replayed_blocks": len(replayed), "minted_in": sorted(o for o in origins if o),
                                      "system_head": shorten(blocks_text(canon_system(e.req.get("system"))), 140),
                                      "local_mismatches": len(local_bad)})
        if parent is None and e.messages and len(e.messages) <= 2 and not replayed:
            stats["fresh_conversations"] += 1

        # --- fidelity of the echoed assistant turn (first child only sees the fresh response)
        if parent is not None and parent.ok and parent.children and parent.children[0] is e:
            idx = len(parent.messages)
            minted_keys = [thinking_key(b) for b in parent.resp_content if b.get("type") in THINKING_TYPES]
            if minted_keys and idx < len(e.messages) and e.messages[idx].get("role") == "assistant":
                echoed = as_blocks(e.messages[idx].get("content"))
                echoed_keys = [thinking_key(b) for b in echoed if b.get("type") in THINKING_TYPES]
                kept = [k for k in minted_keys if k in echoed_keys]
                unsigned = [b for b in echoed if b.get("type") == "thinking" and not b.get("signature")]
                empties = [b for b in parent.resp_content if b.get("type") == "thinking" and not b.get("thinking")]
                empties_kept = [b for b in empties if thinking_key(b) in echoed_keys]
                turn = {"seq": e.seq, "minted_seq": parent.seq, "minted": len(minted_keys), "echoed": len(kept),
                        "empty_text_minted": len(empties), "empty_text_echoed": len(empties_kept), "unsigned_thinking_sent": len(unsigned)}
                replay_by_turn.append(turn)
                stats["echo_turns"] += 1
                stats["echo_minted"] += len(minted_keys)
                stats["echo_kept"] += len(kept)
                if not kept:
                    stats["echo_turns_all_stripped"] += 1
                    evidence["FID-1"].append({**turn, "note": "assistant turn came back with none of its thinking blocks"})
                elif len(kept) < len(minted_keys):
                    evidence["FID-1"].append({**turn, "note": "assistant turn came back with only some of its thinking blocks"})
                if empties and len(empties_kept) < len(empties):
                    note = ("thinking blocks with an empty `thinking` field were dropped while others were kept" if kept else
                            "every thinking block in this turn had an empty `thinking` field and none came back; look in the code for a filter "
                            "on the thinking text. If there is none, the harness strips all thinking (LZY-2)")
                    evidence["FID-2"].append({**turn, "note": note})
                if unsigned:
                    evidence["FID-1"].append({**turn, "note": "thinking sent back without a signature (lossy internal format)"})
                # order / content of non-thinking blocks
                a = [digest(b) for b in canon_blocks(parent.resp_content)]
                b_ = [digest(b) for b in canon_blocks(echoed)]
                if a != b_ and sorted(a) == sorted(b_):
                    evidence["FID-3"].append({"seq": e.seq, "parent": parent.seq, "kind": "blocks_reordered", "active": bool(local_bad) or bool(drops),
                                              "before": [x.get("type") for x in canon_blocks(parent.resp_content)],
                                              "after": [x.get("type") for x in canon_blocks(echoed)]})
            elif minted_keys:
                stats["echo_turns_missing"] += 1

        # --- does thinking from earlier turns survive into this request?
        if parent is not None and parent.ok:
            expected = [thinking_key(b) for _, _, b in parent.replayed()] + \
                       [thinking_key(b) for b in parent.resp_content if b.get("type") in THINKING_TYPES]
            expected = [k for k in expected if k]
            events_here = rec.get("events") or []
            legit_reset = any(k in ("head_replaced_by_summary", "span_replaced_by_summary", "tail_truncated", "tail_rewritten") for k in events_here)
            model_change = parent.model_orig != e.model_orig
            if expected and not legit_reset and not model_change:
                have = {thinking_key(b) for _, _, b in replayed}
                lost = [k for k in expected if k not in have]
                last = e.messages[-1] if e.messages else {}
                new_user_turn = last.get("role") == "user" and not any(b.get("type") == "tool_result" for b in as_blocks(last.get("content")))
                stats["survival_expected"] += len(expected)
                stats["survival_kept"] += len(expected) - len(lost)
                if new_user_turn:
                    stats["userturn_expected"] += len(expected)
                    stats["userturn_kept"] += len(expected) - len(lost)
                    stats["userturn_samples"] += 1
                if lost:
                    evidence["FID-1"].append({"seq": e.seq, "parent": parent.seq, "marker": rec["marker"], "carried_by_parent": len(expected),
                                              "still_present": len(expected) - len(lost), "at_new_user_turn": new_user_turn,
                                              "note": "thinking that was in the history on the previous request is gone from this one"
                                                      + (" (discarded when the next user turn started)" if new_user_turn else "")})
        if not isinstance(e.req.get("thinking"), dict):
            stats["requests_without_thinking_config"] += 1

        # --- predecessor holes
        for v in block_verdicts:
            if "predecessor_mismatch" in v.get("causes", []):
                api_agrees = bool(drops) or e.is_signature_400()
                evidence["FID-4"].append({"seq": e.seq, "active": api_agrees, **v})
        # --- reconcile local vs API
        api_paths = {d.get("path") for d in drops if d.get("reason") == "prefix_binding_mismatch"}
        if e.is_signature_400():
            m = PATH_RE.search(e.error_message())
            if m:
                api_paths.add(f"messages.{m.group(1)}.content.{m.group(2)}")
        local_paths = {v["path"] for v in local_bad if set(v.get("causes", [])) - {"model_cannot_read"}}
        rec["api_prefix_paths"] = sorted(p for p in api_paths if p)
        if api_paths and not local_paths:
            evidence["RECON"].append({"seq": e.seq, "note": "API reported a prefix mismatch the local diff cannot explain (minted before the recording started, URL media bytes, or a rule this tool does not model)",
                                      "api_paths": sorted(p for p in api_paths if p)})
        if local_paths and not api_paths and e.status == 200 and meta.get("mode") in ("error", "drop_block"):
            evidence["RECON"].append({"seq": e.seq, "note": "local diff predicts a mismatch the API did not report (the API is more lenient here; treat as informational)",
                                      "local_paths": sorted(local_paths), "causes": sorted({c for v in local_bad for c in v.get("causes", [])})})
        if api_paths or (e.is_signature_400() and rec.get("api_400_reason") == "prefix"):
            first = sorted(local_bad, key=lambda v: [int(x) for x in PATH_RE.search(v["path"]).groups()])[0] if local_bad else None
            evidence["API-PREFIX"].append({"seq": e.seq, "status": e.status, "dropped": len(api_paths),
                                           "first_local_cause": (first or {}).get("causes"), "message": rec.get("api_400")})
        model_drops = [d for d in drops if d.get("reason") == "model_binding_mismatch"]
        if model_drops:
            evidence["MDL-2"].append({"seq": e.seq, "dropped": len(model_drops), "model": e.model_sent})
        per_ex.append(rec)

    # --- what the harness did after a signature 400
    for e in exs:
        if not e.is_signature_400() or e.passthrough:
            continue
        later = [x for x in exs if x.seq > e.seq and not x.passthrough][:3]
        nxt = later[0] if later else None
        outcome: dict[str, Any] = {"seq": e.seq}
        if nxt is None:
            outcome["next"] = "no_further_request"
        else:
            same = digest(nxt.req) == digest(e.req)
            n_th = len(nxt.replayed())
            nb = ((nxt.req.get("thinking") or {}).get("block_binding") or {}).get("prefix_mismatch_behavior") if isinstance(nxt.req.get("thinking"), dict) else None
            if same:
                outcome["next"] = "blind_retry_same_body"
            elif nb == "drop_block":
                outcome["next"] = "retried_with_drop_block"
            elif n_th == 0 and len(e.replayed()) > 0 and message_digests(nxt.messages) == message_digests(e.messages):
                outcome["next"] = "stripped_thinking_and_retried"
            elif nxt.parent is None:
                outcome["next"] = "started_new_conversation"
            else:
                outcome["next"] = "different_request"
            outcome["next_seq"] = nxt.seq
            outcome["next_status"] = nxt.status
        evidence["ERR-1"].append(outcome)
    for c in counts:
        if c.status == 400 and SIGNATURE_ERROR_CLAUSE in c.error_message():
            evidence["ERR-3"].append({"seq": c.seq, "message": shorten(c.error_message(), 200)})

    # URL media
    for e in exs:
        urls = [b for m in e.messages for b in as_blocks(m.get("content")) if b.get("type") in ("image", "document")
                and isinstance(b.get("source"), dict) and b["source"].get("type") == "url"]
        if urls:
            evidence["PFX-8"].append({"seq": e.seq, "url_media_blocks": len(urls)})
            break

    mode = meta.get("mode")
    checks = build_checks(evidence, exercised, stats, mode)
    metrics = build_metrics(stats, exs, replay_by_turn)
    findings = {"harness": meta.get("harness") or os.path.basename(os.path.dirname(run_dir.rstrip("/"))), "run_id": meta.get("run_id"),
                "mode": mode, "run_dir": os.path.abspath(run_dir), "force_model": meta.get("force_model"),
                "markers": [{"seq": m["seq"], "label": m["label"], "note": m.get("note")} for m in markers],
                "metrics": metrics, "checks": checks, "exchanges": per_ex, "replay_by_turn": replay_by_turn,
                "reconcile": evidence.get("RECON", []), "api_prefix_events": evidence.get("API-PREFIX", [])}
    findings["verdict"] = verdict(findings)
    with open(os.path.join(run_dir, "findings.json"), "w", encoding="utf-8") as fh:
        json.dump(findings, fh, indent=1, ensure_ascii=False)
    return findings


def build_metrics(stats: Counter[str], exs: list[Ex], replay_by_turn: list[dict[str, Any]]) -> dict[str, Any]:
    def ratio(a: int, b: int) -> float | None:
        return round(a / b, 4) if b else None
    total_in = stats["input_tokens"] + stats["cache_read_input_tokens"] + stats["cache_creation_input_tokens"]
    server_drops = stats["drop_prefix_binding_mismatch"] + stats["drop_model_binding_mismatch"]
    return {
        "requests": stats["requests"], "responses": stats["responses"], "side_model_requests": stats["side_model_requests"],
        "thinking_blocks_minted": stats["minted_blocks"], "thinking_blocks_replayed_total": stats["replayed_blocks"],
        "response_thinking_rate": ratio(stats["responses_with_thinking"], stats["responses"]),
        "echo_turns": stats["echo_turns"],
        "client_keep_rate": ratio(stats["echo_kept"], stats["echo_minted"]),
        "client_strip_rate": None if not stats["echo_minted"] else round(1 - stats["echo_kept"] / stats["echo_minted"], 4),
        "turns_with_all_thinking_stripped": stats["echo_turns_all_stripped"],
        "history_keep_rate": ratio(stats["survival_kept"], stats["survival_expected"]),
        "cross_user_turn_keep_rate": ratio(stats["userturn_kept"], stats["userturn_expected"]),
        "cross_user_turn_samples": stats["userturn_samples"],
        "requests_without_thinking_config": stats["requests_without_thinking_config"],
        "server_dropped_blocks_prefix": stats["drop_prefix_binding_mismatch"],
        "server_dropped_blocks_model": stats["drop_model_binding_mismatch"],
        "requests_with_server_drops": stats["requests_with_drops"],
        "request_drop_rate": ratio(stats["requests_with_drops"], stats["requests"]),
        "server_drop_share_of_replayed": ratio(server_drops, stats["replayed_blocks"]),
        "signature_400s": stats["signature_400s"], "other_errors": stats["other_errors"],
        "harness_sets_drop_block": stats["harness_binding_drop_block"], "harness_sets_error": stats["harness_binding_error"],
        "input_tokens": stats["input_tokens"], "output_tokens": stats["output_tokens"],
        "cache_read_input_tokens": stats["cache_read_input_tokens"], "cache_creation_input_tokens": stats["cache_creation_input_tokens"],
        "cache_read_share": ratio(stats["cache_read_input_tokens"], total_in),
        "output_tokens_per_response": ratio(stats["output_tokens"], stats["responses"]),
    }


CHECK_DEFS: list[tuple[str, str, str]] = [
    ("CFG-1", "config", "Sends adaptive thinking (no budgeted or disabled thinking)"),
    ("CFG-2", "config", "Sends no temperature / top_p / top_k"),
    ("CFG-3", "config", "Never forces tool_choice (tool / any)"),
    ("CFG-4", "config", "Never prefills the assistant turn"),
    ("CFG-5", "config", "block_binding always travels with the binding beta header"),
    ("FID-1", "fidelity", "Replays every thinking block with its signature"),
    ("FID-2", "fidelity", "Keeps thinking blocks whose `thinking` text is empty"),
    ("FID-3", "fidelity", "Echoes the assistant turn exactly as returned (order, text, tool input)"),
    ("FID-4", "fidelity", "Never removes a thinking block from the middle of the history"),
    ("PFX-1", "prefix", "Top-level system prompt is byte-stable for the session"),
    ("PFX-2", "prefix", "Inline tools are stable for the session"),
    ("PFX-3", "prefix", "Earlier messages are never edited, removed, or inserted between"),
    ("PFX-4", "prefix", "No sliding window / head truncation"),
    ("PFX-5", "prefix", "Compaction leaves no stale thinking behind"),
    ("PFX-6", "prefix", "Resume replays the persisted system, tools, and messages"),
    ("PFX-7", "prefix", "Side requests never carry thinking into a different conversation"),
    ("PFX-8", "prefix", "No URL-sourced media whose bytes can change"),
    ("MDL-1", "model", "Keeps thinking blocks in history across a model switch"),
    ("MDL-2", "model", "Model-check drops are tolerated (not treated as a failure)"),
    ("ERR-1", "errors", "Recovers from the signature 400 (drop_block or strip, once) instead of dying or looping"),
    ("ERR-3", "errors", "count_tokens 400s are handled"),
    ("LZY-1", "shortcut", "Does not lean on drop_block to hide an unstable prefix"),
    ("LZY-2", "shortcut", "Does not strip all thinking to dodge the check"),
]


def build_checks(evidence: dict[str, list[dict[str, Any]]], exercised: Counter[str], stats: Counter[str], mode: str | None) -> list[dict[str, Any]]:
    conts = exercised["continuations"]
    enough = conts >= 3
    out: list[dict[str, Any]] = []

    def status_for(cid: str) -> tuple[str, list[dict[str, Any]], str]:
        ev = evidence.get(cid, [])
        note = ""
        if cid.startswith("CFG"):
            if ev:
                return "fail", ev, ""
            if cid == "CFG-1" and stats["requests_without_thinking_config"]:
                return "info", [], f"{stats['requests_without_thinking_config']} requests carried no `thinking` object; Fable 5.1 thinks adaptively by default, older models would not think at all"
            return ("pass" if stats["requests"] else "not_exercised"), [], ""
        if cid == "FID-1":
            if not stats["echo_minted"]:
                return "not_exercised", [], "no assistant turn with thinking was followed by another request"
            if ev:
                keep = stats["echo_kept"] / stats["echo_minted"]
                hist = (stats["survival_kept"] / stats["survival_expected"]) if stats["survival_expected"] else None
                turn = (stats["userturn_kept"] / stats["userturn_expected"]) if stats["userturn_expected"] else None
                bits = [f"immediate echo keeps {keep:.0%}"]
                if hist is not None:
                    bits.append(f"history keeps {hist:.0%} request over request")
                if turn is not None:
                    bits.append(f"{turn:.0%} survives a new user turn")
                return "fail", ev, "; ".join(bits)
            return "pass", [], f"{stats['echo_kept']}/{stats['echo_minted']} blocks echoed and none lost later"
        if cid in ("FID-2", "FID-3", "FID-4"):
            if ev:
                active = any(x.get("active", True) for x in ev)
                note = ""
                if cid == "FID-3" and not active:
                    note = ("the echoed turn differs from the response, but consistently, so later thinking binds to the altered form; "
                            "it breaks the day the form changes again (a reload from storage, a serializer upgrade)")
                if cid == "FID-4" and not active:
                    note = ("an interior thinking block was removed; this mode does not arm the check, see the drop_block or error run" if mode not in ("error", "drop_block")
                            else "an interior thinking block was removed and the API reported no drop on this run; the docs say later blocks become invalid, so treat it as a fault")
                return ("fail" if active else "latent"), ev, note
            return ("pass" if stats["echo_turns"] else "not_exercised"), [], ""
        if cid in ("PFX-1", "PFX-2", "PFX-3", "PFX-4"):
            if ev:
                active = any(x.get("active") for x in ev)
                return ("fail" if active else "latent"), ev, "" if active else "edit seen but no thinking was replayed after it, so the API had nothing to reject"
            return ("pass" if enough else "not_exercised"), [], f"{conts} continuation requests compared"
        if cid == "PFX-5":
            if ev:
                return "fail", ev, "kept turns still carry thinking minted before the summary"
            if evidence.get("PFX-5-ok"):
                return "pass", evidence["PFX-5-ok"], "compaction seen; nothing stale replayed after it"
            return "not_exercised", [], "no compaction observed (force one and mark it: $PT_MARK compaction)"
        if cid == "PFX-6":
            if ev:
                active = any(x.get("active") for x in ev)
                return ("fail" if active else "latent"), ev, ""
            return ("pass" if exercised["marker:resume"] else "not_exercised"), [], "mark a resume with $PT_MARK resume before the first request after restart"
        if cid == "PFX-7":
            if ev:
                bad = [x for x in ev if x.get("local_mismatches")]
                return ("fail" if bad else "info"), ev, "thinking replayed into a request that is not a continuation of the conversation that minted it"
            return ("pass" if stats["requests"] > 3 else "not_exercised"), [], ""
        if cid == "PFX-8":
            return ("info", ev, "URL-sourced media present; safe only if the bytes never change (prefer file_id or base64)") if ev else ("pass", [], "")
        if cid == "MDL-1":
            if ev:
                return "fail", ev, "the client removed thinking blocks on the switch; they cannot come back when the session returns to the newer model"
            if evidence.get("MDL-1-ok"):
                return "pass", evidence["MDL-1-ok"], ""
            return "not_exercised", [], "switch models mid-session (mark it: $PT_MARK model_switch)"
        if cid == "MDL-2":
            return ("info", ev, "API dropped blocks the serving model cannot read; expected, not an integration bug") if ev else ("not_exercised", [], "")
        if cid == "ERR-1":
            if not ev:
                return ("not_exercised" if mode != "error" or not stats["signature_400s"] else "pass"), [], "run in --mode error with a prefix edit to see the harness's 400 path"
            good = {"retried_with_drop_block", "stripped_thinking_and_retried"}
            bad = [x for x in ev if x.get("next") not in good]
            return ("fail" if bad else "pass"), ev, ""
        if cid == "ERR-3":
            return ("info", ev, "count_tokens returned the signature 400") if ev else ("not_exercised", [], "")
        if cid == "LZY-1":
            drops = stats["drop_prefix_binding_mismatch"]
            if stats["harness_binding_drop_block"] and drops:
                return "fail", [{"harness_sets_drop_block_on_requests": stats["harness_binding_drop_block"], "prefix_drops": drops,
                                 "requests_with_drops": stats["requests_with_drops"]}], "harness opts into drop_block and the API is dropping thinking: the prefix is still being edited"
            if stats["harness_binding_drop_block"]:
                return "pass", [], "harness sets drop_block and nothing was dropped"
            return "info", [], "harness does not set prefix_mismatch_behavior itself"
        if cid == "LZY-2":
            if stats["echo_turns"] >= 2 and stats["echo_kept"] == 0:
                return "fail", [{"echo_turns": stats["echo_turns"], "minted": stats["echo_minted"], "echoed": 0}], "no thinking block was ever sent back: the model re-derives its reasoning every turn"
            if stats["userturn_expected"] and stats["userturn_kept"] == 0:
                return "fail", [{"user_turns_sampled": stats["userturn_samples"], "blocks_carried": stats["userturn_expected"], "blocks_kept": 0}], \
                    "thinking is replayed inside a tool loop but none survives the next user turn, so every turn starts cold and later prefix edits are hidden"
            return ("pass" if stats["echo_turns"] else "not_exercised"), [], ""
        return "not_exercised", [], note

    for cid, group, title in CHECK_DEFS:
        st, ev, note = status_for(cid)
        out.append({"id": cid, "group": group, "title": title, "status": st, "note": note, "evidence": ev[:12], "evidence_count": len(ev)})
    return out


def verdict(f: dict[str, Any]) -> dict[str, str]:
    by = {c["id"]: c for c in f["checks"]}
    m = f["metrics"]
    fails = [c["id"] for c in f["checks"] if c["status"] == "fail"]
    latent = [c["id"] for c in f["checks"] if c["status"] == "latent"]
    if by["LZY-2"]["status"] == "fail":
        if m.get("client_keep_rate"):
            text = ("Replays thinking inside a tool loop, then discards all of it at the next user turn. Requests succeed, every turn starts without "
                    "earlier reasoning, and prefix edits between turns are hidden from the API")
        else:
            text = "Strips all thinking client-side. Requests succeed, reasoning never carries forward, and any prefix edits are hidden from the API"
        hidden = [c["id"] for c in f["checks"] if c["status"] == "latent"]
        return {"label": "MASKED", "summary": text + (" (latent: " + ", ".join(hidden) + ")." if hidden else ".")}
    pfx_fail = [i for i in fails if i.startswith(("PFX", "FID"))]
    if m["signature_400s"] and by["ERR-1"]["status"] == "fail":
        return {"label": "BREAKS", "summary": "An enforced account gets a 400 the harness does not recover from."}
    if pfx_fail and (m["server_dropped_blocks_prefix"] or m["signature_400s"]):
        return {"label": "DEGRADED", "summary": "Works only because thinking is dropped: the prefix is edited between requests (" + ", ".join(pfx_fail) + ")."}
    if pfx_fail:
        return {"label": "DEGRADED", "summary": "Prefix or fidelity edits found: " + ", ".join(pfx_fail) + "."}
    if latent:
        return {"label": "LATENT", "summary": "No thinking was lost in this run, but edits were seen that will break once thinking is replayed after them: " + ", ".join(latent) + "."}
    if any(i.startswith("CFG") for i in fails):
        return {"label": "CONFIG", "summary": "Prefix handling looked clean, but the request config is rejected by Fable 5.1 without the proxy's repairs: " + ", ".join(i for i in fails if i.startswith("CFG")) + "."}
    unex = [c["id"] for c in f["checks"] if c["status"] == "not_exercised" and c["id"].startswith(("PFX", "FID", "MDL-1"))]
    if unex:
        return {"label": "CLEAN-SO-FAR", "summary": "Nothing failed, but these were not exercised: " + ", ".join(unex) + "."}
    return {"label": "READY", "summary": "Append-only, verbatim replay, no drops, no 400s across every exercised scenario."}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dirs", nargs="+")
    args = ap.parse_args()
    for d in args.run_dirs:
        f = analyze(d)
        m = f["metrics"]
        print(f"{f['harness']} [{f['mode']}] {f['verdict']['label']}: {f['verdict']['summary']}")
        print(f"  requests={m['requests']} minted={m['thinking_blocks_minted']} client_keep_rate={m['client_keep_rate']} "
              f"server_prefix_drops={m['server_dropped_blocks_prefix']} signature_400s={m['signature_400s']} cache_read_share={m['cache_read_share']}")
        for c in f["checks"]:
            if c["status"] in ("fail", "latent"):
                print(f"  {c['status'].upper():7} {c['id']} {c['title']} ({c['evidence_count']} evidence) {c['note']}")
        print(f"  -> {os.path.join(d, 'findings.json')}")


if __name__ == "__main__":
    main()

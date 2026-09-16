#!/usr/bin/env python3
"""Render findings.json files (one per proxy run) plus optional static.json code-reading verdicts into one HTML report.

    pt_report.py --out report.html ~/pt-audit/<harness>/<run>/findings.json [...] [--static <harness>=path/static.json]

Each harness section opens with action items for its maintainer (fix, decide, not tested); --actions-md writes the same list as Markdown.
Runs are grouped by harness. static.json is a list of {check_id, status, evidence, note} from the code-reading pass.
The page uses plain terminology, so a maintainer who never ran the audit can act on it.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
from collections import defaultdict
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pt_checks import CATALOG, DOCS  # noqa: E402

STATUS_ORDER = {"fail": 0, "latent": 1, "info": 2, "not_exercised": 3, "pass": 4}
STATUS_LABEL = {"fail": "Fails", "latent": "Latent", "info": "Note", "not_exercised": "Not exercised", "pass": "Passes", None: "—"}
GROUP_LABEL = {"config": "Request config", "fidelity": "Replay fidelity", "prefix": "Prefix stability", "model": "Model switching",
               "errors": "Error handling", "shortcut": "Shortcuts"}

CSS = """
:root{--ground:#f4f6f9;--surface:#ffffff;--ink:#15202c;--muted:#5a6a7b;--rule:#d9e0e8;--accent:#1d5c8a;--accent-soft:#e3eef7;
--pass:#1b7a4b;--pass-bg:#e2f3ea;--fail:#b3261e;--fail-bg:#fbe6e4;--latent:#9a5b00;--latent-bg:#fbeed6;--info:#3b5b9c;--info-bg:#e6ecf8;--na:#6a7684;--na-bg:#e9edf1;
--mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;--sans:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif;--cond:"IBM Plex Sans Condensed","IBM Plex Sans",system-ui,sans-serif}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--ground:#0f151b;--surface:#172029;--ink:#e4ebf2;--muted:#93a3b4;--rule:#2a3642;--accent:#7bb6e3;--accent-soft:#1b2c3b;
--pass:#63d19a;--pass-bg:#143224;--fail:#ff8a80;--fail-bg:#3d1a18;--latent:#f0b45a;--latent-bg:#3a2a10;--info:#9db8f2;--info-bg:#1c2942;--na:#93a0ae;--na-bg:#222c37}}
:root[data-theme="dark"]{--ground:#0f151b;--surface:#172029;--ink:#e4ebf2;--muted:#93a3b4;--rule:#2a3642;--accent:#7bb6e3;--accent-soft:#1b2c3b;
--pass:#63d19a;--pass-bg:#143224;--fail:#ff8a80;--fail-bg:#3d1a18;--latent:#f0b45a;--latent-bg:#3a2a10;--info:#9db8f2;--info-bg:#1c2942;--na:#93a0ae;--na-bg:#222c37}
body{background:var(--ground);color:var(--ink);font-family:var(--sans);font-size:15px;line-height:1.55;padding-inline:20px;padding-block:28px 64px}
.wrap{max-width:1080px;margin:0 auto;display:flex;flex-direction:column;gap:28px}
h1{font-family:var(--cond);font-weight:600;font-size:30px;line-height:1.15;margin:0;text-wrap:balance}
h2{font-family:var(--cond);font-weight:600;font-size:22px;margin:0;text-wrap:balance}
h3{font-family:var(--cond);font-weight:600;font-size:16px;margin:0;letter-spacing:.02em;text-transform:uppercase;color:var(--muted)}
p{margin:0;max-width:70ch}
a{color:var(--accent)}
code,.mono{font-family:var(--mono);font-size:.88em}
.lede{color:var(--muted);max-width:75ch}
.eyebrow{font-family:var(--mono);font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.pill{display:inline-block;font-family:var(--mono);font-size:12px;font-weight:600;padding:2px 8px;border-radius:3px;white-space:nowrap}
.s-pass{color:var(--pass);background:var(--pass-bg)}.s-fail{color:var(--fail);background:var(--fail-bg)}.s-latent{color:var(--latent);background:var(--latent-bg)}
.s-info{color:var(--info);background:var(--info-bg)}.s-not_exercised,.s-none{color:var(--na);background:var(--na-bg)}
.verdict{font-family:var(--cond);font-size:15px;font-weight:600;letter-spacing:.06em;padding:3px 10px;border-radius:3px}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:14px}
th{font-family:var(--mono);font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);text-align:left;font-weight:500;padding:6px 10px;border-bottom:1px solid var(--rule);white-space:nowrap}
td{padding:8px 10px;border-bottom:1px solid var(--rule);vertical-align:top}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;font-family:var(--mono)}
.matrix td{padding:5px 6px;text-align:center}.matrix td:first-child{text-align:left;white-space:nowrap;font-weight:600}
.dot{display:inline-block;width:14px;height:14px;border-radius:2px}
.d-pass{background:var(--pass)}.d-fail{background:var(--fail)}.d-latent{background:var(--latent)}.d-info{background:var(--info);opacity:.55}.d-not_exercised,.d-none{background:transparent;box-shadow:inset 0 0 0 1.5px var(--rule)}
.harness{background:var(--surface);border:1px solid var(--rule);border-radius:6px;padding:24px;display:flex;flex-direction:column;gap:22px}
.hhead{display:flex;flex-wrap:wrap;gap:10px 16px;align-items:baseline}
.ledger{display:flex;flex-direction:column;gap:8px}
.bar{display:flex;height:22px;border-radius:3px;overflow:hidden;background:var(--na-bg)}
.bar span{display:block;height:100%}
.b-kept{background:var(--pass)}.b-client{background:var(--latent)}.b-server{background:var(--fail)}.b-model{background:var(--info)}
.legend{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:13px;color:var(--muted)}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px 22px}
.stat b{display:block;font-family:var(--mono);font-size:20px;font-weight:600;font-variant-numeric:tabular-nums}
.stat span{font-size:12.5px;color:var(--muted)}
details{border-top:1px solid var(--rule)}
details>summary{cursor:pointer;padding:9px 0;list-style:none;display:grid;grid-template-columns:64px 118px 118px 1fr;gap:10px;align-items:baseline}
details>summary::-webkit-details-marker{display:none}
details>summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
details[open]>summary{border-bottom:1px dashed var(--rule)}
.cid{font-family:var(--mono);font-size:13px;color:var(--muted)}
.body{padding:12px 0 16px 74px;display:flex;flex-direction:column;gap:10px}
.kv{display:grid;grid-template-columns:76px 1fr;gap:4px 12px;font-size:14px}
.kv dt{font-family:var(--mono);font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);padding-top:3px}
.kv dd{margin:0;max-width:78ch}
pre{font-family:var(--mono);font-size:12px;line-height:1.5;background:var(--ground);border:1px solid var(--rule);border-radius:4px;padding:10px 12px;margin:0;overflow-x:auto;white-space:pre-wrap;word-break:break-word;max-height:340px}
.actions{display:flex;flex-direction:column;gap:10px;border:1px solid var(--rule);border-radius:6px;padding:16px 18px;background:var(--ground)}.actions h4{margin:6px 0 0;font-family:var(--cond);font-size:15px;font-weight:600;display:flex;gap:8px;align-items:center}.actions ol,.actions ul{margin:0;padding-left:22px;display:flex;flex-direction:column;gap:14px}.actions ul.untested{gap:6px;font-size:14px}.actions li p{margin:0 0 4px}.actions li .kv{margin-top:6px}.actions pre{max-height:120px}.grouphead{padding:14px 0 4px}
.note{color:var(--muted);font-size:13.5px}
.colhead{display:grid;grid-template-columns:64px 118px 118px 1fr;gap:10px;font-family:var(--mono);font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);padding-bottom:4px}
@media (max-width:640px){details>summary,.colhead{grid-template-columns:52px 96px 1fr}.colhead span:nth-child(3),details>summary>span:nth-child(3){display:none}.body{padding-left:0}.harness{padding:16px}}
"""


def esc(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def pill(status: str | None) -> str:
    return f'<span class="pill s-{status or "none"}">{esc(STATUS_LABEL.get(status, status))}</span>'


def pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


def worst(statuses: list[str | None]) -> str | None:
    real = [s for s in statuses if s]
    return min(real, key=lambda s: STATUS_ORDER.get(s, 9)) if real else None


# Evidence keys that quote content the harness sent (prompt text, tool output, paths). --redact drops them and keeps the rest:
# request numbers, check ids, counts, lengths, block types, and which section changed.
QUOTING_KEYS = {"before", "after", "detail", "removed", "added", "prefill", "system_head", "message", "text_change", "tool_result_change",
                "tool_use_change", "sent", "tool_choice", "value", "api_400", "api_error"}


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("[redacted]" if k in QUOTING_KEYS else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def evidence_gist(ev: Any) -> str:
    """One line from an evidence row that tells a maintainer what changed, without the whole JSON."""
    if not isinstance(ev, dict):
        return ""

    def clip(v: Any, n: int = 70) -> str:
        t = " ".join(str(v).split())
        return t if len(t) <= n else t[: n - 1] + "…"
    if "before" in ev and "after" in ev:
        return f"before: {clip(ev['before'])} | after: {clip(ev['after'])}"
    for ch in ev.get("changes") or []:
        if isinstance(ch, dict) and ch.get("hints"):
            return ", ".join(ch["hints"]) + (f"; removed: {clip(ch['removed'][0])}" if ch.get("removed") else "")
    if ev.get("hints"):
        return ", ".join(ev["hints"])
    for k in ("note", "kind", "next", "added", "removed", "detail"):
        if ev.get(k):
            return f"{k}: {clip(ev[k])}"
    return ""


def merge_runs(runs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per check: worst status across runs, with evidence tagged by mode."""
    merged: dict[str, dict[str, Any]] = {}
    for run in runs:
        for c in run["checks"]:
            m = merged.setdefault(c["id"], {"id": c["id"], "group": c["group"], "title": c["title"], "statuses": [], "notes": [], "evidence": []})
            m["statuses"].append(c["status"])
            if c.get("note"):
                m["notes"].append(f"[{run['mode']}] {c['note']}")
            for ev in c.get("evidence", [])[:6]:
                m["evidence"].append({"mode": run["mode"], **ev})
    for m in merged.values():
        m["status"] = worst(m["statuses"])
    return merged


def ledger(runs: list[dict[str, Any]]) -> str:
    rows = []
    for run in runs:
        m = run["metrics"]
        minted = m.get("thinking_blocks_minted") or 0
        keeps = [k for k in (m.get("client_keep_rate"), m.get("cross_user_turn_keep_rate")) if k is not None]
        keep = min(keeps) if keeps else None
        replayed = m.get("thinking_blocks_replayed_total") or 0
        sp, sm = m.get("server_dropped_blocks_prefix") or 0, m.get("server_dropped_blocks_model") or 0
        strip_share = 0.0 if keep is None else 1 - keep
        drop_share = (sp / replayed) if replayed else 0.0
        model_share = (sm / replayed) if replayed else 0.0
        kept_share = max(0.0, (1 - strip_share) * (1 - min(1.0, drop_share + model_share)))
        server_share = (1 - strip_share) * min(1.0, drop_share)
        mshare = (1 - strip_share) * min(1.0, model_share)
        bar = (f'<div class="bar" role="img" aria-label="thinking ledger">'
               f'<span class="b-kept" style="width:{kept_share * 100:.1f}%"></span><span class="b-client" style="width:{strip_share * 100:.1f}%"></span>'
               f'<span class="b-server" style="width:{server_share * 100:.1f}%"></span><span class="b-model" style="width:{mshare * 100:.1f}%"></span></div>')
        rows.append(f'<div><div class="eyebrow">mode {esc(run["mode"])} · {minted} blocks minted · {m.get("echo_turns", 0)} echoed turns</div>{bar}</div>')
    legend = ('<div class="legend"><span><i class="b-kept"></i>reached the model again</span><span><i class="b-client"></i>stripped by the harness</span>'
              '<span><i class="b-server"></i>dropped by the API (prefix edited)</span><span><i class="b-model"></i>dropped by the API (model cannot read)</span></div>')
    return f'<div class="ledger"><h3>Thinking ledger</h3>{"".join(rows)}{legend}</div>'


def stats(runs: list[dict[str, Any]]) -> str:
    head = "<tr><th>Mode</th><th class='num'>Requests</th><th class='num' title='thinking blocks present in the request right after the response that produced them'>Echo keep</th><th class='num' title='thinking still present when the next user turn starts'>Next-turn keep</th><th class='num'>Requests w/ drops</th><th class='num'>Blocks dropped</th><th class='num'>Signature 400s</th><th class='num'>Responses that think</th><th class='num'>Output tok / resp</th><th class='num'>Cache read share</th><th>Verdict</th></tr>"
    body = []
    for run in runs:
        m = run["metrics"]
        v = run["verdict"]["label"]
        cls = {"READY": "pass", "CLEAN-SO-FAR": "info", "LATENT": "latent", "CONFIG": "latent", "MASKED": "latent", "DEGRADED": "fail", "BREAKS": "fail"}.get(v, "info")
        body.append(f"<tr><td class='mono'>{esc(run['mode'])}</td><td class='num'>{m['requests']}</td><td class='num'>{pct(m['client_keep_rate'])}</td><td class='num'>{pct(m.get('cross_user_turn_keep_rate'))}</td>"
                    f"<td class='num'>{m['requests_with_server_drops']} ({pct(m['request_drop_rate'])})</td><td class='num'>{m['server_dropped_blocks_prefix']}</td>"
                    f"<td class='num'>{m['signature_400s']}</td><td class='num'>{pct(m['response_thinking_rate'])}</td>"
                    f"<td class='num'>{'—' if m['output_tokens_per_response'] is None else round(m['output_tokens_per_response'])}</td><td class='num'>{pct(m['cache_read_share'])}</td>"
                    f"<td><span class='pill s-{cls}'>{esc(v)}</span></td></tr>")
    return f"<div class='scroll'><table>{head}{''.join(body)}</table></div>"


def check_rows(merged: dict[str, dict[str, Any]], static: dict[str, dict[str, Any]]) -> str:
    out = ['<div class="colhead"><span>Check</span><span>On the wire</span><span>In the code</span><span>What is checked</span></div>']
    by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for m in merged.values():
        by_group[m["group"]].append(m)
    for group in ("prefix", "fidelity", "model", "errors", "shortcut", "config"):
        items = by_group.get(group)
        if not items:
            continue
        out.append(f'<div class="grouphead"><h3>{esc(GROUP_LABEL[group])}</h3></div>')
        for m in sorted(items, key=lambda x: (STATUS_ORDER.get(worst([x["status"], (static.get(x["id"]) or {}).get("status")]) or "pass", 9), x["id"])):
            s = static.get(m["id"]) or {}
            cat = CATALOG.get(m["id"], {})
            open_attr = " open" if worst([m["status"], s.get("status")]) == "fail" else ""
            ev = ""
            if m["evidence"]:
                ev = "<pre>" + esc("\n".join(json.dumps(e, ensure_ascii=False) for e in m["evidence"][:8])) + "</pre>"
            notes = "".join(f'<p class="note">{esc(n)}</p>' for n in dict.fromkeys(m["notes"]))
            sev = ""
            if s:
                sev = f'<dt>Code</dt><dd>{esc(s.get("note") or "")}' + (f'<pre>{esc(s.get("evidence"))}</pre>' if s.get("evidence") else "") + "</dd>"
            out.append(f'<details{open_attr}><summary><span class="cid">{esc(m["id"])}</span><span>{pill(m["status"])}</span><span>{pill(s.get("status"))}</span><span>{esc(m["title"])}</span></summary>'
                       f'<div class="body"><dl class="kv"><dt>Habit</dt><dd>{esc(cat.get("habit"))}</dd><dt>Fix</dt><dd>{esc(cat.get("fix"))}</dd><dt>Verify</dt><dd>{esc(cat.get("verify"))}</dd>{sev}</dl>{notes}{ev}</div></details>')
    return "".join(out)


def timeline(run: dict[str, Any]) -> str:
    rows = []
    for e in run["exchanges"]:
        if e.get("passthrough"):
            continue
        flags = ", ".join(e.get("events") or [])
        drops = len(e.get("api_drops") or [])
        err = "signature 400" if e.get("api_400") else (e.get("api_error") or "")
        rows.append(f"<tr><td class='num'>{e['seq']}</td><td>{esc(e.get('marker') or '')}</td><td class='num'>{e['messages']}</td><td class='num'>{e.get('replayed_thinking', 0)}</td>"
                    f"<td class='num'>{e.get('minted_thinking', '')}</td><td class='num'>{drops or ''}</td><td class='num'>{e['status']}</td><td>{esc(flags)}</td><td>{esc(err[:120])}</td></tr>")
    return ("<details><summary><span class='cid'>log</span><span></span><span></span><span>Request timeline, mode " + esc(run["mode"]) + f" ({len(rows)} requests)</span></summary><div class='body' style='padding-left:0'><div class='scroll'><table>"
            "<tr><th class='num'>#</th><th>Marker</th><th class='num'>Msgs</th><th class='num'>Thinking replayed</th><th class='num'>Minted</th><th class='num'>Dropped</th><th class='num'>Status</th><th>Edits vs parent</th><th>Error</th></tr>"
            + "".join(rows) + "</table></div></div></details>")


GROUP_PRIORITY = {"prefix": 0, "fidelity": 1, "errors": 2, "model": 3, "shortcut": 4, "config": 5}
# Informational checks that still leave the maintainer a choice to make.
DECIDE_WHEN_INFO = {"PFX-7", "PFX-8", "CFG-1", "ERR-3"}
UNTESTED_WORTH_LISTING = ("PFX-", "FID-", "MDL-1", "ERR-1")


def action_items(runs: list[dict[str, Any]], static: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Turn check results into what a maintainer does next: things to fix, things to decide, things nobody tested."""
    merged = merge_runs(runs)
    fix: list[dict[str, Any]] = []
    decide: list[dict[str, Any]] = []
    untested: list[dict[str, Any]] = []
    for cid, cat in CATALOG.items():
        m = merged.get(cid) or {}
        code = static.get(cid) or {}
        status = worst([m.get("status"), code.get("status")])
        per_mode = {r["mode"]: c["status"] for r in runs for c in r["checks"] if c["id"] == cid}
        # Only quote what a run said while the check was in the state being reported, and say it once.
        notes = list(dict.fromkeys(c["note"] for r in runs for c in r["checks"]
                                   if c["id"] == cid and c.get("note") and c["status"] == status))[:1]
        item = {"id": cid, "title": m.get("title") or cat["habit"], "group": m.get("group", "config"), "habit": cat["habit"],
                "do": cat["fix"], "done_when": cat["verify"], "where": code.get("evidence") or "", "code_note": code.get("note") or "",
                "evidence_rows": sum(c.get("evidence_count", 0) for r in runs for c in r["checks"] if c["id"] == cid),
                "wire_note": " ".join(notes)}
        wire_rows = [ev for r in runs for c in r["checks"] if c["id"] == cid for ev in c.get("evidence", [])]
        seqs = sorted({ev["seq"] for ev in wire_rows if isinstance(ev, dict) and isinstance(ev.get("seq"), int)})
        item["requests"] = seqs
        item["gist"] = evidence_gist(wire_rows[0]) if wire_rows else ""
        wire_modes = {st: sorted(mode for mode, s2 in per_mode.items() if s2 == st) for st in ("fail", "latent")}
        reqs = f" (request {', '.join(map(str, seqs[:6]))}{'…' if len(seqs) > 6 else ''})" if seqs else ""
        if wire_modes["fail"]:
            item["seen"] = f"failed on the wire in mode {', '.join(wire_modes['fail'])}{reqs}"
        elif wire_modes["latent"]:
            item["seen"] = f"seen on the wire as latent in mode {', '.join(wire_modes['latent'])}{reqs}: the edit happened, but no thinking was replayed after it in these runs"
        elif code.get("status") in ("fail", "latent"):
            item["seen"] = "found by reading the code; these runs did not show it on the wire"
        else:
            item["seen"] = ""
        if status == "fail":
            fix.append(item)
        elif status == "latent":
            if m.get("status") == "latent":
                item["question"] = ("The harness makes this edit, but no thinking was lost in these runs because nothing was replayed after it. "
                                    "It starts dropping thinking, or returning a 400 on a new account, as soon as thinking is sent back after the edit. "
                                    "Fix it now, or accept that risk and watch input_transformations.")
            else:
                item["question"] = ("The code can make this edit, but these runs did not trigger it. Decide whether the path that triggers it "
                                    "matters to your users. If it does, take the option below; if not, note it as a known limit.")
            decide.append(item)
        elif status == "info" and cid in DECIDE_WHEN_INFO and (m.get("evidence") or item["wire_note"] or item["code_note"]):
            item["question"] = item["wire_note"] or item["code_note"]
            decide.append(item)
        elif status == "not_exercised" and cid.startswith(UNTESTED_WORTH_LISTING):
            item["how"] = item["wire_note"] or "Add the matching step from the session script and run again."
            untested.append(item)
    fix.sort(key=lambda i: (GROUP_PRIORITY.get(i["group"], 9), -i["evidence_rows"], i["id"]))
    decide.sort(key=lambda i: (GROUP_PRIORITY.get(i["group"], 9), i["id"]))
    return {"fix": fix, "decide": decide, "untested": untested}


def actions_html(actions: dict[str, list[dict[str, Any]]]) -> str:
    def row(label: str, value: str, mono: bool = False) -> str:
        if not value:
            return ""
        body = f"<pre>{esc(value)}</pre>" if mono else esc(value)
        return f"<dt>{esc(label)}</dt><dd>{body}</dd>"

    parts = ['<div class="actions"><h3>Action items</h3>']
    if not any(actions.values()):
        parts.append('<p>Nothing to fix or decide from these runs.</p></div>')
        return "".join(parts)
    n = 0
    for kind, heading, cls in (("fix", "Fix", "fail"), ("decide", "Decide", "latent")):
        if not actions[kind]:
            continue
        parts.append(f'<h4><span class="pill s-{cls}">{heading}</span> {len(actions[kind])} item{"s" if len(actions[kind]) != 1 else ""}</h4><ol start="{n + 1}">')
        for it in actions[kind]:
            n += 1
            lead = "" if kind == "fix" else it["question"]
            lead_html = f"<p>{esc(lead)}</p>" if lead else ""
            parts.append(f'<li id="act-{esc(it["id"])}"><p><span class="cid">{esc(it["id"])}</span> <strong>{esc(it["habit"] if kind == "fix" else it["title"])}</strong></p>{lead_html}<dl class="kv">'
                         + row("Do" if kind == "fix" else "Option", it["do"]) + row("Where", it["where"], mono=True) + row("In code", it["code_note"])
                         + row("Seen", ". ".join(x for x in (it.get("seen", ""), it.get("gist", ""), it["wire_note"] if kind == "fix" else "") if x))
                         + row("Verify by", it["done_when"]) + "</dl></li>")
        parts.append("</ol>")
    if actions["untested"]:
        items = "".join(f'<li><span class="cid">{esc(i["id"])}</span> {esc(i["title"])}. {esc(i["how"])}</li>' for i in actions["untested"])
        parts.append(f'<h4><span class="pill s-not_exercised">Not tested</span> {len(actions["untested"])}</h4><ul class="untested">{items}</ul>')
    parts.append("</div>")
    return "".join(parts)


def md_text(value: Any) -> str:
    """Free text for the Markdown list. Evidence and notes are lifted from a third-party harness's source, so
    neutralise HTML (many Markdown renderers pass it through) and keep each value on one line."""
    return " ".join(html.escape("" if value is None else str(value), quote=False).split())


def md_code(value: Any) -> str:
    """An inline code span that survives backticks inside it: fence with a longer backtick run, pad when needed."""
    text = " ".join(("" if value is None else str(value)).split())
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def actions_markdown(harness: str, verdict: dict[str, str], note: str, actions: dict[str, list[dict[str, Any]]]) -> str:
    out = [f"# {md_text(harness)}: preserved thinking action items", "", f"Verdict: **{md_text(verdict['label'])}**. {md_text(verdict['summary'])}"]
    if note:
        out += ["", md_text(note)]
    n = 0
    for kind, heading in (("fix", "Fix"), ("decide", "Decide")):
        if not actions[kind]:
            continue
        out += ["", f"## {heading}", ""]
        for it in actions[kind]:
            n += 1
            out.append(f"{n}. **{it['id']} {it['habit'] if kind == 'fix' else md_text(it['title'])}**")
            if kind == "decide":
                out.append(f"   - {md_text(it['question'])}")
            out.append(f"   - {'Do' if kind == 'fix' else 'Option'}: {it['do']}")
            if it["where"]:
                out.append(f"   - Where: {md_code(it['where'])}")
            if it["code_note"]:
                out.append(f"   - In code: {md_text(it['code_note'])}")
            seen = ". ".join(md_text(x) for x in (it.get("seen", ""), it.get("gist", ""), it["wire_note"] if kind == "fix" else "") if x)
            if seen:
                out.append(f"   - Seen: {seen}")
            out.append(f"   - Verify by: {it['done_when']} (the audit's re-check; some steps name its own tools)")
    if actions["untested"]:
        out += ["", "## Not tested", ""] + [f"- **{i['id']} {md_text(i['title'])}**. {md_text(i['how'])}" for i in actions["untested"]]
    if n == 0 and not actions["untested"]:
        out += ["", "Nothing to fix or decide from these runs."]
    out += ["", f"Rules: {DOCS}", ""]
    return "\n".join(out)


def render(groups: dict[str, list[dict[str, Any]]], statics: dict[str, dict[str, dict[str, Any]]], title: str,
           notes: dict[str, str] | None = None, actions_out: dict[str, Any] | None = None) -> str:
    notes = notes or {}
    check_ids = list(CATALOG)
    matrix_rows = []
    sections = []
    all_actions: dict[str, dict[str, list[dict[str, Any]]]] = {}
    all_verdicts: dict[str, dict[str, str]] = {}
    for harness, runs in groups.items():
        runs.sort(key=lambda r: ["observe", "drop_block", "error", "strip"].index(r["mode"]) if r["mode"] in ("observe", "drop_block", "error", "strip") else 9)
        merged = merge_runs(runs)
        static = statics.get(harness, {})
        actions = action_items(runs, static)
        all_actions[harness] = actions
        cells = []
        for cid in check_ids:
            st = worst([(merged.get(cid) or {}).get("status"), (static.get(cid) or {}).get("status")])
            cells.append(f'<td title="{esc(cid)}: {esc(STATUS_LABEL.get(st))}"><span class="dot d-{st or "none"}"></span></td>')
        order = ["BREAKS", "DEGRADED", "MASKED", "LATENT", "CONFIG", "CLEAN-SO-FAR", "READY"]
        overall = min((r["verdict"] for r in runs), key=lambda v: order.index(v["label"]) if v["label"] in order else 9)
        cls = {"READY": "pass", "CLEAN-SO-FAR": "info", "LATENT": "latent", "CONFIG": "latent", "MASKED": "latent", "DEGRADED": "fail", "BREAKS": "fail"}.get(overall["label"], "info")
        all_verdicts[harness] = overall
        matrix_rows.append(f'<tr><td><a href="#h-{esc(harness)}">{esc(harness)}</a></td><td><span class="pill s-{cls}">{esc(overall["label"])}</span></td>{"".join(cells)}</tr>')
        sections.append(
            f'<section class="harness" id="h-{esc(harness)}"><div class="hhead"><h2>{esc(harness)}</h2><span class="verdict s-{cls}">{esc(overall["label"])}</span>'
            f'<span class="note">model {esc(runs[0].get("force_model") or "as sent")} · runs: {esc(", ".join(r["mode"] for r in runs))}</span></div>'
            f'<p>{esc(overall["summary"])}</p>' + (f'<p class="note">{esc(notes[harness])}</p>' if harness in notes else '') + actions_html(actions) + f'{ledger(runs)}<div><h3>By mode</h3>{stats(runs)}</div>'
            f'<div>{check_rows(merged, static)}</div><div>{"".join(timeline(r) for r in runs)}</div></section>')
    if actions_out is not None:
        actions_out.update({h: {"actions": all_actions[h], "verdict": all_verdicts[h]} for h in all_actions})
    heads = "".join(f'<th title="{esc(CATALOG[c]["habit"])}">{esc(c.replace("-", ""))}</th>' for c in check_ids)
    legend = "".join(f'<span><i class="dot d-{s}" style="width:10px;height:10px"></i>{esc(STATUS_LABEL[s])}</span>' for s in ("fail", "latent", "pass", "info", "not_exercised"))
    return (f'<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{esc(title)}</title>'
            f'<style>{CSS}</style><div class="wrap"><header style="display:flex;flex-direction:column;gap:10px"><div class="eyebrow">Preserved thinking readiness · {time.strftime("%Y-%m-%d")}</div><h1>{esc(title)}</h1>'
            f'<p class="lede">Each harness ran real multi-turn sessions against <code>claude-fable-5-1</code> through a recording proxy that arms the prefix check with the <code>thinking-binding-controls-2026-08-01</code> beta. '
            f'“On the wire” is what the requests did. “In the code” is what a source read predicts. “Latent” means the harness edits the prefix but sent no thinking after the edit, so the API had nothing to reject yet. '
            f'Each harness section opens with its action items: what to fix, what to decide, and what nobody tested yet. The evidence behind each item is in the check list below it. Rules: <a href="{DOCS}">Preserved thinking</a>.</p></header>'
            f'<section><div class="scroll"><table class="matrix"><tr><th>Harness</th><th>Verdict</th>{heads}</tr>{"".join(matrix_rows)}</table></div><div class="legend" style="margin-top:10px">{legend}</div></section>'
            f'{"".join(sections)}<footer class="note">Generated by preserved-thinking-audit from recorded requests and a code reading. Each action item names the check that carries its evidence. The evidence quotes short fragments of what the harness sent: system-prompt text, prompts, tool output, and local paths from the test workspace. Build with --redact before sharing if that matters.</footer></div>')


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("findings", nargs="+")
    ap.add_argument("--static", action="append", default=[], help="<harness>=<static.json>")
    ap.add_argument("--note", action="append", default=[], help="<harness>=<one line: version, commit, how it was run>")
    ap.add_argument("--actions-md", default=None, help="also write the action items as Markdown, one section per harness")
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="Harness Thinking Audit")
    ap.add_argument("--redact", action="store_true",
                    help="drop quoted request content (prompt text, tool output, paths) from the evidence; keep counts, positions, and check results")
    args = ap.parse_args()
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    expand = os.path.expanduser  # `--static name=~/...` reaches us with a literal ~ from most shells
    for path in args.findings:
        with open(expand(path), encoding="utf-8") as fh:
            f = json.load(fh)
        if args.redact:
            f = {**f, "checks": redact(f["checks"]), "exchanges": redact(f.get("exchanges", [])), "run_dir": "[redacted]"}
        groups[f["harness"]].append(f)
    statics: dict[str, dict[str, dict[str, Any]]] = {}
    for spec in args.static:
        name, path = spec.split("=", 1)
        with open(expand(path), encoding="utf-8") as fh:
            statics[name] = {row["check_id"]: row for row in json.load(fh)}
    notes = dict(n.split("=", 1) for n in args.note)
    collected: dict[str, Any] = {}
    out = expand(args.out)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(render(dict(groups), statics, args.title, notes, collected))
    print(out)
    if args.actions_md:
        args.actions_md = expand(args.actions_md)
        with open(args.actions_md, "w", encoding="utf-8") as fh:
            fh.write("\n".join(actions_markdown(h, c["verdict"], notes.get(h, ""), c["actions"]) for h, c in collected.items()))
        print(args.actions_md)


if __name__ == "__main__":
    main()

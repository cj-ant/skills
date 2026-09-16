#!/usr/bin/env python3
"""Grade a static.json from the skill's code-reading pass against a fixture's ground truth.

    grade_static.py <static.json | file containing a JSON array> <fixture>.expected.json

Prints recall on planted habits, precision on clean areas, and exits 1 on any miss. The input may be
a whole agent reply: the first JSON array of rows with a check_id is used.
"""

from __future__ import annotations

import json
import re
import sys

BAD = {"fail", "latent"}


def load_rows(path: str) -> list[dict]:
    """The rows from a static.json file, or from an agent reply that contains the array."""
    text = open(path, encoding="utf-8").read()
    decoder = json.JSONDecoder()
    # Try every "[" as the start of a JSON value. Evidence strings contain brackets, so a regex can't find the end.
    for match in re.finditer(r"\[", text):
        try:
            rows, _ = decoder.raw_decode(text, match.start())
        except ValueError:
            continue
        if isinstance(rows, list) and rows and isinstance(rows[0], dict) and "check_id" in rows[0]:
            return rows
    raise SystemExit(f"no static.json rows found in {path}")


def main() -> int:
    rows = {r.get("check_id"): r for r in load_rows(sys.argv[1])}
    truth = json.load(open(sys.argv[2], encoding="utf-8"))
    missed = [c for c in truth["must_flag"] if (rows.get(c) or {}).get("status") not in BAD]
    false_flags = [c for c in truth["must_not_flag"] if (rows.get(c) or {}).get("status") in BAD]
    unevidenced = [c for c in truth["must_flag"] if c not in missed and not re.search(r"\.py", str(rows[c].get("evidence", "")))]
    found = len(truth["must_flag"]) - len(missed)
    print(f"recall     {found}/{len(truth['must_flag'])} planted habits flagged" + (f"   missed: {', '.join(missed)}" if missed else ""))
    print(f"precision  {len(truth['must_not_flag']) - len(false_flags)}/{len(truth['must_not_flag'])} clean areas left alone" + (f"   false flags: {', '.join(false_flags)}" if false_flags else ""))
    print(f"evidence   {found - len(unevidenced)}/{found} flagged habits cite a source file" + (f"   no file cited: {', '.join(unevidenced)}" if unevidenced else ""))
    for check in missed:
        print(f"  missed {check}: {truth['must_flag'][check]}")
    return 1 if (missed or false_flags) else 0


if __name__ == "__main__":
    sys.exit(main())

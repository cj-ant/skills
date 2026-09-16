#!/usr/bin/env python3
"""Write one regex grader per ground-truth check so the graders can't drift from expected.json.

    python3 evals/preserved-thinking-audit/gen_graders.py        # rewrites <case>/graders/*.md
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROW = '"check_id"\\s*:\\s*"{check}"\\s*,\\s*"status"\\s*:\\s*"(?:{statuses})"'
FLAGGED = "fail|latent"
# A clean check must be present with a non-flagged status. Requiring the row keeps an empty reply from scoring.
CLEAN = "pass|info|not_exercised"


def main() -> None:
    for case in sorted(p.parent for p in HERE.glob("*/expected.json")):
        truth = json.loads((case / "expected.json").read_text())
        graders = case / "graders"
        graders.mkdir(exist_ok=True)
        for old in graders.glob("check-*.md"):
            old.unlink()
        for check, why in truth["must_flag"].items():
            (graders / f"check-{check.lower()}-flagged.md").write_text(
                f"---\ntype: regex\npattern: '{ROW.format(check=check, statuses=FLAGGED)}'\ntarget: last_message\nmatch: contains\nweight: 2\n---\n\n"
                f"Planted habit, must come back fail or latent. {why}\n")
        for check, why in truth["must_not_flag"].items():
            (graders / f"check-{check.lower()}-clean.md").write_text(
                f"---\ntype: regex\npattern: '{ROW.format(check=check, statuses=CLEAN)}'\ntarget: last_message\nmatch: contains\nweight: 1\n---\n\n"
                f"Clean in this harness: the row must be present and must not be fail or latent. {why}\n")
        print(f"{case.name}: {len(truth['must_flag'])} flagged + {len(truth['must_not_flag'])} clean graders")


if __name__ == "__main__":
    main()

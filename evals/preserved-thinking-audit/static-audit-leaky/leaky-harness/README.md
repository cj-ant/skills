# leaky-harness (eval fixture)

A small coding agent written to fail a preserved-thinking audit in known ways. It exists so the
`preserved-thinking-audit` skill can be graded against ground truth. Don't copy its habits.
The planted habits and the clean areas are listed in `../expected.json`.

Run: `ANTHROPIC_BASE_URL=... ANTHROPIC_API_KEY=... python3 -m leaky "fix the failing test"`

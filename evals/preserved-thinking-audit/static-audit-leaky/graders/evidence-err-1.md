---
type: llm
weight: 1
---

The reply contains a JSON array of audit rows for a small Python agent harness. Look only at the row whose `check_id` is `ERR-1`, and only at its `evidence` and `note` fields. Code may be identified by file and line number, by function name, or by a quoted line.

PASS if the row cites the file `leaky/client.py` and says the same request is retried after an HTTP error. FAIL if the row is missing, cites only other files, or describes a different habit.

Ignore the row's `status` value, extra detail, and every other row.

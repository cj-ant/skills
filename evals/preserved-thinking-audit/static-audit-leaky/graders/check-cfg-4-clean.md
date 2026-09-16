---
type: regex
pattern: '"check_id"\s*:\s*"CFG-4"\s*,\s*"status"\s*:\s*"(?:pass|info|not_exercised)"'
target: last_message
match: contains
weight: 1
---

Clean in this harness: the row must be present and must not be fail or latent. messages never end with an assistant turn

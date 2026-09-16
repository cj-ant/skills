---
type: regex
pattern: '"check_id"\s*:\s*"PFX-5"\s*,\s*"status"\s*:\s*"(?:fail|latent)"'
target: last_message
match: contains
weight: 2
---

Planted habit, must come back fail or latent. leaky/history.py compact_if_needed: keep-tail compaction keeps the last 6 messages verbatim, thinking included

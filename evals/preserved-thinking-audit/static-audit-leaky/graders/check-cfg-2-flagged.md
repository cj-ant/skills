---
type: regex
pattern: '"check_id"\s*:\s*"CFG-2"\s*,\s*"status"\s*:\s*"(?:fail|latent)"'
target: last_message
match: contains
weight: 2
---

Planted habit, must come back fail or latent. leaky/client.py: temperature 0.2

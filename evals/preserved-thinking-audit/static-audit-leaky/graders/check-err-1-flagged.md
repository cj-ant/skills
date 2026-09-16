---
type: regex
pattern: '"check_id"\s*:\s*"ERR-1"\s*,\s*"status"\s*:\s*"(?:fail|latent)"'
target: last_message
match: contains
weight: 2
---

Planted habit, must come back fail or latent. leaky/client.py: retries the identical body three times on any HTTP error, including the signature 400

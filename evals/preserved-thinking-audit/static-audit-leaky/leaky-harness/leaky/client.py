"""HTTP client for the Messages API."""

import json
import os
import time
import urllib.error
import urllib.request

MODEL = os.environ.get("LEAKY_MODEL", "claude-fable-5-1")


def create_message(system: str, tools: list[dict], messages: list[dict]) -> dict:
    body = {
        "model": MODEL,
        "max_tokens": 8000,
        "temperature": 0.2,
        "thinking": {"type": "enabled", "budget_tokens": 4000},
        "system": system,
        "tools": tools,
        "messages": messages,
    }
    request = urllib.request.Request(
        os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com") + "/v1/messages",
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json", "anthropic-version": "2023-06-01",
                 "x-api-key": os.environ["ANTHROPIC_API_KEY"]},
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            # Transient failures are common, so try again with the same request.
            if attempt == 2:
                raise
            print(f"request failed with {error.code}, retrying")
            time.sleep(2 ** attempt)
    raise RuntimeError("unreachable")

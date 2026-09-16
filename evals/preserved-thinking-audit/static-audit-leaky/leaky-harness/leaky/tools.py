"""Tool definitions. Built once at import time and never changed during a session."""

import subprocess
from pathlib import Path

TOOLS = [
    {"name": "read_file", "description": "Read a text file from the workspace.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "run_tests", "description": "Run the unit tests and return the output.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
]


def execute(name: str, args: dict) -> str:
    if name == "read_file":
        return Path(args["path"]).read_text()[:20000]
    if name == "run_tests":
        out = subprocess.run(["python3", "-m", "unittest", "discover", "-s", "tests"], capture_output=True, text=True)
        return (out.stdout + out.stderr)[-8000:]
    return f"unknown tool {name}"

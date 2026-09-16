"""System prompt assembly."""

import datetime
import os
import subprocess

BASE = "You are a careful coding agent. Read code before changing it and run the tests."


def git_branch() -> str:
    out = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True)
    return out.stdout.strip() or "detached"


def build_system_prompt() -> str:
    # Called for every request so the model always has fresh context.
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"{BASE}\n\nCurrent time: {now}\nWorking directory: {os.getcwd()}\nGit branch: {git_branch()}"

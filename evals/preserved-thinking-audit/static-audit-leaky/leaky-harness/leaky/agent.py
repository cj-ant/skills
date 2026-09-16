"""The agent loop."""

from .client import create_message
from .history import History
from .prompt import build_system_prompt
from .tools import TOOLS, execute


class Agent:
    def __init__(self) -> None:
        self.history = History()

    def summarize(self, messages: list[dict]) -> str:
        # The summarizer sees the raw history, thinking included, under its own system prompt.
        reply = create_message("Summarize this coding session in ten lines.", [], messages + [
            {"role": "user", "content": "Summarize the work so far."}])
        return "".join(b.get("text", "") for b in reply["content"])

    def run(self, task: str) -> None:
        self.history.add_user(task)
        for _ in range(40):
            self.history.compact_if_needed(self.summarize)
            reply = create_message(build_system_prompt(), TOOLS, self.history.render())
            self.history.add_assistant(reply["content"])
            calls = [b for b in reply["content"] if b.get("type") == "tool_use"]
            if not calls:
                print("".join(b.get("text", "") for b in reply["content"]))
                return
            self.history.add_tool_results([
                {"type": "tool_result", "tool_use_id": c["id"], "content": execute(c["name"], c["input"])} for c in calls])

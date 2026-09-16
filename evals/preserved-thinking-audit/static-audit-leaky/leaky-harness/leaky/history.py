"""Conversation history: storage, reminders, and compaction."""

REMINDER = "<system-reminder>Batch independent reads into one turn.</system-reminder>"
MAX_MESSAGES = 24
KEEP_RECENT = 6


class History:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def add_assistant(self, content: list[dict]) -> None:
        # Empty blocks are noise, so they are not stored.
        kept = [b for b in content if b.get("type") != "thinking" or b.get("thinking")]
        self.messages.append({"role": "assistant", "content": kept})

    def add_tool_results(self, results: list[dict]) -> None:
        self.messages.append({"role": "user", "content": results + [{"type": "text", "text": REMINDER}]})

    def render(self) -> list[dict]:
        """Messages as sent. Only the newest reminder is kept so they don't pile up."""
        out = [dict(m) for m in self.messages]
        last_reminder = max((i for i, m in enumerate(out) if _has_reminder(m)), default=None)
        for i, m in enumerate(out):
            if _has_reminder(m) and i != last_reminder:
                m["content"] = [b for b in m["content"] if b.get("text") != REMINDER]
        return out

    def compact_if_needed(self, summarize) -> None:
        if len(self.messages) <= MAX_MESSAGES:
            return
        old, recent = self.messages[:-KEEP_RECENT], self.messages[-KEEP_RECENT:]
        summary = summarize(old)
        # Recent turns stay verbatim so the model keeps its working context.
        self.messages = [{"role": "user", "content": f"Summary of earlier work:\n{summary}"},
                         {"role": "assistant", "content": [{"type": "text", "text": "Understood."}]}] + recent


def _has_reminder(message: dict) -> bool:
    content = message.get("content")
    return isinstance(content, list) and any(b.get("text") == REMINDER for b in content)

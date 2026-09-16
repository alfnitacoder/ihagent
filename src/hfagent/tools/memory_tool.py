"""Long-term memory tool: the agent saves durable facts for future sessions."""

from __future__ import annotations

from ..memory import save_fact
from .base import Tool


class Remember(Tool):
    name = "remember"
    description = (
        "Save a durable fact to long-term memory for future sessions: hosts "
        "and their roles, user preferences, project context, infrastructure "
        "quirks. One concise fact per call. Do not save secrets or passwords."
    )
    parameters = {
        "type": "object",
        "properties": {
            "fact": {
                "type": "string",
                "description": "One self-contained fact, e.g. 'kali219 is the user's Kali Linux pentest box'.",
            }
        },
        "required": ["fact"],
    }

    def run(self, fact: str) -> str:
        return save_fact(fact)

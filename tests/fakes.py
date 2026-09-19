"""Shared test doubles — import from here instead of ``conftest``.

Nested ``tests/video/conftest.py`` otherwise shadows ``from conftest import …``.
"""

from __future__ import annotations

import json

from earner.llm import Completion


class FakeLLM:
    """Scripted Claude. Lets the pipeline be tested without network or spend."""

    def __init__(self, responses: list[str] | None = None, cost_cents: int = 3):
        self.responses = list(responses or [])
        self.cost_cents = cost_cents
        self.calls: list[dict] = []

    def complete(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        text = self.responses.pop(0) if self.responses else "generated deliverable body"
        return Completion(
            text=text,
            input_tokens=1000,
            output_tokens=500,
            cost_cents=self.cost_cents,
            model="fake",
        )


def write_request(cfg, ref: str, **payload) -> None:
    folder = cfg.inbox / "requests"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{ref}.json").write_text(json.dumps(payload))

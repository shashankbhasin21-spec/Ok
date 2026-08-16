from __future__ import annotations

import json

import pytest

from earner import config
from earner.ledger import Ledger
from earner.llm import Completion
from earner.payments import SandboxProvider


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


@pytest.fixture
def cfg(tmp_path):
    c = config.Config(workdir=tmp_path / "wd", min_margin_cents=100, max_llm_cost_cents_per_job=200)
    c.ensure_dirs()
    return c


@pytest.fixture
def ledger(cfg):
    led = Ledger(cfg.db_path)
    yield led
    led.close()


@pytest.fixture
def provider(cfg):
    return SandboxProvider(cfg.workdir / "sbx.json")


def write_request(cfg, ref: str, **payload) -> None:
    folder = cfg.inbox / "requests"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{ref}.json").write_text(json.dumps(payload))

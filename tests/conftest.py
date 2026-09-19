from __future__ import annotations

import pytest

from earner import config
from earner.ledger import Ledger
from earner.payments import SandboxProvider

# Re-export for any remaining ``from conftest import FakeLLM`` call sites.
from tests.fakes import FakeLLM, write_request  # noqa: F401


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

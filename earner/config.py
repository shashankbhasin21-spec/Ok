"""Runtime configuration.

Everything defaults to sandbox. Touching real money requires an explicit
opt-in (``EARNER_MODE=live`` plus a real provider key), so an accidental run
can never charge a real card or send a real invoice.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

SANDBOX = "sandbox"
LIVE = "live"


@dataclass(frozen=True)
class Config:
    mode: str = SANDBOX
    workdir: Path = field(default_factory=lambda: Path("./.earner"))
    model: str = "claude-opus-5"
    effort: str = "high"
    max_tokens: int = 16000
    currency: str = "usd"

    # What the firm sells. Shapes pricing, pitches and social content.
    offer: str = (
        "AI agents that do a specific business job end to end — inbound handling, "
        "research, content, invoicing and follow-up — built, deployed and handed over"
    )

    # Cost guardrails. A job that would spend more on tokens than it can bill
    # is not a business; these caps make that failure loud instead of silent.
    max_llm_cost_cents_per_job: int = 200
    min_margin_cents: int = 100

    # Autonomous mode lets agents source their own work (niche scouting) and
    # run on a loop. It does not bypass approval gates.
    autonomous: bool = False

    anthropic_api_key: str | None = None
    stripe_api_key: str | None = None

    # Channels. Empty means that channel is simply off.
    gmail_user: str | None = None
    gmail_app_password: str | None = None
    instagram_user_id: str | None = None
    instagram_access_token: str | None = None

    @property
    def is_live(self) -> bool:
        return self.mode == LIVE

    @property
    def db_path(self) -> Path:
        return self.workdir / "ledger.db"

    @property
    def inbox(self) -> Path:
        return self.workdir / "inbox"

    @property
    def outbox(self) -> Path:
        return self.workdir / "outbox"

    @property
    def deliverables(self) -> Path:
        return self.workdir / "deliverables"

    def ensure_dirs(self) -> None:
        for p in (self.workdir, self.inbox, self.outbox, self.deliverables):
            p.mkdir(parents=True, exist_ok=True)


def load(**overrides) -> Config:
    """Build a Config from the environment, then apply explicit overrides."""
    env = os.environ
    cfg = Config(
        mode=env.get("EARNER_MODE", SANDBOX).strip().lower(),
        workdir=Path(env.get("EARNER_WORKDIR", "./.earner")),
        model=env.get("EARNER_MODEL", "claude-opus-5"),
        effort=env.get("EARNER_EFFORT", "high"),
        currency=env.get("EARNER_CURRENCY", "usd").lower(),
        offer=env.get("EARNER_OFFER", Config.offer),
        max_llm_cost_cents_per_job=int(env.get("EARNER_MAX_LLM_COST_CENTS", "200")),
        min_margin_cents=int(env.get("EARNER_MIN_MARGIN_CENTS", "100")),
        autonomous=env.get("EARNER_AUTONOMOUS", "0") == "1",
        anthropic_api_key=env.get("ANTHROPIC_API_KEY"),
        stripe_api_key=env.get("STRIPE_API_KEY"),
        gmail_user=env.get("GMAIL_USER"),
        gmail_app_password=env.get("GMAIL_APP_PASSWORD"),
        instagram_user_id=env.get("INSTAGRAM_USER_ID"),
        instagram_access_token=env.get("INSTAGRAM_ACCESS_TOKEN"),
    )
    if overrides:
        cfg = Config(**{**cfg.__dict__, **overrides})
    if cfg.mode not in (SANDBOX, LIVE):
        raise ValueError(f"EARNER_MODE must be '{SANDBOX}' or '{LIVE}', got {cfg.mode!r}")
    if cfg.is_live and not cfg.stripe_api_key:
        raise ValueError("EARNER_MODE=live requires STRIPE_API_KEY (real invoices, real money)")
    return cfg

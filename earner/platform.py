"""The running platform: hires the staff, runs the loop, keeps the books."""

from __future__ import annotations

import time

from . import approval, config, goals
from .agents.acquisition import AcquisitionAgent
from .agents.bidding import BiddingAgent
from .agents.collections import CollectionsAgent
from .agents.growth import GrowthAgent
from .agents.quality import QualityAgent
from .agents.service_desk import ServiceDeskAgent
from .agents.studio import ContentStudioAgent
from .ceo import CEO
from .channels.gmail import GmailChannel
from .channels.instagram import InstagramChannel
from .ledger import Ledger
from .payments import build_provider

# Order matters: sell, deliver, build, market, collect, then inspect.
STAFF = [
    AcquisitionAgent,
    BiddingAgent,
    ServiceDeskAgent,
    ContentStudioAgent,
    GrowthAgent,
    CollectionsAgent,
    QualityAgent,
]


class Platform:
    def __init__(self, cfg=None, gate_name: str = "hold"):
        self.cfg = cfg or config.load()
        self.cfg.ensure_dirs()
        self.ledger = Ledger(self.cfg.db_path)
        self.provider = build_provider(self.cfg)
        self.gate = approval.build_gate(gate_name, is_live=self.cfg.is_live)
        self.gmail = GmailChannel(self.cfg)
        self.instagram = InstagramChannel(self.cfg)
        self.staff = {
            cls.name: cls(self.cfg, self.ledger, self.provider, self.gate)
            for cls in STAFF
        }
        self.ceo = CEO(self.cfg, self.ledger, self.staff)

    def close(self) -> None:
        self.ledger.close()

    # ------------------------------------------------------------------- runs

    def tick(self) -> list:
        """One pass of the whole company."""
        reports = []

        if self.gmail.enabled:
            try:
                pulled = self.gmail.ingest_requests()
                if pulled:
                    self.ledger.log("channel", None, "gmail_ingest", count=pulled)
            except Exception as exc:  # noqa: BLE001 - a mail blip must not stop the run
                self.ledger.log("channel", None, "gmail_error", error=str(exc))

        for agent in self.staff.values():
            try:
                reports.append(agent.tick())
            except Exception as exc:  # noqa: BLE001 - one agent must not take down the firm
                from .agent import TickReport

                reports.append(
                    TickReport(agent=agent.name, errors=[f"{type(exc).__name__}: {exc}"])
                )

        if self.gmail.enabled:
            try:
                sent = self.gmail.flush_outbox()
                if sent:
                    self.ledger.log("channel", None, "gmail_sent", count=sent)
            except Exception as exc:  # noqa: BLE001
                self.ledger.log("channel", None, "gmail_send_error", error=str(exc))

        return reports

    def run(self, *, loops: int = 1, interval: float = 900.0, on_report=None) -> list:
        """Run the company. ``loops <= 0`` means run until interrupted."""
        all_reports = []
        count = 0
        while loops <= 0 or count < loops:
            reports = self.tick()
            all_reports.extend(reports)
            if on_report:
                on_report(reports)
            count += 1
            if loops <= 0 or count < loops:
                target = goals.load(self.cfg.workdir / "target.json")
                if target and target.expired:
                    break
                time.sleep(interval)
        return all_reports

    def autopilot(self, *, interval: float = 900.0, max_cycles: int = 0, on_cycle=None) -> dict:
        """Run unattended until the target is hit, the deadline passes, or the
        spend cap trips. Returns why it stopped.

        This is the whole company on a loop: pull mail, sell, deliver, market,
        collect, review, report. It stops itself rather than running forever —
        an autonomous system with no stop condition is how money leaks.
        """
        cycles = 0
        while True:
            reports = self.tick()
            cycles += 1
            status = self.target_status()
            if on_cycle:
                on_cycle(cycles, reports, status)

            if status and status.settled_cents >= status.target_cents:
                return {"stopped": "target_hit", "cycles": cycles,
                        "settled_cents": status.settled_cents}
            if status and status.hours_left <= 0:
                return {"stopped": "deadline", "cycles": cycles,
                        "settled_cents": status.settled_cents}
            if max_cycles and cycles >= max_cycles:
                return {"stopped": "max_cycles", "cycles": cycles,
                        "settled_cents": self.ledger.revenue_cents()}

            spend = self.ledger.cost_cents()
            revenue = self.ledger.revenue_cents()
            if spend > 2_000 and spend > revenue * 3:
                # Burning far more on tokens than the work brings in.
                return {"stopped": "spend_guard", "cycles": cycles,
                        "spend_cents": spend, "settled_cents": revenue}

            time.sleep(interval)

    # ---------------------------------------------------------------- targets

    def set_target(self, amount_cents: int, hours: float) -> goals.Target:
        target = goals.Target(amount_cents=amount_cents, hours=hours, started_at=time.time())
        goals.save(self.cfg.workdir / "target.json", target)
        self.ledger.log("target", None, "set", amount_cents=amount_cents, hours=hours)
        return target

    def target_status(self):
        target = goals.load(self.cfg.workdir / "target.json")
        return goals.evaluate(target, self.ledger) if target else None

    # ------------------------------------------------------------ diagnostics

    def readiness(self) -> list[tuple[str, bool, str]]:
        """What is actually wired up. Money needs every row to be true."""
        cfg = self.cfg
        return [
            ("Claude API key", bool(cfg.anthropic_api_key), "ANTHROPIC_API_KEY — agents can think"),
            (
                "Payments (live)",
                bool(cfg.is_live and cfg.stripe_api_key),
                "STRIPE_API_KEY + EARNER_MODE=live — required for real invoices",
            ),
            (
                "Gmail",
                self.gmail.enabled,
                "GMAIL_USER + GMAIL_APP_PASSWORD — inbound briefs and outbound replies",
            ),
            (
                "Instagram",
                self.instagram.enabled,
                "INSTAGRAM_USER_ID + INSTAGRAM_ACCESS_TOKEN — publishing, replies, ads",
            ),
            (
                "Demand",
                any(self.cfg.inbox.glob("*/*.json")),
                "leads or requests in the inbox — no demand, no revenue",
            ),
        ]

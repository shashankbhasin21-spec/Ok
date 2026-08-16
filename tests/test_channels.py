"""Gmail filtering, using the kinds of mail a real inbox actually contains."""

from __future__ import annotations

import os
import stat

from earner.channels.gmail import GmailChannel, Mail
from earner.connect import write_env

# Shapes taken from a real personal inbox — this is what ingestion sees if it
# is not label-scoped, and none of it is a client brief.
NOISE = [
    Mail("<1>", "alerts@axis.bank.in", "INR 470.00 was debited from your A/c no. XX6214.",
         "Dear Shashank Bhasin, Here is the summary of your transaction: Amount Debited INR 470"),
    Mail("<2>", "alerts@hdfcbank.bank.in", "OTP to verify your Update KYC",
         "Dear SHASHANK BHASIN, 207475 is your OTP to verify your mobile number for Update KYC."),
    Mail("<3>", "noreply@github.com", "[GitHub] Sudo email verification code",
         "Please verify your identity. Here is your GitHub sudo authentication code: 05525054."),
    Mail("<4>", "no-reply@spotify.com", "Premium just got better",
         "Spotify Premium Email EXPLORE NOW Get Spotify for iPhone iPad Android Other message"),
    Mail("<5>", "someone@example.com", "quick q", "can you help"),  # too short to scope
]

REAL_REQUEST = Mail(
    "<6>",
    "ops@acmeagency.com",
    "Competitor brief for our Q3 board pack",
    "Hi — we need a written comparison of the three biggest CRM vendors for a 40-person "
    "agency, covering pricing, migration effort and support quality. Board meets on the "
    "12th so we need it by the 9th. Budget is around $600.",
)


def test_automated_mail_is_never_a_brief(cfg):
    channel = GmailChannel(cfg)
    for mail in NOISE:
        assert channel.is_noise(mail), f"{mail.subject!r} must not become a billable job"


def test_a_genuine_request_passes(cfg):
    assert GmailChannel(cfg).is_noise(REAL_REQUEST) is False


def test_ingestion_is_off_until_credentials_exist(cfg):
    """No credentials means no reads — never a crash, never a partial state."""
    channel = GmailChannel(cfg)
    assert channel.enabled is False
    assert channel.fetch() == []
    assert channel.ingest_requests() == 0
    assert channel.flush_outbox() == 0


def test_env_is_merged_and_owner_only(tmp_path):
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=sk-existing\nEARNER_MODE=sandbox\n")

    write_env({"GMAIL_USER": "a@b.com", "GMAIL_APP_PASSWORD": "secret"}, env)

    text = env.read_text()
    assert "ANTHROPIC_API_KEY=sk-existing" in text, "existing keys must survive"
    assert "GMAIL_USER=a@b.com" in text
    mode = stat.S_IMODE(os.stat(env).st_mode)
    assert mode == 0o600, "a file holding credentials must not be world-readable"

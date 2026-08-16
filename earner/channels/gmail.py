"""Gmail channel — real inbound requests in, real replies out.

Uses IMAP + SMTP with a Google App Password, which needs no OAuth app review
and works on a normal account with 2FA enabled. Inbound mail becomes a JSON
brief in ``inbox/requests``; approved outbox messages get sent for real.

Setup: enable 2-Step Verification, create an App Password at
https://myaccount.google.com/apppasswords, then set GMAIL_USER and
GMAIL_APP_PASSWORD.
"""

from __future__ import annotations

import email
import hashlib
import imaplib
import json
import re
import smtplib
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import parseaddr

IMAP_HOST = "imap.gmail.com"
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465


@dataclass
class Mail:
    message_id: str
    sender: str
    subject: str
    body: str


class GmailChannel:
    def __init__(self, cfg):
        self.cfg = cfg
        self.user = cfg.gmail_user
        self.password = cfg.gmail_app_password

    @property
    def enabled(self) -> bool:
        return bool(self.user and self.password)

    # ------------------------------------------------------------------ inbound

    def fetch(self, *, label: str = "INBOX", limit: int = 25, unread_only: bool = True) -> list[Mail]:
        if not self.enabled:
            return []
        conn = imaplib.IMAP4_SSL(IMAP_HOST)
        try:
            conn.login(self.user, self.password)
            conn.select(label)
            status, data = conn.search(None, "UNSEEN" if unread_only else "ALL")
            if status != "OK":
                return []
            ids = data[0].split()[-limit:]
            out = []
            for msg_id in ids:
                status, raw = conn.fetch(msg_id, "(RFC822)")
                if status != "OK" or not raw or not raw[0]:
                    continue
                out.append(self._parse(email.message_from_bytes(raw[0][1])))
            return out
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001 - logout failure must not lose fetched mail
                pass

    def _parse(self, msg) -> Mail:
        subject = str(make_header(decode_header(msg.get("Subject", "(no subject)"))))
        sender = parseaddr(msg.get("From", ""))[1]
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    body = part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", "replace"
                    )
                    break
        else:
            body = msg.get_payload(decode=True).decode(
                msg.get_content_charset() or "utf-8", "replace"
            )
        return Mail(
            message_id=msg.get("Message-ID", "") or hashlib.sha256(body.encode()).hexdigest()[:24],
            sender=sender,
            subject=subject,
            body=_strip_quoted(body).strip(),
        )

    def ingest_requests(self) -> int:
        """Turn unread mail into briefs the delivery agent will pick up.

        Deduplicated on the RFC Message-ID, so re-running never re-bills a
        client for an email you already turned into a job.
        """
        if not self.enabled:
            return 0
        self.cfg.ensure_dirs()
        folder = self.cfg.inbox / "requests"
        folder.mkdir(parents=True, exist_ok=True)
        written = 0
        for mail in self.fetch():
            if not mail.sender or not mail.body:
                continue
            ref = "gmail-" + hashlib.sha256(mail.message_id.encode()).hexdigest()[:16]
            path = folder / f"{ref}.json"
            if path.exists():
                continue
            path.write_text(
                json.dumps(
                    {
                        "email": mail.sender,
                        "title": mail.subject,
                        "brief": mail.body,
                        "source": "gmail",
                        "message_id": mail.message_id,
                    },
                    indent=2,
                )
            )
            written += 1
        return written

    # ----------------------------------------------------------------- outbound

    def send(self, to: str, subject: str, body: str, *, attachment_path: str | None = None) -> None:
        if not self.enabled:
            raise RuntimeError("Gmail channel is not configured (GMAIL_USER / GMAIL_APP_PASSWORD)")
        msg = EmailMessage()
        msg["From"] = self.user
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        if attachment_path:
            from pathlib import Path

            path = Path(attachment_path)
            msg.add_attachment(
                path.read_bytes(),
                maintype="text",
                subtype="markdown",
                filename=path.name,
            )
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as smtp:
            smtp.login(self.user, self.password)
            smtp.send_message(msg)

    def flush_outbox(self) -> int:
        """Send every approved message sitting in the outbox, then archive it.

        Outbox files are written only after an approval gate said yes, so this
        never sends something a human hasn't seen.
        """
        if not self.enabled:
            return 0
        self.cfg.ensure_dirs()
        sent_dir = self.cfg.outbox / "sent"
        sent_dir.mkdir(parents=True, exist_ok=True)
        count = 0
        for path in sorted(self.cfg.outbox.glob("*.txt")):
            text = path.read_text()
            to_match = re.search(r"^To:\s*(.+)$", text, re.MULTILINE)
            if not to_match or "@" not in to_match.group(1):
                continue
            subject_match = re.search(r"^Subject:\s*(.+)$", text, re.MULTILINE)
            body = re.sub(r"^(To|Subject):.*$", "", text, flags=re.MULTILINE).strip()
            self.send(
                to_match.group(1).strip(),
                subject_match.group(1).strip() if subject_match else "Following up",
                body,
            )
            path.rename(sent_dir / path.name)
            count += 1
        return count


def _strip_quoted(body: str) -> str:
    """Drop quoted reply history so a thread doesn't re-brief the whole chain."""
    cut = re.split(r"\n-{2,}\s*Original Message|\nOn .* wrote:|\n>{1,}", body, maxsplit=1)
    return cut[0]

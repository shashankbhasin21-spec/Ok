"""Credential setup with live validation.

Nothing here trusts a pasted string. Gmail credentials are proved by an actual
IMAP login and SMTP handshake; the Instagram token is proved by a real Graph
API call that reads back the account it belongs to. A credential that "looks
right" but fails at 3am in an autopilot loop is worse than one that never got
saved, so this fails loudly at setup instead.

Secrets are prompted locally, never echoed, and written to .env with 0600.
"""

from __future__ import annotations

import getpass
import imaplib
import json
import os
import smtplib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .channels.gmail import IMAP_HOST, REQUEST_LABEL, SMTP_HOST, SMTP_PORT
from .channels.instagram import GRAPH
from .channels.upwork import UpworkChannel, UpworkAuthError


@dataclass
class CheckResult:
    ok: bool
    detail: str
    values: dict[str, str] | None = None


def verify_gmail(user: str, app_password: str) -> CheckResult:
    """Prove the credential works for both reading and sending."""
    password = app_password.replace(" ", "")  # Google displays it in groups of four
    try:
        conn = imaplib.IMAP4_SSL(IMAP_HOST)
        conn.login(user, password)
        status, raw = conn.list()
        labels = []
        if status == "OK":
            labels = [line.decode(errors="replace").split(' "/" ')[-1].strip('"') for line in raw]
        conn.logout()
    except imaplib.IMAP4.error as exc:
        return CheckResult(
            False,
            f"IMAP login refused ({exc}). Use a 16-character App Password, not your "
            "Google account password, and make sure IMAP is enabled in Gmail settings.",
        )
    except OSError as exc:
        return CheckResult(False, f"Could not reach {IMAP_HOST}: {exc}")

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.login(user, password)
    except Exception as exc:  # noqa: BLE001 - any SMTP failure means we cannot send
        return CheckResult(False, f"IMAP worked but SMTP login failed ({exc}) — replies cannot send.")

    has_label = any(label == REQUEST_LABEL for label in labels)
    detail = f"Read and send both verified for {user}."
    if not has_label:
        detail += (
            f" The '{REQUEST_LABEL}' label does not exist yet — create it and add a Gmail "
            "filter routing client mail to it. Until then nothing will be ingested, which is "
            "the safe default."
        )
    return CheckResult(
        True,
        detail,
        {"GMAIL_USER": user, "GMAIL_APP_PASSWORD": password},
    )


def verify_instagram(token: str, user_id: str | None = None) -> CheckResult:
    """Read the account back from the Graph API so we know the token is real."""
    try:
        if not user_id:
            # Discover the IG account from the Pages the token can see.
            pages = _graph("me/accounts", {"fields": "name,instagram_business_account"}, token)
            for page in pages.get("data", []):
                account = page.get("instagram_business_account")
                if account:
                    user_id = account["id"]
                    break
            if not user_id:
                return CheckResult(
                    False,
                    "The token is valid but no Instagram Business account is linked to any Page "
                    "it can see. In the Instagram app: Settings → Account type → switch to "
                    "Business or Creator, then link it to a Facebook Page.",
                )

        me = _graph(user_id, {"fields": "username,name,followers_count"}, token)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        try:
            message = json.loads(body)["error"]["message"]
        except Exception:  # noqa: BLE001
            message = body[:300]
        return CheckResult(False, f"Graph API rejected the token: {message}")
    except OSError as exc:
        return CheckResult(False, f"Could not reach the Graph API: {exc}")

    followers = me.get("followers_count")
    detail = f"Connected to @{me.get('username')}"
    if followers is not None:
        detail += f" ({followers:,} followers)"
    return CheckResult(
        True,
        detail + ".",
        {"INSTAGRAM_USER_ID": str(user_id), "INSTAGRAM_ACCESS_TOKEN": token},
    )


def _graph(path: str, params: dict, token: str) -> dict:
    query = urllib.parse.urlencode({**params, "access_token": token})
    with urllib.request.urlopen(f"{GRAPH}/{path}?{query}", timeout=30) as resp:
        return json.loads(resp.read())


def connect_upwork(cfg) -> CheckResult:
    """Run the OAuth2 authorization-code flow, then prove the grant with a real query."""
    channel = UpworkChannel(cfg)
    if not channel.configured:
        return CheckResult(
            False,
            "Set UPWORK_CLIENT_ID and UPWORK_CLIENT_SECRET first. Create the key at "
            "https://www.upwork.com/developer/keys/apply — request 'Common Entities - "
            "Read-Only Access' plus job-search scope, and set the redirect URI to match.",
        )

    print("\n  1. Open this URL and approve access:\n")
    print(f"     {channel.authorize_url()}\n")
    print("  2. Upwork redirects to your callback with ?code=… in the address bar.")
    code = input("  3. Paste the code here: ").strip()
    if not code:
        return CheckResult(False, "No code entered")

    try:
        channel.exchange_code(code)
    except UpworkAuthError as exc:
        return CheckResult(False, str(exc))

    # A stored token proves nothing until it answers a real query.
    try:
        jobs = channel.search_jobs("ai automation", limit=3)
    except UpworkAuthError as exc:
        return CheckResult(
            False,
            f"Token stored but the API rejected the query: {exc}\n"
            "      Usually a missing scope on the key, or the search query name differs for "
            "your key — check the schema explorer in Upwork's API Center.",
        )

    detail = f"Authorised. Job search returned {len(jobs)} posting(s)"
    if jobs:
        detail += f", e.g. \"{jobs[0].title[:60]}\" ({jobs[0].bid_count} applicants)"
    return CheckResult(True, detail + ".")


def write_env(values: dict[str, str], path: Path) -> None:
    """Merge into .env, keeping existing keys, readable only by this user."""
    existing: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                existing[key.strip()] = value
    existing.update(values)

    path.write_text("\n".join(f"{k}={v}" for k, v in existing.items()) + "\n")
    os.chmod(path, 0o600)


def run_wizard(env_path: Path, *, only: str | None = None, cfg=None) -> int:
    """Interactive setup. Returns the number of channels connected."""
    print("Credentials are typed here, never echoed, and saved to")
    print(f"{env_path} with owner-only permissions.\n")
    connected = 0

    if only in (None, "gmail"):
        print("── Gmail " + "─" * 52)
        print("Needs 2-Step Verification on, then an App Password from")
        print("https://myaccount.google.com/apppasswords\n")
        user = input("  Gmail address (blank to skip): ").strip()
        if user:
            password = getpass.getpass("  App password (hidden): ").strip()
            print("  checking…", flush=True)
            result = verify_gmail(user, password)
            print(f"  {'OK' if result.ok else 'FAILED'} — {result.detail}\n")
            if result.ok:
                write_env(result.values, env_path)
                connected += 1

    if only in (None, "instagram"):
        print("── Instagram " + "─" * 48)
        print("Needs a Business/Creator account linked to a Facebook Page, and a")
        print("token from a Meta app with instagram_content_publish + pages_show_list.")
        print("Get one at https://developers.facebook.com/tools/explorer\n")
        token = getpass.getpass("  Access token (hidden, blank to skip): ").strip()
        if token:
            user_id = input("  IG user id (blank to auto-detect): ").strip() or None
            print("  checking…", flush=True)
            result = verify_instagram(token, user_id)
            print(f"  {'OK' if result.ok else 'FAILED'} — {result.detail}\n")
            if result.ok:
                write_env(result.values, env_path)
                connected += 1

    if only == "upwork" or (only is None and cfg is not None):
        print("── Upwork " + "─" * 51)
        print("Official GraphQL API: authorised job discovery, no scraping.")
        print("It cannot submit proposals — Upwork exposes no such mutation.\n")
        result = connect_upwork(cfg)
        print(f"  {'OK' if result.ok else 'FAILED'} — {result.detail}\n")
        if result.ok:
            connected += 1

    return connected

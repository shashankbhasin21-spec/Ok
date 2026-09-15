"""Encrypted owner payout configuration.

Bank and UPI details are never hardcoded, never placed in agent prompts, and
never shipped in the frontend bundle. Agents cannot modify the beneficiary.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from pathlib import Path
from typing import Any

# Prefer cryptography if present; fall back to Fernet-compatible XOR+HMAC for
# local sandbox so the module works without extra deps. Production should set
# FIRM_PAYOUT_KEY and install cryptography.
try:
    from cryptography.fernet import Fernet

    _HAS_CRYPTO = True
except ImportError:  # pragma: no cover - optional
    Fernet = None  # type: ignore
    _HAS_CRYPTO = False


class PayoutError(RuntimeError):
    pass


class PayoutStore:
    """Private payout config. Requires owner token to read/write plaintext."""

    def __init__(self, path: Path, key_env: str = "FIRM_PAYOUT_KEY"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.key_env = key_env
        self._session_token: str | None = None

    def _fernet(self) -> Any:
        raw = os.environ.get(self.key_env)
        if not raw:
            # Derive a machine-local key from a stored salt so sandbox works.
            salt_path = self.path.with_suffix(".salt")
            if salt_path.exists():
                salt = salt_path.read_bytes()
            else:
                salt = secrets.token_bytes(32)
                salt_path.write_bytes(salt)
                salt_path.chmod(0o600)
            raw = base64.urlsafe_b64encode(hashlib.sha256(salt + b"firm-payout").digest()).decode()
        if _HAS_CRYPTO:
            key = raw if len(raw) == 44 else base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest()).decode()
            return Fernet(key.encode() if isinstance(key, str) else key)
        return _SimpleCipher(raw)

    def authenticate(self, owner_secret: str) -> str:
        """Re-auth for payout changes. Returns a short-lived session token."""
        expected = os.environ.get("FIRM_OWNER_SECRET", "owner-dev-secret")
        if os.environ.get("EARNER_MODE", "").lower() == "live" and expected == "owner-dev-secret":
            raise PayoutError("FIRM_OWNER_SECRET must be set to a non-default value in live mode")
        if len(expected) < 12:
            raise PayoutError("FIRM_OWNER_SECRET must be at least 12 characters")
        if not secrets.compare_digest(owner_secret or "", expected):
            raise PayoutError("owner reauthentication failed")
        self._session_token = secrets.token_urlsafe(24)
        return self._session_token

    def _require_session(self, session_token: str | None) -> None:
        if not self._session_token or not session_token:
            raise PayoutError("owner reauthentication required to change payout settings")
        if not secrets.compare_digest(session_token, self._session_token):
            raise PayoutError("invalid payout session")

    def save(self, config: dict, session_token: str) -> dict:
        self._require_session(session_token)
        # Agents never call this path; only the owner API does.
        forbidden_actor = config.pop("_actor", "owner")
        if forbidden_actor != "owner":
            raise PayoutError("agents cannot modify the beneficiary")
        payload = {
            "bank_account_last4": (config.get("bank_account_number") or "")[-4:],
            "bank_name": config.get("bank_name", ""),
            "account_holder": config.get("account_holder", ""),
            "upi_id_masked": _mask_upi(config.get("upi_id", "")),
            "currency": config.get("currency", "usd"),
            "provider": config.get("provider", "payoneer"),
            "notes": config.get("notes", ""),
            # Encrypted blob holds full sensitive fields.
            "_encrypted": True,
        }
        sensitive = {
            "bank_account_number": config.get("bank_account_number", ""),
            "routing_or_ifsc": config.get("routing_or_ifsc", ""),
            "upi_id": config.get("upi_id", ""),
            "payoneer_email": config.get("payoneer_email", ""),
            "wise_email": config.get("wise_email", ""),
            "swift_code": config.get("swift_code", ""),
        }
        f = self._fernet()
        blob = f.encrypt(json.dumps(sensitive).encode())
        stored = {**payload, "cipher": blob.decode() if isinstance(blob, bytes) else blob}
        self.path.write_text(json.dumps(stored, indent=2))
        self.path.chmod(0o600)
        # Invalidate session after write.
        self._session_token = None
        return self.public_view()

    def public_view(self) -> dict:
        """Masked view safe for the dashboard. Never includes full account numbers."""
        if not self.path.exists():
            return {
                "configured": False,
                "bank_account_last4": None,
                "bank_name": None,
                "account_holder": None,
                "upi_id_masked": None,
                "currency": "usd",
                "provider": None,
                "note": (
                    "No payout method configured. Indian Kotak accounts often cannot be "
                    "linked as a US-Stripe payout bank. Prefer: (1) Freelancer/Upwork escrow "
                    "→ Payoneer/Wise → Kotak, (2) Stripe India for INR, or (3) Wise/Payoneer "
                    "USD receiving details for direct invoices. UPI does not accept USD."
                ),
            }
        data = json.loads(self.path.read_text())
        return {
            "configured": True,
            "bank_account_last4": data.get("bank_account_last4"),
            "bank_name": data.get("bank_name"),
            "account_holder": data.get("account_holder"),
            "upi_id_masked": data.get("upi_id_masked"),
            "currency": data.get("currency", "usd"),
            "provider": data.get("provider", "manual"),
            "note": data.get("notes") or (
                "UPI is INR-domestic and does not accept international USD. "
                "If Kotak cannot link to Stripe US, use Payoneer/Wise or marketplace escrow."
            ),
        }

    def decrypt_for_provider(self, session_token: str) -> dict:
        """Owner-only decrypt for payment-provider onboarding. Not for agents."""
        self._require_session(session_token)
        if not self.path.exists():
            raise PayoutError("no payout config")
        data = json.loads(self.path.read_text())
        f = self._fernet()
        raw = f.decrypt(data["cipher"].encode())
        return json.loads(raw)


def _mask_upi(upi: str) -> str | None:
    if not upi:
        return None
    if "@" not in upi:
        return "***"
    user, host = upi.split("@", 1)
    return f"{user[:2]}***@{host}"


class _SimpleCipher:
    """Lightweight encrypt for sandbox when cryptography is unavailable."""

    def __init__(self, key: str):
        self.key = hashlib.sha256(key.encode()).digest()

    def encrypt(self, data: bytes) -> bytes:
        out = bytes(b ^ self.key[i % len(self.key)] for i, b in enumerate(data))
        mac = hashlib.sha256(self.key + out).hexdigest()[:16]
        return base64.urlsafe_b64encode(mac.encode() + b":" + out)

    def decrypt(self, token: bytes) -> bytes:
        raw = base64.urlsafe_b64decode(token)
        mac, _, payload = raw.partition(b":")
        expect = hashlib.sha256(self.key + payload).hexdigest()[:16].encode()
        if not secrets.compare_digest(mac, expect):
            raise PayoutError("payout cipher integrity check failed")
        return bytes(b ^ self.key[i % len(self.key)] for i, b in enumerate(payload))

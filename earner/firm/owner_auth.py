"""Owner authentication for firm control-plane mutations."""

from __future__ import annotations

import hmac
import os
import secrets
import time


class OwnerAuthError(PermissionError):
    pass


class OwnerAuth:
    """Require a configured owner secret for mutating controls.

    Refuses to start in production-like modes with the development default.
    """

    DEV_DEFAULT = "owner-dev-secret"

    def __init__(self):
        self._sessions: dict[str, float] = {}

    @property
    def secret(self) -> str:
        return os.environ.get("FIRM_OWNER_SECRET", self.DEV_DEFAULT)

    def is_dev_default(self) -> bool:
        return self.secret == self.DEV_DEFAULT

    def require_configured(self, *, live_mode: bool) -> None:
        if live_mode and self.is_dev_default():
            raise OwnerAuthError(
                "FIRM_OWNER_SECRET must be set to a non-default value before live owner controls"
            )
        if len(self.secret) < 12:
            raise OwnerAuthError("FIRM_OWNER_SECRET must be at least 12 characters")

    def authenticate(self, provided: str | None, *, live_mode: bool) -> str:
        self.require_configured(live_mode=live_mode)
        if not provided or not hmac.compare_digest(provided, self.secret):
            raise OwnerAuthError("owner authentication failed")
        token = secrets.token_urlsafe(24)
        self._sessions[token] = time.time() + 3600
        return token

    def authorize(self, session_token: str | None, *, live_mode: bool) -> None:
        self.require_configured(live_mode=live_mode)
        if not session_token or session_token not in self._sessions:
            raise OwnerAuthError("owner session required")
        if self._sessions[session_token] < time.time():
            self._sessions.pop(session_token, None)
            raise OwnerAuthError("owner session expired")

    def authorize_secret_or_session(
        self,
        *,
        secret: str | None,
        session: str | None,
        live_mode: bool,
    ) -> None:
        """Accept either a valid session or a one-shot secret header."""
        self.require_configured(live_mode=live_mode)
        if session and session in self._sessions and self._sessions[session] >= time.time():
            return
        if secret and hmac.compare_digest(secret, self.secret):
            return
        raise OwnerAuthError("owner authentication required for this action")

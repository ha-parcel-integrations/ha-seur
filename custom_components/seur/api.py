"""SEUR miSEUR account client."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from .const import ACCOUNT_URL, INBOX_URL, TOKEN_URL

_LOGGER = logging.getLogger(__name__)


class SEURApiError(Exception):
    """A non-credential SEUR API failure."""

    def __init__(
        self,
        detail: str,
        *,
        status_code: int | None = None,
        response_keys: tuple[str, ...] | None = None,
        retry_after: float | None = None,
    ) -> None:
        """Store a safe failure category and HTTP status."""
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.response_keys = response_keys
        self.retry_after = retry_after


class SEURAuthError(SEURApiError):
    """The configured credentials were rejected."""


class SEURApiClient:
    """Read-only client for a miSEUR account inbox."""

    def __init__(
        self, email: str, password: str, session: aiohttp.ClientSession
    ) -> None:
        """Store account credentials without persisting token material."""
        self._email = email.strip()
        self._password = password
        self._session = session
        self._uuid: str | None = None
        self._token: str | None = None
        self._lock = asyncio.Lock()

    async def _json(self, method: str, url: str, **kwargs: Any) -> tuple[int, Any]:
        request = getattr(self._session, method.lower())
        async with request(url, **kwargs) as response:
            try:
                body = await response.json(content_type=None)
            except (ValueError, aiohttp.ContentTypeError) as err:
                raise SEURApiError(
                    "non-JSON response", status_code=response.status
                ) from err
            # Only structure is useful for support. Never log credentials,
            # tokens, account IDs, parcel IDs, or response values.
            _LOGGER.debug(
                "SEUR response: status=%s, JSON type=%s, keys=%s",
                response.status,
                type(body).__name__,
                sorted(body) if isinstance(body, dict) else None,
            )
            return response.status, body

    async def async_login(self) -> dict[str, Any]:
        """Validate the password grant and resolve the runtime account UUID."""
        status, token = await self._json(
            "POST",
            TOKEN_URL,
            data={
                "grant_type": "password",
                "client_id": "miseur",
                "username": self._email,
                "password": self._password,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if (
            status == 401
            and isinstance(token, dict)
            and token.get("error") == "invalid_grant"
        ):
            raise SEURAuthError("credentials rejected", status_code=status)
        if (
            status != 200
            or not isinstance(token, dict)
            or not isinstance(token.get("access_token"), str)
            or not token["access_token"]
        ):
            raise SEURApiError("unexpected token response", status_code=status)
        self._token = token["access_token"]
        status, profile = await self._json(
            "POST",
            ACCOUNT_URL,
            json={"username": self._email, "idioma": "es"},
            headers=self._headers(),
        )
        if (
            status != 200
            or not isinstance(profile, dict)
            or profile.get("codigo_error")
            or profile.get("msg_error")
        ):
            raise SEURApiError(
                "account lookup failed",
                status_code=status,
                response_keys=_response_keys(profile),
            )
        user = profile.get("usuario")
        uuid = user.get("uuid") if isinstance(user, dict) else None
        if not isinstance(uuid, str) or not uuid:
            raise SEURApiError("account UUID missing", status_code=status)
        username = user.get("username") if isinstance(user, dict) else None
        if isinstance(username, str) and username.casefold() != self._email.casefold():
            raise SEURAuthError("account identity mismatch", status_code=status)
        self._uuid = uuid
        return {"uuid": uuid}

    async def async_get_parcels(self) -> dict[str, list[dict[str, Any]]]:
        """Return received and sent shipment lists from the account inbox."""
        async with self._lock:
            if self._uuid is None or self._token is None:
                await self.async_login()
            status, payload = await self._fetch_inbox()
            if status == 401:
                # The access token lives 24h and there is no refresh token.
                await self.async_login()
                status, payload = await self._fetch_inbox()
        if (
            status != 200
            or not isinstance(payload, dict)
            or payload.get("codigo_error")
            or payload.get("msg_error")
        ):
            raise SEURApiError(
                "shipment inbox failed",
                status_code=status,
                response_keys=_response_keys(payload),
            )
        received, sent = payload.get("recibidos"), payload.get("enviados")
        if not isinstance(received, list) or not isinstance(sent, list):
            raise SEURApiError("unexpected shipment inbox shape", status_code=status)
        return {
            "incoming": [p for p in received if isinstance(p, dict)],
            "outgoing": [p for p in sent if isinstance(p, dict)],
        }

    async def _fetch_inbox(self) -> tuple[int, Any]:
        return await self._json(
            "POST",
            INBOX_URL,
            json={
                "idioma": "ES",
                "uuid": self._uuid,
                "email": self._email,
                "type": "[Shippings] Get Shipping List From Api",
            },
            headers=self._headers(),
        )

    def _headers(self) -> dict[str, str]:
        return {**_BROWSER_HEADERS, "Authorization": f"Bearer {self._token}"}


_BROWSER_HEADERS = {
    "Content-Type": "application/json",
    "Origin": "https://www.seur.com",
    "Referer": "https://www.seur.com/miseur/home",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "GET, POST",
    "Access-Control-Allow-Origin": "www.seur.com",
}


def _response_keys(value: Any) -> tuple[str, ...] | None:
    """Return response structure only, never values, for safe diagnostics."""
    return tuple(sorted(value)) if isinstance(value, dict) else None

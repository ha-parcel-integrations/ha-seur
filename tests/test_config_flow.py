"""Tests for the SEUR config and options flow."""

from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.seur.api import (
    SEURApiError,
    SEURAuthError,
)
from custom_components.seur.const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_INCLUDE_HISTORY,
    DOMAIN,
)

EMAIL = "user@example.test"
PASSWORD = "hunter2"
CREDENTIALS = {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}

LOGIN = "custom_components.seur.config_flow.SEURApiClient.async_login"


def _entry(email: str = EMAIL) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=email,
        unique_id=email,
        data={CONF_EMAIL: email, CONF_PASSWORD: PASSWORD},
        options={
            CONF_DELIVERED_FILTER_TYPE: "days",
            CONF_DELIVERED_FILTER_AMOUNT: 7,
            CONF_INCLUDE_HISTORY: False,
        },
    )


# ---------------------------------------------------------------------------
# user step
# ---------------------------------------------------------------------------


async def test_user_flow_creates_entry(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["step_id"] == "user"

    with patch(LOGIN, new=AsyncMock(return_value={"accountId": "abc"})):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS
        )

    assert result["type"] == "create_entry"
    assert result["title"] == EMAIL
    assert result["data"] == CREDENTIALS


@pytest.mark.parametrize(
    "error,expected",
    [
        (SEURAuthError("HTTP 401"), "invalid_auth"),
        (SEURApiError("HTTP 500"), "cannot_connect"),
        (aiohttp.ClientError("boom"), "cannot_connect"),
    ],
)
async def test_user_flow_surfaces_errors(hass, error, expected):
    """Bad credentials and an outage must not look the same to the user."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with patch(LOGIN, new=AsyncMock(side_effect=error)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS
        )

    assert result["type"] == "form"
    assert result["errors"] == {"base": expected}


async def test_user_flow_aborts_on_duplicate_account(hass):
    _entry().add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with patch(LOGIN, new=AsyncMock(return_value={})):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS
        )

    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


# ---------------------------------------------------------------------------
# reauth
# ---------------------------------------------------------------------------


async def test_reauth_updates_the_password(hass):
    entry = _entry()
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    with patch(LOGIN, new=AsyncMock(return_value={})):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: "new-password"}
        )
        await hass.async_block_till_done()

    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new-password"


async def test_reauth_rejects_a_different_account(hass):
    """Entering another account's credentials must not silently rebind."""
    entry = _entry()
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    with patch(LOGIN, new=AsyncMock(return_value={})):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_EMAIL: "someone-else@example.test", CONF_PASSWORD: PASSWORD},
        )

    assert result["type"] == "abort"
    assert result["reason"] == "wrong_account"
    assert entry.data[CONF_EMAIL] == EMAIL


async def test_reauth_surfaces_invalid_credentials(hass):
    entry = _entry()
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    with patch(LOGIN, new=AsyncMock(side_effect=SEURAuthError("HTTP 401"))):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS
        )

    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_auth"}


async def test_reauth_surfaces_connection_errors(hass):
    entry = _entry()
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    with patch(LOGIN, new=AsyncMock(side_effect=SEURApiError("HTTP 500"))):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS
        )

    assert result["errors"] == {"base": "cannot_connect"}


async def test_reauth_surfaces_client_error(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    with patch(LOGIN, new=AsyncMock(side_effect=aiohttp.ClientError("offline"))):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS
        )
    assert result["errors"] == {"base": "cannot_connect"}


# ---------------------------------------------------------------------------
# options
# ---------------------------------------------------------------------------


async def test_options_flow_saves_and_reloads(hass):
    entry = _entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"

    with patch.object(hass.config_entries, "async_schedule_reload") as schedule_reload:
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                "delivered": {
                    CONF_DELIVERED_FILTER_TYPE: "parcels",
                    CONF_DELIVERED_FILTER_AMOUNT: 5,
                },
                "history": {CONF_INCLUDE_HISTORY: True},
            },
        )

    assert result["type"] == "create_entry"
    assert result["data"] == {
        CONF_DELIVERED_FILTER_TYPE: "parcels",
        CONF_DELIVERED_FILTER_AMOUNT: 5,
        CONF_INCLUDE_HISTORY: True,
    }
    # A changed setting only takes effect on reload, so the flow schedules one
    # itself rather than registering an update listener (which is deprecated in
    # combination with reloading).
    schedule_reload.assert_called_once_with(entry.entry_id)

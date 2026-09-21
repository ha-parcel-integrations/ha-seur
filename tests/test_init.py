"""Setup behaviour for the SEUR account integration."""

from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.seur import async_unload_entry
from custom_components.seur.api import SEURApiError, SEURAuthError
from custom_components.seur.const import DOMAIN

from .payloads import EMAIL, shipment


async def test_setup_and_unload(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=EMAIL,
        unique_id=EMAIL,
        data={"email": EMAIL, "password": "synthetic"},
    )
    entry.add_to_hass(hass)
    with (
        patch("custom_components.seur.api.SEURApiClient.async_login", new=AsyncMock()),
        patch(
            "custom_components.seur.api.SEURApiClient.async_get_parcels",
            new=AsyncMock(return_value={"incoming": [shipment()], "outgoing": []}),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize(
    "error", [SEURAuthError("bad"), SEURApiError("down"), aiohttp.ClientError()]
)
async def test_setup_failure_closes_session(hass, error):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=EMAIL,
        unique_id=EMAIL,
        data={"email": EMAIL, "password": "synthetic"},
    )
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.seur.api.SEURApiClient.async_login",
            new=AsyncMock(side_effect=error),
        ),
        patch("aiohttp.ClientSession.close", new=AsyncMock()) as close,
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    close.assert_awaited()


async def test_platform_forward_failure_closes_session(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=EMAIL,
        unique_id=EMAIL,
        data={"email": EMAIL, "password": "synthetic"},
    )
    entry.add_to_hass(hass)
    with (
        patch("custom_components.seur.api.SEURApiClient.async_login", new=AsyncMock()),
        patch(
            "custom_components.seur.api.SEURApiClient.async_get_parcels",
            new=AsyncMock(return_value={"incoming": [], "outgoing": []}),
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ),
        patch("aiohttp.ClientSession.close", new=AsyncMock()) as close,
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    close.assert_awaited()


async def test_parcel_sensors_follow_the_inbox(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=EMAIL,
        unique_id=EMAIL,
        data={"email": EMAIL, "password": "synthetic"},
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor", DOMAIN, f"{entry.entry_id}_SEUR-TEST-GONE", config_entry=entry
    )
    get_parcels = AsyncMock(
        return_value={"incoming": [shipment("SEUR-TEST-A", "SX001")], "outgoing": []}
    )
    with (
        patch("custom_components.seur.api.SEURApiClient.async_login", new=AsyncMock()),
        patch(
            "custom_components.seur.api.SEURApiClient.async_get_parcels",
            new=get_parcels,
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        def parcel_ids() -> set[str]:
            return {
                item.unique_id.removeprefix(f"{entry.entry_id}_")
                for item in er.async_entries_for_config_entry(registry, entry.entry_id)
                if item.unique_id.startswith(f"{entry.entry_id}_SEUR-TEST")
            }

        assert parcel_ids() == {"SEUR-TEST-A"}

        get_parcels.return_value = {
            "incoming": [shipment("SEUR-TEST-B", "SX001")],
            "outgoing": [],
        }
        await entry.runtime_data.coordinator.async_refresh()
        await hass.async_block_till_done()
        assert parcel_ids() == {"SEUR-TEST-B"}

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_unload_returns_false_when_platform_unload_fails(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, data={"email": EMAIL, "password": "synthetic"}
    )
    with patch.object(
        hass.config_entries, "async_unload_platforms", new=AsyncMock(return_value=False)
    ):
        assert not await async_unload_entry(hass, entry)

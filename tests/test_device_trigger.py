"""Tests for SEUR device triggers."""

from unittest.mock import AsyncMock, patch

from homeassistant.const import CONF_DEVICE_ID, CONF_TYPE

from custom_components.seur.const import DOMAIN
from custom_components.seur.device_trigger import (
    TRIGGER_EVENTS,
    async_attach_trigger,
    async_get_triggers,
)


async def test_get_triggers_returns_all_four(hass):
    triggers = await async_get_triggers(hass, "device123")
    types = {t["type"] for t in triggers}
    assert types == {
        "parcel_registered",
        "parcel_status_changed",
        "parcel_delivered",
        "parcel_delivery_time_changed",
    }
    for trigger in triggers:
        assert trigger["domain"] == DOMAIN
        assert trigger["device_id"] == "device123"


def test_trigger_events_map_to_domain_prefix():
    assert TRIGGER_EVENTS["parcel_registered"] == f"{DOMAIN}_parcel_registered"


async def test_attach_trigger_delegates_to_event_platform(hass):
    with patch(
        "custom_components.seur.device_trigger.event_trigger.async_attach_trigger",
        new=AsyncMock(return_value="unsubscribe"),
    ) as attach:
        result = await async_attach_trigger(
            hass,
            {CONF_TYPE: "parcel_delivered", CONF_DEVICE_ID: "device123"},
            AsyncMock(),
            {},
        )
    assert result == "unsubscribe"
    attach.assert_awaited_once()

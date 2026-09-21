"""Tests for received/sent account coordination and events."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.seur.api import SEURApiError, SEURAuthError
from custom_components.seur.const import (
    DOMAIN,
    HOT_INTERVAL_MINUTES,
    MID_INTERVAL_MINUTES,
    ParcelStatus,
)
from custom_components.seur.coordinator import (
    SEURCoordinator,
    _hottest_tier_minutes,
    _in_quiet_window,
    _next_anchor,
    _next_update_interval,
    _stagger_minutes,
)

from .payloads import EMAIL, shipment


def entry() -> MockConfigEntry:
    """Build a synthetic account entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=EMAIL,
        unique_id=EMAIL,
        data={"email": EMAIL, "password": "synthetic"},
        options={"delivered_filter_type": "parcels", "delivered_filter_amount": 20},
    )


async def test_splits_received_and_sent_and_suppresses_first_events(hass):
    config = entry()
    config.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcels.return_value = {
        "incoming": [shipment()],
        "outgoing": [shipment("SEUR-TEST-OUT", "SX001")],
    }
    coordinator = SEURCoordinator(hass, client, config)
    events = []
    hass.bus.async_listen(
        f"{DOMAIN}_parcel_status_changed", lambda event: events.append(event)
    )
    data = await coordinator._async_update_data()
    assert [item["barcode"] for item in data] == ["SEUR-TEST-0001"]
    assert [item["barcode"] for item in coordinator.outgoing] == ["SEUR-TEST-OUT"]
    assert events == []


async def test_incoming_and_outgoing_transitions_have_distinct_events(hass):
    config = entry()
    config.add_to_hass(hass)
    client = AsyncMock()
    coordinator = SEURCoordinator(hass, client, config)
    incoming, outgoing = [], []
    hass.bus.async_listen(
        f"{DOMAIN}_parcel_delivered", lambda event: incoming.append(event)
    )
    hass.bus.async_listen(
        f"{DOMAIN}_outgoing_parcel_status_changed", lambda event: outgoing.append(event)
    )
    client.async_get_parcels.return_value = {
        "incoming": [shipment("SEUR-TEST-IN", "SX001")],
        "outgoing": [shipment("SEUR-TEST-OUT", "SX001")],
    }
    await coordinator._async_update_data()
    client.async_get_parcels.return_value = {
        "incoming": [shipment("SEUR-TEST-IN", "LL003")],
        "outgoing": [shipment("SEUR-TEST-OUT", "LC003")],
    }
    await coordinator._async_update_data()
    await hass.async_block_till_done()
    assert incoming[0].data["status"] is ParcelStatus.DELIVERED
    assert outgoing[0].data["new_status"] is ParcelStatus.OUT_FOR_DELIVERY


async def test_event_helpers_cover_new_and_unchanged_parcels(hass):
    config = entry()
    config.add_to_hass(hass)
    coordinator = SEURCoordinator(hass, AsyncMock(), config)
    coordinator._known_state = {"known": ParcelStatus.IN_TRANSIT}
    coordinator._known_outgoing_state = {"out": ParcelStatus.IN_TRANSIT}
    fired = []
    hass.bus.async_listen(
        f"{DOMAIN}_parcel_registered", lambda event: fired.append(event)
    )
    hass.bus.async_listen(
        f"{DOMAIN}_outgoing_parcel_delivered", lambda event: fired.append(event)
    )
    coordinator._fire_change_events(
        [
            {
                "barcode": "new",
                "status": ParcelStatus.REGISTERED,
                "planned_from": None,
                "planned_to": None,
            },
            {"status": ParcelStatus.IN_TRANSIT},
        ]
    )
    coordinator._fire_outgoing_change_events(
        [{"barcode": "out", "status": ParcelStatus.DELIVERED}]
    )
    await hass.async_block_till_done()
    assert len(fired) == 2


def test_polling_helpers_cover_quiet_and_hot_tiers():
    now = datetime(2026, 9, 21, 1, tzinfo=timezone.utc)
    assert _in_quiet_window(now)
    assert _next_anchor(now).hour == 6
    assert _next_update_interval(now, MID_INTERVAL_MINUTES, "entry").total_seconds() > 0
    assert _hottest_tier_minutes([], now) == MID_INTERVAL_MINUTES
    assert (
        _hottest_tier_minutes(
            [{"status": ParcelStatus.OUT_FOR_DELIVERY, "planned_from": None}], now
        )
        == HOT_INTERVAL_MINUTES
    )
    assert (
        _hottest_tier_minutes(
            [{"status": ParcelStatus.OUT_FOR_DELIVERY, "planned_from": "bad"}], now
        )
        == HOT_INTERVAL_MINUTES
    )


async def test_rate_limit_uses_update_failed_backoff(hass):
    config = entry()
    config.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcels.side_effect = SEURApiError("limited", status_code=429)
    coordinator = SEURCoordinator(hass, client, config)
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    assert coordinator._consecutive_429 == 1


def test_polling_helpers_cover_anchors_and_planned_windows():
    evening = datetime(2026, 9, 21, 22, tzinfo=timezone.utc)
    assert _next_anchor(evening) == datetime(2026, 9, 22, 0, tzinfo=timezone.utc)
    stagger = _stagger_minutes("entry")
    late = datetime(2026, 9, 21, 23, 59 - stagger, tzinfo=timezone.utc)
    assert late + _next_update_interval(late, MID_INTERVAL_MINUTES, "entry") == (
        datetime(2026, 9, 22, 0, tzinfo=timezone.utc)
    )
    soon = {
        "status": ParcelStatus.OUT_FOR_DELIVERY,
        "planned_from": "2026-09-21T13:00:00+00:00",
    }
    later = {**soon, "planned_from": "2026-09-22T13:00:00+00:00"}
    noon = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)
    assert _hottest_tier_minutes([soon], noon) == HOT_INTERVAL_MINUTES
    assert _hottest_tier_minutes([later], noon) == MID_INTERVAL_MINUTES


async def test_properties_and_cached_device_id(hass):
    config = entry()
    config.add_to_hass(hass)
    coordinator = SEURCoordinator(hass, AsyncMock(), config)
    assert coordinator.current_tier_minutes is None
    assert coordinator.delivered_codes == set()
    assert coordinator._device_id() is None
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=config.entry_id, identifiers={(DOMAIN, config.entry_id)}
    )
    assert coordinator._device_id() == device.id
    assert coordinator._device_id() == device.id


async def test_auth_and_non_rate_limit_errors(hass):
    config = entry()
    config.add_to_hass(hass)
    client = AsyncMock()
    coordinator = SEURCoordinator(hass, client, config)
    client.async_get_parcels.side_effect = SEURAuthError("expired")
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()
    client.async_get_parcels.side_effect = SEURApiError("down", status_code=500)
    with pytest.raises(SEURApiError):
        await coordinator._async_update_data()


async def test_status_and_delivery_time_changes_fire_events(hass):
    config = entry()
    config.add_to_hass(hass)
    coordinator = SEURCoordinator(hass, AsyncMock(), config)
    coordinator._known_state = {"known": ParcelStatus.IN_TRANSIT}
    coordinator._known_delivery_times = {"known": (None, None)}
    coordinator._known_outgoing_state = {"out": ParcelStatus.IN_TRANSIT}
    fired = []
    for suffix in (
        "parcel_status_changed",
        "parcel_delivery_time_changed",
        "outgoing_parcel_status_changed",
        "outgoing_parcel_delivered",
    ):
        hass.bus.async_listen(f"{DOMAIN}_{suffix}", fired.append)
    coordinator._fire_change_events(
        [
            {
                "barcode": "known",
                "status": ParcelStatus.OUT_FOR_DELIVERY,
                "planned_from": "2026-09-21T13:00:00+00:00",
                "planned_to": None,
            }
        ]
    )
    coordinator._fire_outgoing_change_events(
        [
            {"barcode": "out", "status": ParcelStatus.IN_TRANSIT},
            {"barcode": "unknown", "status": ParcelStatus.DELIVERED},
        ]
    )
    await hass.async_block_till_done()
    assert sorted(event.event_type for event in fired) == [
        f"{DOMAIN}_parcel_delivery_time_changed",
        f"{DOMAIN}_parcel_status_changed",
    ]

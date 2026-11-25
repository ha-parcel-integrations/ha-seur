"""Tests for the contents action, its routing and the store lifecycle."""

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.seur.const import CONF_TRACKING_CODE, DOMAIN

from .payloads import ACTIVE_CODE, DELIVERED_CODE, shipment

API = "custom_components.seur.api.SEURApiClient"
UNLISTED = "SEUR-TEST-9999"
STORE_KEY = f"{DOMAIN}.contents.%s"


def _entry(name: str = "a") -> MockConfigEntry:
    email = f"{name}@example.test"
    return MockConfigEntry(
        domain=DOMAIN,
        title=email,
        unique_id=email,
        data={"email": email, "password": "synthetic"},
    )


async def _setup(hass, entry=None, incoming=None, outgoing=None) -> MockConfigEntry:
    entry = entry or _entry()
    entry.add_to_hass(hass)
    envelope = {
        "incoming": [shipment()] if incoming is None else incoming,
        "outgoing": outgoing or [],
    }
    with (
        patch(f"{API}.async_login", new=AsyncMock()),
        patch(f"{API}.async_get_parcels", new=AsyncMock(return_value=envelope)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def _call(hass, data: dict) -> None:
    await hass.services.async_call(DOMAIN, "update_contents", data, blocking=True)
    await hass.async_block_till_done()


def _contents(entry, code: str = ACTIVE_CODE) -> str | None:
    parcels = {p["barcode"]: p for p in entry.runtime_data.coordinator.data}
    return parcels[code]["contents"]


async def test_a_parcel_has_no_contents_without_a_stored_value(hass):
    assert _contents(await _setup(hass)) is None


async def test_update_contents_sets_replaces_and_clears(hass):
    entry = await _setup(hass)
    await _call(hass, {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": " Running  shoes "})
    assert _contents(entry) == "Running shoes"
    await _call(hass, {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Winter socks"})
    assert _contents(entry) == "Winter socks"
    await _call(hass, {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "  "})
    assert _contents(entry) is None
    await _call(hass, {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Shoes"})
    await _call(hass, {CONF_TRACKING_CODE: ACTIVE_CODE})
    assert _contents(entry) is None


async def test_update_contents_normalizes_the_code(hass):
    entry = await _setup(hass)
    await _call(
        hass, {CONF_TRACKING_CODE: f" {ACTIVE_CODE.lower()} ", "contents": "Shoes"}
    )
    assert _contents(entry) == "Shoes"


async def test_update_contents_reaches_delivered_and_outgoing_parcels(hass, freezer):
    freezer.move_to("2026-09-22T12:00:00+00:00")
    entry = await _setup(
        hass,
        incoming=[shipment(DELIVERED_CODE, "LL003")],
        outgoing=[shipment("SEUR-OUT-0001")],
    )
    coordinator = entry.runtime_data.coordinator
    await _call(hass, {CONF_TRACKING_CODE: DELIVERED_CODE, "contents": "Shoes"})
    await _call(hass, {CONF_TRACKING_CODE: "SEUR-OUT-0001", "contents": "Socks"})
    assert [p["contents"] for p in coordinator.delivered] == ["Shoes"]
    assert [p["contents"] for p in coordinator.outgoing] == ["Socks"]


async def test_update_contents_works_for_a_code_that_is_not_listed(hass):
    entry = await _setup(hass)
    await _call(hass, {CONF_TRACKING_CODE: UNLISTED, "contents": "Shoes"})
    assert entry.runtime_data.coordinator.contents_store.get(UNLISTED) == "Shoes"


async def test_update_contents_never_polls_the_carrier(hass):
    entry = await _setup(hass)
    coordinator = entry.runtime_data.coordinator
    data = {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"}
    with (
        patch(f"{API}.async_get_parcels", new=AsyncMock()) as fetch,
        patch(f"{API}.async_login", new=AsyncMock()) as login,
        patch.object(coordinator, "async_request_refresh", new=AsyncMock()) as refresh,
        patch.object(
            coordinator,
            "async_update_listeners",
            wraps=coordinator.async_update_listeners,
        ) as notify,
    ):
        await _call(hass, data)
        assert notify.call_count == 1
        await _call(hass, data)
        assert notify.call_count == 1
        await _call(hass, {CONF_TRACKING_CODE: UNLISTED})
        assert notify.call_count == 1
    fetch.assert_not_called()
    login.assert_not_called()
    refresh.assert_not_called()


async def test_a_contents_change_alone_fires_no_event(hass):
    await _setup(hass)
    events = []
    for name in ("registered", "status_changed", "delivered"):
        hass.bus.async_listen(f"{DOMAIN}_parcel_{name}", events.append)
    await _call(hass, {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Shoes"})
    assert events == []


async def test_a_poll_keeps_the_stored_value_and_it_rides_on_events(hass):
    entry = await _setup(hass)
    coordinator = entry.runtime_data.coordinator
    await _call(hass, {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Shoes"})
    events = []
    hass.bus.async_listen(f"{DOMAIN}_parcel_delivered", events.append)
    envelope = {"incoming": [shipment(status="LL003")], "outgoing": []}
    with patch(f"{API}.async_get_parcels", new=AsyncMock(return_value=envelope)):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert [e.data["contents"] for e in events] == ["Shoes"]


async def test_known_codes_list_both_directions(hass):
    entry = await _setup(hass, outgoing=[shipment("SEUR-OUT-0001")])
    assert entry.runtime_data.coordinator.known_codes == {
        ACTIVE_CODE,
        "SEUR-OUT-0001",
    }


async def test_update_contents_rejects_an_empty_code(hass):
    await _setup(hass)
    with pytest.raises(ServiceValidationError):
        await _call(hass, {CONF_TRACKING_CODE: "  "})


async def test_update_contents_needs_a_loaded_entry(hass):
    entry = await _setup(hass)
    entry.mock_state(hass, ConfigEntryState.SETUP_RETRY)
    try:
        with pytest.raises(ServiceValidationError):
            await _call(hass, {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Shoes"})
    finally:
        entry.mock_state(hass, ConfigEntryState.LOADED)


async def test_two_entries_listing_the_code_both_get_the_value(hass):
    first = await _setup(hass, _entry("a"))
    second = await _setup(hass, _entry("b"))
    await _call(hass, {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Shoes"})
    assert _contents(first) == "Shoes"
    assert _contents(second) == "Shoes"


async def test_entry_required_when_no_entry_lists_the_code(hass):
    await _setup(hass, _entry("a"))
    await _setup(hass, _entry("b"))
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, {CONF_TRACKING_CODE: UNLISTED, "contents": "Shoes"})
    assert err.value.translation_key == "update_contents_entry_required"


async def test_config_entry_id_targets_exactly_one_entry(hass):
    first = await _setup(hass, _entry("a"))
    second = await _setup(hass, _entry("b"))
    await _call(
        hass,
        {
            CONF_TRACKING_CODE: ACTIVE_CODE,
            "contents": "Shoes",
            "config_entry_id": second.entry_id,
        },
    )
    assert _contents(first) is None
    assert _contents(second) == "Shoes"

    await _call(
        hass,
        {
            CONF_TRACKING_CODE: UNLISTED,
            "contents": "Socks",
            "config_entry_id": first.entry_id,
        },
    )
    assert first.runtime_data.coordinator.contents_store.get(UNLISTED) == "Socks"
    assert second.runtime_data.coordinator.contents_store.get(UNLISTED) is None


async def test_config_entry_id_of_an_unloaded_entry_is_rejected(hass):
    first = await _setup(hass, _entry("a"))
    second = await _setup(hass, _entry("b"))
    second.mock_state(hass, ConfigEntryState.SETUP_RETRY)
    try:
        with pytest.raises(ServiceValidationError):
            await _call(
                hass,
                {
                    CONF_TRACKING_CODE: ACTIVE_CODE,
                    "contents": "Shoes",
                    "config_entry_id": second.entry_id,
                },
            )
        await _call(hass, {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Shoes"})
        assert _contents(first) == "Shoes"
        assert second.runtime_data.coordinator.contents_store.get(ACTIVE_CODE) is None
    finally:
        second.mock_state(hass, ConfigEntryState.LOADED)


async def test_the_action_stays_until_the_last_entry_unloads(hass):
    first = await _setup(hass, _entry("a"))
    second = await _setup(hass, _entry("b"))
    assert await hass.config_entries.async_unload(first.entry_id)
    assert hass.services.has_service(DOMAIN, "update_contents")
    assert await hass.config_entries.async_unload(second.entry_id)
    assert not hass.services.has_service(DOMAIN, "update_contents")


async def test_a_stored_value_survives_a_restart_of_the_entry(hass, hass_storage):
    entry = await _setup(hass)
    await _call(hass, {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Shoes"})
    store = entry.runtime_data.coordinator.contents_store
    await store._store.async_save(store._data_to_save())

    assert await hass.config_entries.async_unload(entry.entry_id)
    envelope = {"incoming": [shipment()], "outgoing": []}
    with (
        patch(f"{API}.async_login", new=AsyncMock()),
        patch(f"{API}.async_get_parcels", new=AsyncMock(return_value=envelope)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert _contents(entry) == "Shoes"


async def test_an_old_entry_without_a_store_still_loads(hass, hass_storage):
    entry = await _setup(hass)
    assert STORE_KEY % entry.entry_id not in hass_storage
    assert _contents(entry) is None


async def test_removing_the_entry_removes_its_store(hass, hass_storage):
    entry = await _setup(hass)
    await _call(hass, {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Shoes"})
    store = entry.runtime_data.coordinator.contents_store
    await store._store.async_save(store._data_to_save())
    key = STORE_KEY % entry.entry_id
    assert key in hass_storage

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage

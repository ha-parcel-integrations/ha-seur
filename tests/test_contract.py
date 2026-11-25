"""The parcel contract's checks, run over every normalised sample."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.seur.const import ParcelStatus
from custom_components.seur.parcels import (
    is_pickup,
    normalize_parcel,
    parse_iso,
    planned_for_status,
    planned_window,
)

from .payloads import shipment, situation

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "contents",
    "status",
    "raw_status",
    "delivered",
    "delivered_at",
    "planned_from",
    "planned_to",
    "pickup",
    "pickup_point",
    "url",
    "weight",
    "dimensions",
    "history",
    "raw",
]
ZONE = ZoneInfo("Europe/Madrid")


def _pickup(code: str = "LI574") -> dict:
    raw = shipment(status=code)
    raw["tipo_entrega"] = "SHOP"
    raw["destinatario"] = {"centro_seur": "Synthetic Shop"}
    return raw


def _collected() -> dict:
    raw = _pickup("LL010")
    raw["situaciones"] = [
        situation("LL010", "2026-09-22T16:00:00Z"),
        situation("LI574", "2026-09-21T09:00:00Z"),
        situation("SX010", "2026-09-19T09:00:00Z"),
    ]
    return raw


def _collected_without_flag() -> dict:
    raw = _collected()
    raw["tipo_entrega"] = "HOME"
    return raw


SAMPLES = {
    "active": shipment,
    "delivered": lambda: shipment(status="LL003"),
    "at_pickup_point": _pickup,
    "collected": _collected,
    "collected_without_flag": _collected_without_flag,
    "in_transit_to_shop": lambda: _pickup("SX001"),
    "problem": lambda: shipment(status="NEW"),
    "bare": lambda: {"clave_envio": "SEUR-TEST-0009"},
}


@pytest.fixture(params=SAMPLES)
def parcel(request) -> dict:
    return normalize_parcel(SAMPLES[request.param](), include_history=True)


def test_keys_and_order(parcel):
    assert list(parcel) == CANONICAL_KEYS


def test_delivered_follows_status(parcel):
    assert parcel["delivered"] is (parcel["status"] is ParcelStatus.DELIVERED)


def test_delivered_at_only_when_delivered(parcel):
    if not parcel["delivered"]:
        assert parcel["delivered_at"] is None


def test_planned_window_is_ordered_and_anchored(parcel):
    if parcel["planned_to"] is not None:
        assert parcel["planned_from"] is not None
        assert parse_iso(parcel["planned_to"]) > parse_iso(parcel["planned_from"])


def test_planned_is_empty_when_the_parcel_is_done_or_waiting(parcel):
    if parcel["status"] in (
        ParcelStatus.DELIVERED,
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.RETURNING,
    ):
        assert parcel["planned_from"] is None
        assert parcel["planned_to"] is None


def test_pickup_is_a_bool_and_the_point_needs_it(parcel):
    assert isinstance(parcel["pickup"], bool)
    if not parcel["pickup"]:
        assert parcel["pickup_point"] is None


def test_history_is_none_only_when_the_option_is_off():
    assert normalize_parcel(shipment())["history"] is None
    assert normalize_parcel(shipment(), include_history=True)["history"] != None  # noqa: E711


def test_a_collected_pickup_parcel_stays_pickup():
    parcel = normalize_parcel(_collected())
    assert parcel["status"] is ParcelStatus.DELIVERED
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] == "2026-09-22T16:00:00Z"
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] == "Synthetic Shop"


def test_pickup_survives_collection_without_the_route_flag():
    parcel = normalize_parcel(_collected_without_flag())
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] == "Synthetic Shop"


def test_a_home_parcel_is_not_pickup():
    assert normalize_parcel(shipment())["pickup"] is False


def test_is_pickup_uses_flag_status_and_events():
    assert is_pickup(True, ParcelStatus.IN_TRANSIT) is True
    assert is_pickup(None, ParcelStatus.AT_PICKUP_POINT) is True
    assert is_pickup(False, ParcelStatus.DELIVERED, [ParcelStatus.AT_PICKUP_POINT])
    assert is_pickup(False, ParcelStatus.DELIVERED, [None]) is False


def test_planned_for_status_empties_done_statuses():
    for status in (
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.RETURNING,
        ParcelStatus.DELIVERED,
    ):
        assert planned_for_status(status, "a", "b") == (None, None)
    assert planned_for_status(ParcelStatus.IN_TRANSIT, "a", "b") == ("a", "b")


def test_a_moment_ends_at_the_end_of_that_day():
    start, end = planned_window("2099-04-29T13:00:00Z", None, zone=ZONE)
    stop = datetime.fromisoformat(end)
    assert (stop.hour, stop.minute, stop.second, stop.microsecond) == (23, 59, 59, 0)
    assert stop.date() == parse_iso(start).astimezone(ZONE).date()


def test_a_date_covers_the_whole_day():
    start, end = planned_window(date(2099, 4, 29), None, zone=ZONE)
    first, last = datetime.fromisoformat(start), datetime.fromisoformat(end)
    assert (first.hour, first.minute, first.second) == (0, 0, 0)
    assert (last.hour, last.minute, last.second, last.microsecond) == (23, 59, 59, 0)
    assert first.date() == last.date()


def test_two_dates_span_the_days():
    start, end = planned_window("2099-04-29", "2099-04-30", zone=ZONE)
    assert start.startswith("2099-04-29T00:00:00")
    assert end.startswith("2099-04-30T23:59:59")


def test_planned_window_edge_shapes():
    assert planned_window(None, None, zone=ZONE) == (None, None)
    assert planned_window("garbage", None, zone=ZONE) == (None, None)
    assert planned_window(5, None, zone=ZONE) == (None, None)
    assert planned_window(None, "2099-04-29", zone=ZONE)[0].startswith("2099-04-29")
    naive = planned_window(datetime(2099, 4, 29, 10), None, zone=ZONE)
    assert naive[0] == "2099-04-29T10:00:00+02:00"
    reversed_ = planned_window(
        "2099-04-30T10:00:00Z", "2099-04-29T10:00:00Z", zone=ZONE
    )
    assert reversed_[0] == "2099-04-29T10:00:00Z"
    assert planned_window("2099-04-29T21:59:59Z", None, zone=ZONE)[1] is None
    same = planned_window("2099-04-29T10:00:00Z", "2099-04-29T10:00:00Z", zone=ZONE)
    assert same[0] == "2099-04-29T10:00:00Z"
    window = planned_window("2099-04-29T10:00:00Z", "2099-04-29T12:00:00Z", zone=ZONE)
    assert window == ("2099-04-29T10:00:00Z", "2099-04-29T12:00:00Z")

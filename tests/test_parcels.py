"""Tests for safe SEUR normalisation."""

from custom_components.seur.const import ParcelStatus
from custom_components.seur.parcels import (
    build_history,
    format_dimensions,
    map_event_status,
    map_parcel_status,
    normalize_parcel,
    parse_iso,
    to_iso_timestamp,
)

from .payloads import DELIVERED_CODE, shipment


def test_maps_all_observed_codes():
    assert map_parcel_status("SX010") is ParcelStatus.REGISTERED
    assert map_parcel_status("SX001") is ParcelStatus.IN_TRANSIT
    assert map_parcel_status("LI567") is ParcelStatus.IN_TRANSIT
    assert map_parcel_status("LC003") is ParcelStatus.OUT_FOR_DELIVERY
    assert map_parcel_status("LL003") is ParcelStatus.DELIVERED
    assert map_parcel_status("NEW") is ParcelStatus.UNKNOWN


def test_normalizes_full_raw_data_and_reverses_history():
    raw = shipment()
    raw["remitente"] = {"razon_social": "Synthetic Sender"}
    raw["destinatario"] = {
        "razon_social": "Synthetic Recipient",
        "email": "private@example.test",
    }
    parcel = normalize_parcel(raw, include_history=True)
    assert parcel["barcode"] == "SEUR-TEST-0001"
    assert parcel["status"] is ParcelStatus.OUT_FOR_DELIVERY
    assert parcel["sender"] == "Synthetic Sender"
    assert parcel["receiver"] == "Synthetic Recipient"
    assert parcel["history"][0]["raw_status"] == "SX010"
    assert parcel["raw"] is raw
    assert parcel["raw"]["destinatario"]["email"] == "private@example.test"


def test_party_name_falls_back_and_ignores_non_names():
    raw = shipment()
    raw["remitente"] = {"razon_social": " ", "nombre_contacto": "Fallback Sender"}
    raw["destinatario"] = "not an object"
    parcel = normalize_parcel(raw)
    assert parcel["sender"] == "Fallback Sender"
    assert parcel["receiver"] is None


def test_delivered_and_pickup_are_conservative():
    delivered = normalize_parcel(shipment(DELIVERED_CODE, "LL003"))
    assert delivered["delivered"] is True
    assert delivered["delivered_at"] == "2026-09-21T09:00:00Z"
    raw = shipment()
    raw["tipo_entrega"] = "SHOP"
    pickup = normalize_parcel(raw)
    assert pickup["pickup"] is False
    assert pickup["pickup_point"] is None


def test_payload_shape_warnings_are_one_shot(caplog):
    raw = shipment()
    raw["situaciones"][1]["fecha"] = 1726909200
    for _ in range(3):
        build_history(raw["situaciones"])
        normalize_parcel({"clave_envio": None})
    messages = [record.getMessage() for record in caplog.records]
    assert sum("timestamp shape" in message for message in messages) == 1
    assert sum("without a barcode" in message for message in messages) == 1


def test_history_ignores_bad_event_and_caps():
    events = [
        *shipment()["situaciones"],
        {"cod_situacion": "SX001", "fecha": "not-a-time"},
    ]
    assert len(build_history(events, max_events=2)) == 2


def test_missing_barcode_is_skipped():
    assert normalize_parcel({"situaciones": []}) is None


def test_helper_edge_cases_and_bad_payload_fields():
    assert map_event_status(None) is None
    assert to_iso_timestamp(1) is None
    assert format_dimensions(1, None, 3) is None
    assert format_dimensions(1, 2, 3)["text"] == "1 x 2 x 3 cm"
    assert parse_iso(1) is None
    assert parse_iso("bad") is None
    raw = shipment()
    raw["peso"] = True
    raw["situaciones"] = "invalid"
    assert normalize_parcel(raw)["weight"] is None
    assert build_history(None) == []
    assert build_history(["not an event"]) == []

"""Synthetic, non-identifying payload shapes for SEUR tests."""

from __future__ import annotations

ACTIVE_CODE = "SEUR-TEST-0001"
DELIVERED_CODE = "SEUR-TEST-0002"
EMAIL = "parcel.tester@example.test"
UUID = "00000000-1111-2222-3333-444444444444"


def situation(code: str, timestamp: str) -> dict[str, str]:
    """Return one newest-first SEUR status event."""
    return {"cod_situacion": code, "fecha": timestamp, "grupo_situacion": "TEST"}


def shipment(code: str = ACTIVE_CODE, status: str = "LC003") -> dict:
    """Return a safe, fictional SEUR shipment without contact data."""
    return {
        "clave_envio": code,
        "identificador_busqueda": f"lookup-{code}",
        "peso": 1.25,
        "num_bultos": 1,
        "tipo_envio": "STANDARD",
        "tipo_recogida": "HOME",
        "tipo_entrega": "HOME",
        "cod_servicio": "2",
        "cod_producto": "10",
        "is_enviado": False,
        "situaciones": [
            situation(status, "2026-09-21T09:00:00Z"),
            situation("SX001", "2026-09-20T09:00:00Z"),
            situation("SX010", "2026-09-19T09:00:00Z"),
        ],
    }


def inbox(
    *, received: list[dict] | None = None, sent: list[dict] | None = None
) -> dict:
    """Return the complete synthetic inbox envelope."""
    return {
        "codigo_error": None,
        "msg_error": None,
        "recibidos": received or [],
        "enviados": sent or [],
    }


TOKEN = {"access_token": "synthetic-token"}
PROFILE = {
    "codigo_error": None,
    "msg_error": None,
    "usuario": {"uuid": UUID, "username": EMAIL},
}

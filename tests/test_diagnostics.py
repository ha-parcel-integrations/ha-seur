"""Diagnostics must never disclose account or package identifiers."""

from datetime import timedelta
from unittest.mock import MagicMock

from custom_components.seur.diagnostics import async_get_config_entry_diagnostics


async def test_diagnostics_redact_seur_identifiers(hass):
    entry = MagicMock()
    coordinator = entry.runtime_data.coordinator
    entry.options = {}
    coordinator.current_tier_minutes = 15
    coordinator.update_interval = timedelta(minutes=15)
    coordinator.data = [
        {
            "barcode": "SEUR-TEST-0001",
            "raw": {
                "clave_envio": "SEUR-TEST-0001",
                "delivery": {"email": "hidden@example.test"},
            },
        }
    ]
    coordinator.delivered = []
    coordinator.outgoing = []
    coordinator.delivered_outgoing = []
    coordinator.delivered_codes = set()
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["incoming"][0]["barcode"] == "**REDACTED**"
    assert result["incoming"][0]["raw"]["clave_envio"] == "**REDACTED**"
    assert result["incoming"][0]["raw"]["delivery"] == "**REDACTED**"

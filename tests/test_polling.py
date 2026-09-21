"""Tests for account polling tiers."""

from datetime import datetime, timezone

from custom_components.seur.const import (
    HOT_INTERVAL_MINUTES,
    MID_INTERVAL_MINUTES,
    ParcelStatus,
)
from custom_components.seur.coordinator import _hottest_tier_minutes


def test_outgoing_out_for_delivery_keeps_account_hot():
    assert (
        _hottest_tier_minutes(
            [{"status": ParcelStatus.OUT_FOR_DELIVERY, "planned_from": None}],
            datetime.now(timezone.utc),
        )
        == HOT_INTERVAL_MINUTES
    )


def test_other_active_states_use_mid_tier():
    assert (
        _hottest_tier_minutes(
            [{"status": ParcelStatus.IN_TRANSIT, "planned_from": None}],
            datetime.now(timezone.utc),
        )
        == MID_INTERVAL_MINUTES
    )

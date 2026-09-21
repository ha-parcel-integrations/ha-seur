"""Coordinator for the SEUR parcel tracker integration.

Fetching and event firing only — the parcel mapping lives in :mod:`.parcels`,
shared verbatim with the account-less variant.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    SEURApiClient,
    SEURApiError,
    SEURAuthError,
)
from .const import (
    CONF_INCLUDE_HISTORY,
    DEFAULT_INCLUDE_HISTORY,
    DOMAIN,
    HOT_INTERVAL_MINUTES,
    HOT_LOOKAHEAD_HOURS,
    MID_INTERVAL_MINUTES,
    QUIET_WINDOW_END_HOUR,
    QUIET_WINDOW_START_HOUR,
    STAGGER_MINUTES,
    ParcelStatus,
)
from .parcels import apply_delivered_filter, normalize_parcel, sort_parcels_by_ts

_LOGGER = logging.getLogger(__name__)

# Base for the 429 backoff when the carrier's response carries no
# ``Retry-After`` of its own: ``BACKOFF_BASE_SECONDS * 2**consecutive_429``,
# capped at ``BACKOFF_CAP_SECONDS``.
BACKOFF_BASE_SECONDS = 60
BACKOFF_CAP_SECONDS = 3600


def _stagger_minutes(entry_id: str) -> int:
    """Deterministic per-install offset, stable across restarts."""
    digest = hashlib.sha256(entry_id.encode()).hexdigest()
    return int(digest, 16) % STAGGER_MINUTES


def _in_quiet_window(moment: datetime) -> bool:
    """Whether ``moment`` (local time) falls in the no-polling window."""
    return QUIET_WINDOW_START_HOUR <= moment.hour < QUIET_WINDOW_END_HOUR


def _next_anchor(now: datetime) -> datetime:
    """Return the next of the two daily anchors (00:00 / 06:00 local)."""
    six_today = now.replace(
        hour=QUIET_WINDOW_END_HOUR, minute=0, second=0, microsecond=0
    )
    if now < six_today:
        return six_today
    midnight_tomorrow = (now + timedelta(days=1)).replace(
        hour=QUIET_WINDOW_START_HOUR, minute=0, second=0, microsecond=0
    )
    return midnight_tomorrow


def _hottest_tier_minutes(active_parcels: list[dict], now: datetime) -> int:
    """Tier for the account-based model (Section 2.2).

    Unlike the barcode-based model this never returns ``None`` — a single
    account call already returns the full state, so the mid-tier poll is
    also the only way to discover a new shipment.
    """
    for parcel in active_parcels:
        if parcel["status"] != ParcelStatus.OUT_FOR_DELIVERY:
            continue
        planned_from = parcel.get("planned_from")
        if not planned_from:
            return HOT_INTERVAL_MINUTES
        planned_dt = dt_util.parse_datetime(planned_from)
        if planned_dt is None:
            return HOT_INTERVAL_MINUTES
        if dt_util.as_utc(now) >= dt_util.as_utc(planned_dt) - timedelta(
            hours=HOT_LOOKAHEAD_HOURS
        ):
            return HOT_INTERVAL_MINUTES

    return MID_INTERVAL_MINUTES


def _next_update_interval(now: datetime, tier_minutes: int, entry_id: str) -> timedelta:
    """Turn a tier into the coordinator's next ``update_interval``.

    Clamp the naive next-due time forward to the next anchor whenever it
    would land inside the quiet window — including when ``now`` itself is
    already inside it (an anchor poll computing its own follow-up).
    """
    if _in_quiet_window(now):
        return _next_anchor(now) - now

    stagger = timedelta(minutes=_stagger_minutes(entry_id))
    candidate = now + timedelta(minutes=tier_minutes) + stagger
    if _in_quiet_window(candidate):
        return _next_anchor(now) - now
    return candidate - now


class SEURCoordinator(DataUpdateCoordinator[dict[str, list[dict]]]):
    """Polls the account's parcel list and publishes the canonical lists.

    ``coordinator.data`` is the active (not-yet-delivered) parcels,
    ``self.delivered`` the rest.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: SEURApiClient,
        entry: ConfigEntry,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            # Passing config_entry makes self.config_entry available on the
            # base class, which every helper below relies on.
            config_entry=entry,
            name=DOMAIN,
            # Recomputed at the end of every refresh (Section 2.2's tiering) —
            # start with the hot cadence so the very first poll, right after
            # setup, happens promptly regardless of what it finds.
            update_interval=timedelta(minutes=HOT_INTERVAL_MINUTES),
        )
        self._client = client
        self.delivered: list[dict] = []
        self.outgoing: list[dict] = []
        self.delivered_outgoing: list[dict] = []
        self._known_outgoing_state: dict[str, ParcelStatus] | None = None
        # Consecutive 429 responses, for the exponential backoff in Section 3.
        # Reset to 0 on any success.
        self._consecutive_429 = 0
        # Last tier computed by _hottest_tier_minutes — surfaced in
        # diagnostics.
        self._current_tier_minutes: int | None = None
        # barcode -> last seen ParcelStatus / (planned_from, planned_to).
        # ``None`` on the first refresh so events are suppressed for parcels
        # that already existed when the integration started — otherwise every
        # restart would flood users with "registered" notifications.
        self._known_state: dict[str, ParcelStatus] | None = None
        self._known_delivery_times: dict[str, tuple[str | None, str | None]] | None = (
            None
        )
        # Cached device id, attached to every fired event so device-trigger
        # automations can filter to this account's device.
        self._cached_device_id: str | None = None
        # Timestamp of the last successful poll (diagnostic sensor).
        self.last_success_time: datetime | None = None

    @property
    def current_tier_minutes(self) -> int | None:
        """Tier minutes computed on the last refresh (diagnostics only)."""
        return self._current_tier_minutes

    @property
    def delivered_codes(self) -> set[str]:
        """Always empty — nothing per-parcel to skip in the account model."""
        return set()

    def _device_id(self) -> str | None:
        """Resolve (and cache) this entry's device id for event payloads."""
        if self._cached_device_id is not None:
            return self._cached_device_id
        registry = dr.async_get(self.hass)
        device = next(
            iter(
                dr.async_entries_for_config_entry(registry, self.config_entry.entry_id)
            ),
            None,
        )
        if device is not None:
            self._cached_device_id = device.id
        return self._cached_device_id

    @property
    def _include_history(self) -> bool:
        """Whether the opt-in per-parcel history option is enabled."""
        return bool(
            self.config_entry.options.get(CONF_INCLUDE_HISTORY, DEFAULT_INCLUDE_HISTORY)
        )

    async def _async_update_data(self) -> list[dict]:
        """Fetch the account's parcels and split into active vs delivered.

        ``aiohttp.ClientError`` and a non-429 ``SEURApiError`` are
        deliberately not caught — ``DataUpdateCoordinator`` turns those into
        ``UpdateFailed`` with backoff on its own. An expired session needs
        special handling, because retrying it forever would never recover; a
        429 needs its own handling too, for the Section 3 backoff.
        """
        try:
            envelope = await self._client.async_get_parcels()
        except SEURAuthError as err:
            raise ConfigEntryAuthFailed("SEUR session expired") from err
        except SEURApiError as err:
            if err.status_code != 429:
                raise
            self._consecutive_429 += 1
            retry_after = err.retry_after or min(
                BACKOFF_BASE_SECONDS * 2**self._consecutive_429, BACKOFF_CAP_SECONDS
            )
            raise UpdateFailed(
                "SEUR rate-limited (429)", retry_after=retry_after
            ) from err
        self._consecutive_429 = 0

        include_history = self._include_history
        incoming = [
            parcel
            for raw in envelope["incoming"]
            if (parcel := normalize_parcel(raw, include_history=include_history))
        ]
        outgoing = [
            parcel
            for raw in envelope["outgoing"]
            if (parcel := normalize_parcel(raw, include_history=include_history))
        ]
        active = [parcel for parcel in incoming if not parcel["delivered"]]
        delivered = [parcel for parcel in incoming if parcel["delivered"]]
        outgoing_active = [parcel for parcel in outgoing if not parcel["delivered"]]
        outgoing_delivered = [parcel for parcel in outgoing if parcel["delivered"]]

        self.delivered = apply_delivered_filter(
            sort_parcels_by_ts(delivered, "delivered_at", descending=True),
            self.config_entry,
        )
        normalized_active = sort_parcels_by_ts(active, "planned_from")
        self.outgoing = sort_parcels_by_ts(outgoing_active, "planned_from")
        self.delivered_outgoing = apply_delivered_filter(
            sort_parcels_by_ts(outgoing_delivered, "delivered_at", descending=True),
            self.config_entry,
        )

        # Incoming = active + delivered, combined so the transition to
        # delivered is visible in one set.
        incoming = normalized_active + self.delivered
        self._fire_change_events(incoming)
        outgoing_all = self.outgoing + self.delivered_outgoing
        self._fire_outgoing_change_events(outgoing_all)
        self._known_state = {
            parcel["barcode"]: parcel["status"]
            for parcel in incoming
            if parcel.get("barcode")
        }
        self._known_delivery_times = {
            parcel["barcode"]: (parcel.get("planned_from"), parcel.get("planned_to"))
            for parcel in incoming
            if parcel.get("barcode")
        }
        self._known_outgoing_state = {
            parcel["barcode"]: parcel["status"]
            for parcel in outgoing_all
            if parcel.get("barcode")
        }

        self.last_success_time = datetime.now(timezone.utc)

        now = dt_util.now()
        self._current_tier_minutes = _hottest_tier_minutes(
            normalized_active + self.outgoing, now
        )
        self.update_interval = _next_update_interval(
            now, self._current_tier_minutes, self.config_entry.entry_id
        )
        return normalized_active

    def _fire_outgoing_change_events(self, parcels: list[dict]) -> None:
        """Fire outgoing status transitions, suppressing the first refresh."""
        if self._known_outgoing_state is None:
            return
        for parcel in parcels:
            barcode = parcel.get("barcode")
            if not barcode or barcode not in self._known_outgoing_state:
                continue
            old_status = self._known_outgoing_state[barcode]
            if old_status == parcel["status"]:
                continue
            suffix = (
                "outgoing_parcel_delivered"
                if parcel["status"] is ParcelStatus.DELIVERED
                else "outgoing_parcel_status_changed"
            )
            self.hass.bus.async_fire(
                f"{DOMAIN}_{suffix}",
                {
                    **parcel,
                    "device_id": self._device_id(),
                    "old_status": old_status,
                    "new_status": parcel["status"],
                },
            )

    def _fire_change_events(self, parcels: list[dict]) -> None:
        """Fire registered / status-changed / delivered / delivery-time events.

        Silent on the very first refresh — we cannot know which parcels are
        genuinely new versus already present before HA started.

        The event contract, identical across the suite:

        * every payload is the full normalised parcel plus ``device_id``;
        * the hop **to** ``delivered`` fires only ``_parcel_delivered``, never
          also ``_parcel_status_changed``;
        * a barcode first seen already-delivered fires nothing;
        * ``registered`` only fires for a new, not-yet-delivered barcode;
        * an ETA going ``value → null`` is intentionally silent — the carrier
          just lost the window, which is not worth waking someone up for.
        """
        if self._known_state is None:
            return

        known_times = self._known_delivery_times or {}
        device_id = self._device_id()

        for parcel in parcels:
            barcode = parcel.get("barcode")
            if not barcode:
                continue
            new_status = parcel["status"]
            if barcode not in self._known_state:
                if new_status != ParcelStatus.DELIVERED:
                    self.hass.bus.async_fire(
                        f"{DOMAIN}_parcel_registered",
                        {**parcel, "device_id": device_id},
                    )
                continue

            if self._known_state[barcode] != new_status:
                if new_status == ParcelStatus.DELIVERED:
                    self.hass.bus.async_fire(
                        f"{DOMAIN}_parcel_delivered",
                        {**parcel, "device_id": device_id},
                    )
                else:
                    self.hass.bus.async_fire(
                        f"{DOMAIN}_parcel_status_changed",
                        {
                            **parcel,
                            "device_id": device_id,
                            "old_status": self._known_state[barcode],
                            "new_status": new_status,
                        },
                    )

            old_from, old_to = known_times.get(barcode, (None, None))
            new_from = parcel.get("planned_from")
            new_to = parcel.get("planned_to")
            from_changed = new_from is not None and new_from != old_from
            to_changed = new_to is not None and new_to != old_to
            if from_changed or to_changed:
                self.hass.bus.async_fire(
                    f"{DOMAIN}_parcel_delivery_time_changed",
                    {
                        **parcel,
                        "device_id": device_id,
                        "old_planned_from": old_from,
                        "new_planned_from": new_from,
                        "old_planned_to": old_to,
                        "new_planned_to": new_to,
                    },
                )

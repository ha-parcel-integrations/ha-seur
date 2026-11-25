"""Pure SEUR payload normalisation helpers."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta, tzinfo
import logging
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    HISTORY_MAX_EVENTS,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)
NEW_ISSUE_URL = "https://github.com/ha-parcel-integrations/ha-seur/issues/new?template=unrecognised_status.yml"
DELIVERY_WINDOW_ISSUE_URL = "https://github.com/ha-parcel-integrations/ha-seur/issues/3"
_STATUS_MAP = {
    "SX010": ParcelStatus.REGISTERED,
    "SX001": ParcelStatus.IN_TRANSIT,
    "LI567": ParcelStatus.IN_TRANSIT,
    "LO001": ParcelStatus.IN_TRANSIT,
    "SW189": ParcelStatus.IN_TRANSIT,
    # ENTREGA EN TIENDA: announced for the pickup point, not yet collectable.
    "LI569": ParcelStatus.IN_TRANSIT,
    "LI574": ParcelStatus.AT_PICKUP_POINT,
    "LC003": ParcelStatus.OUT_FOR_DELIVERY,
    "LL003": ParcelStatus.DELIVERED,
    "LL010": ParcelStatus.DELIVERED,
}
_ZONE = ZoneInfo("Europe/Madrid")
_warned: set[str] = set()
# Compatibility name used by the scaffold's test isolation fixture.
_unmapped_statuses_logged = _warned
_payload_shapes_logged: set[str] = set()


def _warn(code: str) -> None:
    if code not in _warned:
        _warned.add(code)
        _LOGGER.warning(
            "Unrecognised SEUR status — help us map it. Open an issue "
            "and paste this line: %s\n  status=%s → reported as 'unknown'",
            NEW_ISSUE_URL,
            code,
        )


def _warn_payload_shape(kind: str, message: str, *args: Any) -> None:
    if kind not in _payload_shapes_logged:
        _payload_shapes_logged.add(kind)
        _LOGGER.warning(message, *args)


def _key_types(value: dict[str, Any]) -> str:
    return ", ".join(
        f"{key}: {type(item).__name__}" for key, item in sorted(value.items())
    )


def map_parcel_status(code: str | None) -> ParcelStatus:
    """Map a confirmed SEUR situation code conservatively."""
    if not code:
        return ParcelStatus.UNKNOWN
    result = _STATUS_MAP.get(code)
    if result is None:
        _warn(code)
        return ParcelStatus.UNKNOWN
    return result


def map_event_status(code: str | None) -> ParcelStatus | None:
    """Map a history code, preserving missing codes as null."""
    return None if not code else map_parcel_status(code)


def to_iso_timestamp(value: Any) -> str | None:
    """Keep SEUR's ISO timestamps only; numeric timestamps are not observed."""
    return value if isinstance(value, str) else None


def format_dimensions(
    length: float | None, width: float | None, height: float | None
) -> dict[str, Any] | None:
    """SEUR supplies no dimensions; retain suite helper compatibility."""
    if length is None or width is None or height is None:
        return None
    return {
        "length": length,
        "width": width,
        "height": height,
        "text": f"{round(length)} x {round(width)} x {round(height)} cm",
    }


def parse_iso(value: Any) -> datetime | None:
    """Parse an ISO timestamp, treating naive timestamps as UTC."""
    if not isinstance(value, str) or not value:
        return None
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        return None
    return result if result.tzinfo else result.replace(tzinfo=UTC)


def build_history(
    events: Any, *, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict[str, Any]]:
    """Reverse SEUR's newest-first situation history and cap it."""
    if not isinstance(events, list):
        return []
    result: list[dict[str, Any]] = []
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        code, timestamp = event.get("cod_situacion"), event.get("fecha")
        if parse_iso(timestamp) is None:
            _warn_payload_shape(
                "history_timestamp",
                "SEUR history timestamp shape is unexpected; diagnostics redact values",
            )
            continue
        result.append(
            {
                "timestamp": timestamp,
                "status": map_parcel_status(code),
                "raw_status": code,
            }
        )
    return result[-max_events:]


def _moment(value: Any, zone: tzinfo) -> tuple[datetime, bool, str] | None:
    """Return ``(aware datetime, date_only, text)`` for a carrier time, or ``None``.

    A naive time or a bare date gets ``zone``; an aware time keeps its offset
    and, when it came in as a string, its own spelling.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        moment = datetime.combine(value, time.min, tzinfo=zone)
        return moment, True, moment.isoformat()
    elif isinstance(value, str) and value:
        try:
            if len(value) == 10:
                moment = datetime.combine(
                    date.fromisoformat(value), time.min, tzinfo=zone
                )
                return moment, True, moment.isoformat()
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo:
            return parsed, False, value
    else:
        return None
    moment = parsed if parsed.tzinfo else parsed.replace(tzinfo=zone)
    return moment, False, moment.isoformat()


def _end_of_day(moment: datetime, zone: tzinfo) -> datetime:
    local = moment.astimezone(zone)
    return datetime.combine(local.date(), time(23, 59, 59), tzinfo=zone)


def planned_window(
    start: Any, end: Any, *, zone: tzinfo
) -> tuple[str | None, str | None]:
    """Return ``(planned_from, planned_to)`` in the canonical window shape.

    ``start`` and ``end`` are ISO strings, dates or datetimes, or ``None``; an
    epoch number must be converted first (see ``to_iso_timestamp``). A window
    keeps both ends. One moment with a time ends at ``23:59:59`` the same local
    day; a date covers ``00:00:00`` to ``23:59:59``; two dates run from the
    first day's start to the last day's end. Equal values are one moment,
    reversed values are swapped, and ``planned_to`` is never set without
    ``planned_from``. ``zone`` is the carrier's own zone: it dates naive times
    and bare dates and decides which day a moment ends on, so a ``Z`` moment
    still ends on the carrier's local day. When nothing is left of that day
    after the moment, there is no ``planned_to``.
    """
    first = _moment(start, zone)
    last = _moment(end, zone)
    if first is None:
        first, last = last, None
    if first is None:
        return None, None

    def closing(moment: tuple[datetime, bool, str]) -> datetime:
        return _end_of_day(moment[0], zone) if moment[1] else moment[0]

    if last is not None and closing(last) < first[0]:
        first, last = last, first
    if last is not None and closing(last) == first[0]:
        last = None

    if last is None:
        end_of_day = _end_of_day(first[0], zone)
        return first[2], end_of_day.isoformat() if end_of_day > first[0] else None
    return first[2], closing(last).isoformat() if last[1] else last[2]


_NO_WINDOW_STATUSES = frozenset(
    {ParcelStatus.AT_PICKUP_POINT, ParcelStatus.RETURNING, ParcelStatus.DELIVERED}
)


def planned_for_status(
    status: ParcelStatus, planned_from: str | None, planned_to: str | None
) -> tuple[str | None, str | None]:
    """Empty the delivery window once the parcel is at its destination or done."""
    if status in _NO_WINDOW_STATUSES:
        return None, None
    return planned_from, planned_to


def is_pickup(
    route_flag: bool | None,
    status: ParcelStatus,
    event_statuses: Iterable[ParcelStatus | None] = (),
) -> bool:
    """Whether the delivery route ends at a pickup point.

    The carrier's own route flag wins; otherwise the parcel having reached
    ``AT_PICKUP_POINT`` at any point counts, so the value survives collection.
    """
    if route_flag:
        return True
    return status is ParcelStatus.AT_PICKUP_POINT or any(
        event is ParcelStatus.AT_PICKUP_POINT for event in event_statuses
    )


def normalize_parcel(
    raw: dict[str, Any], *, include_history: bool = False
) -> dict[str, Any] | None:
    """Map one SEUR shipment into the canonical shape, retaining its raw object."""
    barcode = raw.get("clave_envio")
    if not isinstance(barcode, str) or not barcode:
        _warn_payload_shape(
            "missing_barcode",
            "SEUR shipment without a barcode was ignored; diagnostics redact values",
        )
        return None
    events = raw.get("situaciones")
    newest = (
        events[0]
        if isinstance(events, list) and events and isinstance(events[0], dict)
        else {}
    )
    code = newest.get("cod_situacion")
    status = map_parcel_status(code)
    delivered = status is ParcelStatus.DELIVERED
    delivered_at = (
        newest.get("fecha") if delivered and parse_iso(newest.get("fecha")) else None
    )
    delivery = raw.get("delivery")
    if isinstance(delivery, dict) and delivery:
        _warn_payload_shape(
            "delivery",
            "SEUR parcel carries unseen delivery details; the delivery window stays "
            "empty. Please report these field names and types to %s: %s",
            DELIVERY_WINDOW_ISSUE_URL,
            _key_types(delivery),
        )
    pickup = is_pickup(
        raw.get("tipo_entrega") == "SHOP",
        status,
        (
            map_event_status(event.get("cod_situacion"))
            for event in events or []
            if isinstance(event, dict)
        )
        if isinstance(events, list)
        else (),
    )
    planned_from, planned_to = planned_for_status(
        status, *planned_window(None, None, zone=_ZONE)
    )
    weight = raw.get("peso")
    if isinstance(weight, bool) or not isinstance(weight, (int, float)):
        weight = None
    return {
        "carrier": "SEUR",
        "barcode": barcode,
        "sender": _party_name(raw.get("remitente")),
        "receiver": _party_name(raw.get("destinatario")),
        "contents": None,
        "status": status,
        "raw_status": code,
        "delivered": delivered,
        "delivered_at": delivered_at,
        "planned_from": planned_from,
        "planned_to": planned_to,
        "pickup": pickup,
        "pickup_point": _pickup_point(raw.get("destinatario")) if pickup else None,
        "url": None,
        "weight": float(weight) if weight is not None else None,
        "dimensions": None,
        "history": build_history(events) if include_history else None,
        # ``raw`` deliberately keeps the carrier's complete original object
        # for advanced local Home Assistant automations. Diagnostics redact it
        # before it can be shared outside the user's installation.
        "raw": raw,
    }


def _pickup_point(party: Any) -> str | None:
    """Return the pickup point's name; the rest of ``destinatario`` is personal."""
    if not isinstance(party, dict):
        return None
    name = party.get("centro_seur")
    return name.strip() or None if isinstance(name, str) else None


def _party_name(party: Any) -> str | None:
    """Return SEUR's display name for one sender or recipient party."""
    if not isinstance(party, dict):
        return None
    for key in ("razon_social", "nombre_contacto"):
        value = party.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def sort_parcels_by_ts(
    parcels: list[dict], key: str, *, descending: bool = False
) -> list[dict]:
    """Sort dated parcels while retaining invalid dates at the end."""
    dated = [(parse_iso(parcel.get(key)), parcel) for parcel in parcels]
    valid = sorted(
        ((stamp, parcel) for stamp, parcel in dated if stamp is not None),
        key=lambda item: item[0],
        reverse=descending,
    )
    return [parcel for _, parcel in valid] + [
        parcel for stamp, parcel in dated if stamp is None
    ]


def apply_delivered_filter(parcels: list[dict], entry: ConfigEntry) -> list[dict]:
    """Apply the configured delivered parcel retention filter."""
    amount = int(
        entry.options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if (
        entry.options.get(CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE)
        == "parcels"
    ):
        return parcels[:amount]
    cutoff = datetime.now(UTC) - timedelta(days=amount)
    return [
        parcel
        for parcel in parcels
        if (stamp := parse_iso(parcel.get("delivered_at"))) is None or stamp >= cutoff
    ]

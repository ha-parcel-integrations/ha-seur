# SEUR Parcel Tracker

[![Release](https://img.shields.io/github/v/release/ha-parcel-integrations/ha-seur.svg)](https://github.com/ha-parcel-integrations/ha-seur/releases)
[![Downloads](https://img.shields.io/github/downloads/ha-parcel-integrations/ha-seur/total.svg)](https://github.com/ha-parcel-integrations/ha-seur/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> 💬 Questions or feedback? Join the discussion on the [Home Assistant community](https://community.home-assistant.io/t/packages-postnl-dhl-nl-dpd-and-gls-parcel-integration/112433/).

A custom Home Assistant integration that tracks parcels from your [SEUR](https://www.seur.com/) account. Sign in with the credentials you use for miSEUR and received and sent parcels are imported automatically.

Part of the [ha-parcel-integrations](https://ha-parcel-integrations.github.io/) family: it publishes the same canonical parcel format, statuses and events as the other carrier integrations, so it plugs straight into the [Parcel Aggregator](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) and cross-carrier automations.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Options](#options)
- [Removal](#removal)
- [Sensors](#sensors)
- [Parcel status reference](#parcel-status-reference)
- [Events](#events)
- [Examples](#examples)
- [Debugging](#debugging)
- [Troubleshooting](#troubleshooting)
- [Related integrations](#related-integrations)
- [Disclaimer](#disclaimer)
- [Contributing](#contributing)
- [License](#license)

## Features

- Auto-imports every parcel your SEUR account already knows about — no per-parcel setup
- Per-parcel sensor with the canonical status (`registered` / `in_transit` / `out_for_delivery` / `delivered` / …), carrier status code, weight and optional history
- Summary sensors for incoming parcels, outgoing parcels, delivered incoming parcels and delivered outgoing parcels
- Events + device triggers for incoming parcel registration/status/delivery and outgoing status/delivery changes
- Opt-in per-parcel status history
- Manual refresh button and a diagnostic last-update sensor

## Requirements

- Home Assistant 2024.12 or newer
- A SEUR account (the same one you use on the SEUR
  website or app)

## Installation

### HACS (recommended)

1. In HACS, choose the three-dot menu → **Custom repositories**.
2. Add `https://github.com/ha-parcel-integrations/ha-seur` as an **Integration**.
3. Install **SEUR** and restart Home Assistant.

### Manual

Copy `custom_components/seur` into your `config/custom_components/` folder and restart Home Assistant.

## Configuration

Add the integration via **Settings → Devices & Services → Add Integration → SEUR** and sign in with your SEUR account email and password.

Parcels are discovered automatically from the configured miSEUR account. There is no tracking-code editor or manual tracking service.

## Options

Open **Configure** on the integration entry:

| Section | Option | Default | Description |
|---|---|---|---|
| Delivered parcels | Filter by / amount | last 7 days | How long delivered parcels stay visible on the delivered sensor. |
| Parcel history | Include status history | off | Adds a `history` attribute per parcel with each status update. |

Polling isn't one of these settings: the integration polls on a dynamic,
status-driven schedule (quiet overnight window, faster when an incoming or
outgoing parcel is out for delivery) with nothing to
configure. See [CLAUDE.md](CLAUDE.md) for the details.

## Removal

Standard HA removal applies: **Settings → Devices & Services → SEUR → ⋮ → Delete**. Nothing is stored on SEUR's side.

## Sensors

| Entity | Description |
|---|---|
| `sensor.seur_incoming_parcels` | Number of active incoming parcels, full list under `parcels` |
| `sensor.seur_outgoing_parcels` | Number of active outgoing parcels, full list under `parcels` |
| `sensor.seur_outgoing_delivered_parcels` | Recently delivered outgoing parcels (see the retention option) |
| `sensor.seur_awaiting_pickup` | Parcels waiting for you at a pickup point. Stays at 0 for now: SEUR's pickup-point data hasn't been confirmed from a real parcel yet |
| `sensor.seur_parcel_<code>` | One per active incoming parcel; state is the canonical status and attributes carry the normalised parcel |
| `sensor.seur_next_delivery` | Present but empty until SEUR supplies a confirmed delivery time |
| `sensor.seur_delivered_parcels` | Recently delivered incoming packages (see the retention option) |
| `sensor.seur_last_successful_update` | Diagnostic: when SEUR was last polled successfully |

A delivered incoming parcel moves from its per-parcel sensor to the delivered summary automatically. Outgoing parcels are represented by summary sensors only.

## Parcel status reference

The `status` field is the carrier-agnostic enum shared by the whole integration family:


| Status | Meaning |
|---|---|
| `registered` | Announced / received by SEUR |
| `in_transit` | In the sorting network |
| `out_for_delivery` | With the courier today |
| `at_pickup_point` | Waiting for you at a pickup location |
| `delivered` | Delivered |
| `returning` | Going back to the sender |
| `problem` | SEUR reports an exception |
| `unknown` | Not yet scanned, or a status we have not mapped yet |

The carrier's own human-readable text is always available as `raw_status`.

## Events

The integration fires these on the event bus (also available as device triggers on the SEUR device):

| Event | When |
|---|---|
| `seur_parcel_registered` | A new parcel appears in the active list |
| `seur_parcel_status_changed` | A parcel's canonical status changes (`old_status` / `new_status` in the payload), except the final hop to delivered |
| `seur_parcel_delivered` | A parcel is delivered |
| `seur_parcel_delivery_time_changed` | The expected delivery window changes |
| `seur_outgoing_parcel_status_changed` | An outgoing parcel's canonical status changes |
| `seur_outgoing_parcel_delivered` | An outgoing parcel is delivered |

Every payload is the full normalised parcel plus the hub's `device_id`. Events are suppressed on the first refresh after start-up.

## Examples

Ready-to-paste automations and dashboard snippets live in [`examples/`](examples/).

### Community Lovelace cards

Third-party cards that work with this integration's sensors:

- [jonisnet/hki-parcels-card](https://github.com/jonisnet/hki-parcels-card)
- [klaptafel/ha-package-tracker-card](https://github.com/klaptafel/ha-package-tracker-card)

## Debugging

```yaml
logger:
  logs:
    custom_components.seur: debug
```

## Troubleshooting

- **A parcel shows `unknown`** — SEUR returned a status code that is not mapped yet. It remains visible and its carrier code is available as `raw_status`.
- **A status logs "Unrecognised SEUR status"** — please [open an issue](https://github.com/ha-parcel-integrations/ha-seur/issues/new) with the logged line so the mapping can be extended.

## Related integrations

This integration is part of [**ha-parcel-integrations**](https://ha-parcel-integrations.github.io/) — a family of
parcel-carrier integrations that all publish the same canonical parcel format,
statuses and events.

- [**Parcel Aggregator**](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) rolls every installed carrier
  up into one set of sensors.
- Browse [the organisation](https://ha-parcel-integrations.github.io/) for the current list of supported carriers.

## Disclaimer

This is an independent, community-built project. It is not affiliated with, endorsed by, sponsored by, or supported by SEUR, Home Assistant, or any other third party referenced in this project. Please don't contact SEUR for support with this integration.

All third-party trademarks, trade names, product names, logos, and other brand assets are the property of their respective owners. References to them are solely to identify the relevant carrier or service and do not imply affiliation, sponsorship, or endorsement. Nothing in this project grants or implies any licence or right to use third-party brand assets.

This integration may rely on public, unofficial, or undocumented carrier interfaces, accessed with your own account or API key where required. These may change or be withdrawn without notice and may be subject to SEUR's terms. Data is sent only to SEUR's own services or those of its group; this project operates no servers of its own. You are responsible for ensuring that your use complies with applicable law and those terms. Use is at your own risk; see the [licence](LICENSE) for warranty limitations.

This integration reads the configured user's miSEUR account inbox. It does not use the public tracking endpoint.

## Contributing

Pull requests and issues are welcome. Please open an issue before
submitting a large change.

## License

[MIT](LICENSE)

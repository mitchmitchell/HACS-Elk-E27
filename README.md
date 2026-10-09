# Elk E27 Alarm Engine Integration for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![GitHub release](https://img.shields.io/github/v/release/mitchmitchell/HACS-Elk-E27)](https://github.com/mitchmitchell/HACS-Elk-E27/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A Home Assistant custom integration for the **Elk Products E27 Alarm Engine**. It connects
directly to the panel on your local network, with no cloud service involved, and keeps Home
Assistant updated as the panel reports changes. Each area becomes an alarm control panel you
can arm and disarm. Each zone becomes a binary sensor. Panel-controlled lights, locks,
thermostats and outputs become light, lock, climate and switch entities. All communication goes
through the [`elke27`](https://github.com/mitchmitchell/elke27) Python library
([PyPI](https://pypi.org/project/elke27/)).

> **Canonical repository:** [mitchmitchell/HACS-Elk-E27](https://github.com/mitchmitchell/HACS-Elk-E27).
> [ElkProducts/HACS-Elk-E27](https://github.com/ElkProducts/HACS-Elk-E27) is a mirror with the
> same code and releases. Add only one of them to HACS, and report issues on the canonical
> repository.

---

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Actions](#actions)
- [How it works](#how-it-works)
- [Known limitations](#known-limitations)
- [Troubleshooting](#troubleshooting)
- [Removal](#removal)
- [Releases and changelog](#releases-and-changelog)
- [Relationship to Home Assistant core](#relationship-to-home-assistant-core)
- [Support and contributing](#support-and-contributing)
- [License](#license)

---

## Features

The integration creates one **device** for the panel, showing the panel's name, model, firmware
version, serial number and MAC address. All entities belong to that device. Entities are named
after the names programmed into the panel. If an item has no name, a fallback such as `Area 1`
or `Zone 12` is used.

| Platform | One entity per | What you can do |
|---|---|---|
| Alarm control panel | Area | Arm away, arm home, arm custom bypass and disarm, all with a numeric user code |
| Binary sensor | Zone (zones defined as `UNDEFINED` are skipped) | See whether the zone is open/violated, bypassed or in trouble |
| Light | Panel light | Turn on/off and set brightness |
| Lock | Panel lock | Lock and unlock |
| Climate | Thermostat | Set HVAC mode, fan mode and heat/cool setpoints |
| Switch | Output | Turn the output on/off |
| Sensor (diagnostic) | Panel (2 sensors) | Panel name, and panel connection state (`connected` / `disconnected`) |

### Alarm control panel (areas)

- **Arm away**, **arm home** (the panel's *Stay* mode) and **disarm**. Each one needs your
  numeric alarm user code, which is entered in the Home Assistant keypad. The E27 has no
  *Night* mode, so arm night is not offered.
- **Arm custom bypass** bypasses every zone *in this area* that is currently open and not
  already bypassed, using the code you enter, and then arms the area in **away** mode. If the
  panel rejects a bypass, the area is not armed and an error is shown.
  - The area then shows `armed_away` while the exit delay runs. If no entry/exit zone opens
    before the exit delay ends, the panel's auto-stay switches it to Stay and it shows
    `armed_home`. This is panel behavior.
  - Disarming an armed area clears all zone bypasses (panel behavior). Disarming an area that is already disarmed does not.
- When the panel rejects a request, the error says why, for example *invalid user code*,
  *area not ready (open or faulted zones)* or *area is in alarm; disarm to clear it first*
  (needs elke27 0.3.10 or later). The reason and panel error code are also logged as a
  warning.
- States reported: `disarmed`, `armed_home`, `armed_away` and `triggered` (alarm active).
- Extra attributes:

  | Attribute | Meaning |
  |---|---|
  | `ready` | The panel's ready flag for the area (needs elke27 0.3.10 or later) |
  | `ready_status` | The panel's ready status: `RDY_AWAY`, `RDY_STAY` or `RDY_NOT` (needs elke27 0.3.10 or later) |
  | `faulted_zone_ids` | IDs of zones in this area that are open and not bypassed |
  | `faulted_zones` | Names of those zones |

### Binary sensors (zones)

- The sensor is **on** when the zone is open/violated.
- The device class is *motion*, *window* or *door* when the panel's zone type says so.
  Otherwise it is *opening*.
- The icon follows the zone definition (burglary entry/exit, perimeter, interior, 24-hour,
  tamper, fire, CO, panic, medical, automation, power supervision, water, hi/lo temp) and
  changes when the zone opens.
- Attributes: `definition`, `bypassed`, `trouble`.
- To bypass a zone, use the [`elke27.zone_bypass`](#elke27zone_bypass) action.

### Lights

- On/off with brightness. The panel's 0–99 level is mapped to Home Assistant's brightness
  scale.
- Turning a light on without a brightness value sets it to full (level 99).
- After a change, the integration asks the panel for the light's status right away and
  again 3 seconds later, because some (Z-Wave) devices report their new state late.

### Locks

- Lock and unlock.

### Climate (thermostats)

- **HVAC modes:** Off, Heat, Cool and Heat/Cool (the panel's *Auto* mode).
- **Fan modes:** Auto, On.
- **Setpoints:** a heat (low) and cool (high) setpoint, shown as a temperature range.
- **Units and range:** °F, 40–99 °F. The panel takes whole degrees.
- **Current humidity** is shown when the thermostat reports it (a reading of 0 is treated as
  "no humidity sensor").

### Switches (outputs)

- Turn each panel output on or off.

### Diagnostic sensors

- **Panel name**: the name reported by the panel.
- **Panel ready**: `connected` while the integration has a ready session with the panel,
  otherwise `disconnected`.

---

## Requirements

- **Home Assistant 2026.1.0 or newer.**
- An **Elk E27** panel on your local network that Home Assistant can reach over TCP. Manual
  setup connects on port **2101**.
- The panel's **linking credentials**: an **access code** and a **passphrase**.
  - Your panel's installer sets these on the panel, so ask your installer for them.
  - They are used once, to link Home Assistant to the panel. They are **not** your alarm
    user code (PIN).
  - Your alarm user code is entered separately each time you arm or disarm.
- **Optional, for automatic discovery:** Home Assistant must be on the same network segment
  as the panel. Discovery uses a UDP broadcast on port **2362**. A Home Assistant container
  must use host networking for discovery to work. If discovery can't reach the panel, use
  manual setup.
- **[HACS](https://hacs.xyz/)**, if you want to install through HACS (recommended).

---

## Installation

### HACS (recommended)

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=mitchmitchell&repository=HACS-Elk-E27&category=integration)

Or add it by hand:

1. In Home Assistant, open **HACS**.
2. Open the **⋮** menu (top right) and choose **Custom repositories**.
3. Enter `https://github.com/mitchmitchell/HACS-Elk-E27`, set **Type** to **Integration**,
   and click **Add**.
4. Search HACS for **Elk E27**, open it and click **Download**.
5. **Restart Home Assistant.**

HACS will notify you when new releases come out.

### Manual

1. Download the latest release from the
   [Releases page](https://github.com/mitchmitchell/HACS-Elk-E27/releases).
2. Copy the `custom_components/elke27` folder into your Home Assistant configuration
   directory, so that you end up with `<config>/custom_components/elke27/manifest.json`.
3. **Restart Home Assistant.**

Home Assistant installs the required `elke27` library automatically the first time the
integration loads.

---

## Configuration

Configuration happens entirely in the UI. There is no YAML.

[![Open your Home Assistant instance and start setting up Elk E27.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=elke27)

1. Go to **Settings → Devices & services → Add integration** and search for **Elk E27**.
   It is listed as *Elk E27 Alarm Engine Integration*.
2. **Choose a setup method:**
   - **Discover panels**: searches your network for E27 panels.
   - **Manual setup**: enter the panel's address yourself.

#### Discover panels

- If **one** panel is found, you only need to enter the **access code** and **passphrase**.
- If **several** panels are found, pick one from the list. Each entry shows its name, model,
  MAC and IP, and panels you have already added are marked *(already configured)*. Then enter
  the access code and passphrase.
- Choose **Rescan for panels** if your panel isn't listed.
- If no panels are found, you'll see *"No panels were discovered…"*. Rescan, or go back and
  use manual setup.

#### Manual setup

| Field | Description |
|---|---|
| Host | The panel's IP address or host name |
| Access code | Linking access code from your installer (**not** a PIN) |
| Passphrase | Linking passphrase from your installer |

Manual setup always uses port 2101.

#### What happens next

The integration links to the panel, opens a session, waits up to 30 seconds for the panel to
report ready, and reads the panel information. It then creates the entry, using the panel's
name as the title or the host if the panel reports no name. Entities appear as soon as the
panel's areas, zones, lights, locks, thermostats and outputs have been read.

The access code and passphrase are **not stored**. Only the link keys the panel issues during
linking are saved.

### Options

There is no options flow. Nothing needs configuring after setup.

### Changing the panel's address

There is no reconfigure option yet. If the panel's IP address changes, run **Add
integration → Elk E27 → Manual setup** again with the new address and your linking
credentials. The integration recognises the panel by its MAC address, updates the existing
entry's address and reports *"This panel is already configured."* A DHCP reservation for the
panel avoids this.

### Re-linking (reauthentication)

If the stored link keys go missing or the panel rejects them, Home Assistant shows a
**reauthentication** notice for the integration. Open it and enter the **access code** and
**passphrase** again (*Relink panel*). The entry reloads with new link keys. Your entities and
their IDs stay the same.

---

## Actions

### `elke27.alarm_arm_automatic`

Arms one or more Elk E27 areas from an automation or script. It is designed for automatic
arming when everyone has left, driven by geofencing (`person` / `device_tracker` entities
and zones) or occupancy sensors. Your Home Assistant automation decides **when** to arm.
This action only performs the arm.

| Field | Required | Description |
|---|---|---|
| `target` | yes | One or more Elk E27 alarm control panel entities, or a device or area that contains them |
| `mode` | yes | `away` or `home` |
| `code` | yes | Numeric alarm user code |

This is meant for unattended geofence-style arming. Before arming, the integration bypasses
every open (faulted), not-yet-bypassed zone in each targeted area using your code. Zones in
other areas are never changed.

**When something fails.** Panel refusals are never retried. The failure is reported straight
away, with the area, the zone and the panel's reason. What happens to the bypasses this call
made depends on whether the failure is definite:

- **A bypass fails** (for example zone A is bypassed, then the panel refuses zone B): the
  area is **not** armed, and zone A is un-bypassed again.
- **The panel refuses the arm** (a panel error code such as *area not ready*, a missing or
  invalid user code), or the arm command **was never sent** because the panel was not
  connected: the area is not armed, and the bypasses made for it are undone.
- **The arm result is unknown** (a timeout, a dropped connection, or another error where the
  panel may have acted): the bypasses are **left in place**, because the area may be armed
  and relying on them. The error, the notification and the event say the result is unknown
  and list the bypassed zones. Check the area on the panel; if it is not armed, clear the
  bypasses with [`elke27.zone_bypass`](#elke27zone_bypass) and `bypass: false`.

The rollback sends one un-bypass per zone, in reverse order, and never retries it. This
matters because the panel only clears bypasses when an **armed** area is disarmed;
disarming an area that never armed leaves them in place. If an un-bypass fails, the error,
the notification and the event name those zones as still bypassed
(`still_bypassed_zone_ids`). Clear them with `elke27.zone_bypass` and `bypass: false`.
Zones are named with their number, for example *Perimeter (zone 16)*.

Calls for the **same area** run one at a time: a second call waits until the first has
finished bypassing, arming and any rollback, then reads fresh zone state.

In each case Home Assistant raises an error, creates a persistent notification for that
area, and fires an [`elke27_arm_automatic_failed`](#elke27_arm_automatic_failed-event)
event, so you can alert yourself. If you want to retry, do it in your automation.

**Several targets.** Each area is handled on its own, in entity ID order. A failure in one
area doesn't stop the others, and areas armed earlier in the same call **stay armed**. Each
failed area gets its own notification and event. The action reports an error if any area
failed.

The integration sends the arm request with the panel's auto-stay-cancel and exit-delay-cancel
flags set, so the panel:

- **cancels auto-stay** (arm away stays away even if no exit is detected), and
- **cancels the exit delay** (the area arms immediately).

Use the standard `alarm_control_panel.alarm_arm_away` / `alarm_arm_home` actions when you
want the panel's normal exit delay and auto-stay behaviour without automatic bypasses.

Single action call:

```yaml
action: elke27.alarm_arm_automatic
target:
  entity_id: alarm_control_panel.main_floor   # example entity ID
data:
  mode: away
  code: "1234"
```

#### Example: arm away when everyone leaves

This automation arms the area once nobody has been in the `home` zone for 5 minutes and the
area is still disarmed. `zone.home`'s state is the number of people in the zone. The code
comes from `secrets.yaml` (for example `elk_alarm_code: "1234"`), so it isn't written into
the automation. `!secret` only works in YAML files, such as `automations.yaml` or a package,
so not in the UI automation editor.

```yaml
- alias: "Arm Elk E27 when everyone leaves"
  triggers:
    - trigger: numeric_state
      entity_id: zone.home
      below: 1
      for: "00:05:00"
  conditions:
    - condition: state
      entity_id: alarm_control_panel.main_floor   # example entity ID
      state: disarmed
  actions:
    - action: elke27.alarm_arm_automatic
      target:
        entity_id: alarm_control_panel.main_floor
      data:
        mode: away
        code: !secret elk_alarm_code
  mode: single
```

The same pattern works with occupancy: trigger when your occupancy sensors have shown no
presence for a while, and use `mode: home` to arm stay instead.

#### `elke27_arm_automatic_failed` event

Fired once for each area that `elke27.alarm_arm_automatic` could not arm. The event data
includes:

| Key | Description |
|---|---|
| `entity_id` | Alarm control panel entity that was being armed |
| `area_id` | Elk area number |
| `stage` | `bypass` (a zone bypass failed, so the area was not armed), `arm` (the bypasses succeeded but the panel refused the arm, or it was never sent) or `arm_uncertain` (the arm result is unknown, for example a timeout) |
| `outcome` | `not_armed` (the area is known not to be armed; bypasses were rolled back) or `unknown` (check the panel; bypasses were left in place) |
| `zone_id` | Zone that could not be bypassed (`null` when `stage` is `arm` or `arm_uncertain`) |
| `reason` | Panel or integration reason (the alarm code is never included) |
| `bypassed_zone_ids` | Zones in this area that this call bypassed before the failure |
| `rolled_back_zone_ids` | Of those, the zones whose bypass was undone after the failure |
| `still_bypassed_zone_ids` | Zones still bypassed: un-bypasses that failed, or, when `outcome` is `unknown`, every zone bypassed by the call. If the area is not armed, clear them with `elke27.zone_bypass` and `bypass: false` |

Example automation trigger:

```yaml
triggers:
  - trigger: event
    event_type: elke27_arm_automatic_failed
actions:
  - action: notify.mobile_app_your_phone
    data:
      title: "Elk E27 did not arm"
      message: >-
        Area {{ trigger.event.data.area_id }} not armed
        ({{ trigger.event.data.stage }}): {{ trigger.event.data.reason }}.
        Bypassed: {{ trigger.event.data.bypassed_zone_ids }}
```

### `elke27.zone_bypass`

Bypasses one or more zones so they are ignored while the area is armed, or removes the
bypass.

| Field | Required | Description |
|---|---|---|
| `target` | yes | One or more Elk E27 zone binary sensors |
| `code` | yes | Numeric alarm user code |
| `bypass` | no | `true` (default) to bypass, `false` to remove the bypass |

```yaml
action: elke27.zone_bypass
target:
  entity_id: binary_sensor.garage_door   # example entity ID
data:
  code: !secret elk_alarm_code
  bypass: true
```

The zone's `bypassed` attribute shows the result. If the panel refuses (for example the
zone is not bypassable, or the code is wrong), the action fails with the panel's reason.
Disarming an armed area clears all bypasses (panel behavior); disarming an area that is already disarmed does not.

The standard Home Assistant actions for alarm panels (`alarm_control_panel.*`), lights,
locks, climate and switches also work with this integration's entities.

---

## How it works

- **Local push:** the integration holds a persistent, encrypted session with the panel and
  updates entities when the panel reports changes. It doesn't poll on a schedule.
- **Linking identity:** Home Assistant identifies itself to the panel with a client serial
  number. The serial is taken from the MAC address of the network interface Home Assistant
  uses to reach the panel, or randomly generated if that MAC can't be found. It is stored in
  the config entry, so it stays the same across restarts.
- **Reconnects:** if the connection drops, entities become unavailable and the integration
  keeps reconnecting with a growing delay of up to 5 minutes between attempts. When the
  connection comes back, it refreshes everything from the panel. If the panel rejects the link
  keys, it stops retrying. Reload the integration (or restart Home Assistant) to get the
  re-link prompt.
- **New items:** areas, zones, lights, locks, thermostats and outputs that show up after setup
  get entities automatically.

---

## Known limitations

- **Arming**
  - A numeric user code is always required to arm or disarm.
  - **Arm vacation** is not offered. **Arm night** is not offered because the E27 has no
    Night mode.
  - **Custom bypass** always arms in away mode after bypassing the open zones. The panel's
    auto-stay may then switch it to Stay (`armed_home`) when no exit is detected.
  - Disarming an armed area clears all zone bypasses (panel behavior).
- **No code prompt for other devices:** lights, locks, outputs and thermostats are controlled
  without a user code. If your panel demands a code for one of those commands, the action
  fails with *"PIN required to perform this action."*
- **Thermostats**
  - °F only, whole degrees, 40–99 °F.
  - Heat/cool setpoints are always shown as a range, whatever the mode.
  - HVAC *action* (heating/cooling/off) is inferred from the selected mode. It does not
    show whether the equipment is actually running.
  - Only Auto and On fan modes are supported.
- **Setup**
  - Manual setup always uses port 2101.
  - There is no options or reconfigure flow (see
    [Changing the panel's address](#changing-the-panels-address)).
  - Discovery only works on the same network segment as the panel.
- **Stale entities:** if you delete an area, zone or other item in the panel, its entity is
  not removed automatically. Delete it yourself in Home Assistant.
- **`UNDEFINED` zones:** zones defined as `UNDEFINED` in the panel get no entity.

---

## Troubleshooting

### Setup errors

| Message | What to check |
|---|---|
| *Unable to connect to the panel…* | The host/IP is correct, the panel is online, and Home Assistant can reach it on TCP port 2101 (or the port the panel advertised during discovery). |
| *Unable to authenticate with the panel…* | The access code and passphrase are correct. These are the linking credentials set by your installer, not your alarm code. Confirm them with your installer if in doubt. |
| *The panel requires linking…* | Enter the access code and passphrase again. |
| *No panels were discovered…* | The panel is online and on the same network segment, and UDP broadcast on port 2362 isn't blocked. Otherwise use **Manual setup**. |
| *This panel is already configured.* | The panel (matched by MAC address) already has an entry. If you changed its IP address, the existing entry has been updated. |

### "PIN required to perform this action." / "Code must be numeric."

Enter your numeric alarm user code in the keypad dialog, or in the `code` field of an action.

### Thermostat shows a strange setpoint (for example 680°)

Versions before **0.1.5** sent thermostat setpoints to the panel multiplied by 10, so 68 °F
was stored as 680. Version 0.1.5 sends whole degrees. It also shows any oversized stored value
correctly (680 is shown as 68.0).

To fix it, update to 0.1.5 or newer and change the setpoint once. The panel then stores the
correct value.

### Entities are unavailable

Check the **Panel ready** diagnostic sensor. If it says `disconnected`, the integration has
lost its session with the panel and is reconnecting. Make sure the panel is powered and
reachable on the network.

### Debug logging

To capture logs from both the integration and the library, add this to `configuration.yaml`
and restart:

```yaml
logger:
  default: warning
  logs:
    custom_components.elke27: debug
    elke27_lib: debug
```

The **Enable debug logging** button on the integration page only covers
`custom_components.elke27`. Use the YAML above if you also need the library's logs, which is
usually the case for connection problems.

> **Before sharing logs, check them for alarm user codes, IP addresses and other sensitive
> information.**

### Diagnostics

Go to **Settings → Devices & services → Elk E27**, open the **⋮** menu on the entry and choose
**Download diagnostics**.

- The download contains the panel snapshot, with sensitive values redacted by the library.
- It also contains the panel host/port and the integration's client serial number.

Attach it to bug reports.

---

## Removal

1. Go to **Settings → Devices & services → Elk E27**, open the **⋮** menu on the entry and
   choose **Delete**.
2. If you installed through HACS, open **HACS**, find **Elk E27**, and choose **Remove**.
   Then restart Home Assistant.
3. If you installed manually, delete `<config>/custom_components/elke27` and restart.

---

## Releases and changelog

Release notes for each version are on the
[Releases page](https://github.com/mitchmitchell/HACS-Elk-E27/releases). Library changes
are tracked in the [`elke27` repository](https://github.com/mitchmitchell/elke27).

---

## Relationship to Home Assistant core

A smaller, alarm-only version of this integration has been proposed for Home Assistant core
in [home-assistant/core#170241](https://github.com/home-assistant/core/pull/170241). It has not
been merged yet. This HACS integration is the full-featured version, with lights, locks,
thermostats and outputs, and it will continue to be maintained.

Both use the domain `elke27`. If the core version ships, having this custom integration
installed will override the built-in one.

---

## Support and contributing

- **Bugs and feature requests:**
  [open an issue](https://github.com/mitchmitchell/HACS-Elk-E27/issues). Include your
  Home Assistant version, the integration version, debug logs and a diagnostics file.
- **Library:** [mitchmitchell/elke27](https://github.com/mitchmitchell/elke27).
- **Contributing:** see [CONTRIBUTING.md](CONTRIBUTING.md).
  - The repo includes a VS Code dev container.
  - `scripts/setup` installs the development requirements.
  - `scripts/develop` starts a local Home Assistant with the integration loaded.
  - `scripts/lint` runs Ruff.

This is a community integration. It is not an official Home Assistant integration.

---

## License

MIT. See [LICENSE](LICENSE).

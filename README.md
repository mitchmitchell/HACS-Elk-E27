# Elk E27 Alarm Engine Integration for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![GitHub release](https://img.shields.io/github/v/release/mitchmitchell/HACS-Elk-E27)](https://github.com/mitchmitchell/HACS-Elk-E27/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

The **Elk E27 Alarm Engine Integration** is a Home Assistant custom integration for the **Elk Products E27 Alarm Engine**. It connects
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

## Quick start

1. Install it as a HACS **custom repository** (it is not in the default HACS store): in HACS,
   open the **⋮** menu → **Custom repositories**, add
   `https://github.com/mitchmitchell/HACS-Elk-E27` with type **Integration**, click **Add**,
   then search HACS for **Elk E27 Alarm Engine Integration**, download it and restart Home Assistant.
   Details under [Installation](#installation).
2. Get the panel's **access code** and **passphrase** from your installer. These link Home
   Assistant to the panel. They are not your alarm code.
3. Go to **Settings → Devices & services → Add integration → Elk E27 Alarm Engine Integration**, choose
   **Discover panels** (or **Manual setup**), and enter the access code and passphrase.
4. Add an **Alarm panel** card for an area and arm or disarm it with your numeric alarm user
   code.

---

## Contents

- [Quick start](#quick-start)
- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Actions](#actions)
- [Examples](#examples)
- [How it works](#how-it-works)
- [Known limitations](#known-limitations)
- [Known issues](#known-issues)
- [Retries and errors](#retries-and-errors)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)
- [Glossary](#glossary)
- [Upgrading](#upgrading)
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
| Binary sensor | Zone (zones defined as `UNDEFINED` at setup are skipped) | See whether the zone is open/violated, bypassed or in trouble; bypass it with [`elke27.zone_bypass`](#elke27zone_bypass) |
| Light | Panel light | Turn on/off and set brightness |
| Lock | Panel lock | Lock and unlock |
| Climate | Thermostat | Set HVAC mode, fan mode and heat/cool setpoints; see current temperature and humidity |
| Switch | Output | Turn the output on/off |
| Sensor (diagnostic) | Panel (2 sensors) | Panel name, and panel connection state (`connected` / `disconnected`) |

<!-- TODO: screenshots (device page, alarm keypad, zone attributes) in docs/images/ -->

### Alarm control panel (areas)

- **Arm away**, **arm home** (the panel's *Stay* mode) and **disarm**. Each one needs your
  numeric alarm user code, which is entered in the Home Assistant keypad.
- The E27 has no *Night* or *Vacation* mode, so arm night and arm vacation are not offered.
- **Arm away may end up as armed home.** If no entry/exit zone opens before the exit delay
  ends, the panel's *auto-stay* switches the area to Stay and it shows `armed_home`. This is
  panel behavior. Check for both `armed_away` and `armed_home` in automations.
- **Arm custom bypass** bypasses only the zones *in this area* that are open (faulted) and not
  already bypassed, using the code you enter, then arms the area in **away** mode.
  - If the panel doesn't acknowledge a bypass, the area is **not** armed and an error is shown.
  - Like arm away, the area may then end up `armed_home` through auto-stay.
- **Disarming an armed area clears all zone bypasses** (panel behavior). Disarming an area that
  is already disarmed does not.
- When the panel rejects a request, the error shows the reason and the panel error code, for
  example *invalid parameter (check the user code) (error 11004)*. See
  [Panel error messages](#panel-error-messages). The reason and code are also logged as a
  warning.
- States reported: `disarmed`, `armed_home`, `armed_away` and `triggered` (alarm active).
  There is no `arming` or `pending` state: the area shows its armed state as soon as the panel
  accepts the request, including during the exit delay.
- Extra attributes:

  | Attribute | Meaning |
  |---|---|
  | `ready` | `true` when the area is ready to arm |
  | `ready_status` | The panel's ready status: `RDY_AWAY`, `RDY_STAY` or `RDY_NOT` |
  | `faulted_zone_ids` | IDs of zones in this area that are open and not bypassed |
  | `faulted_zones` | Names of those zones |

#### What each arming method does

| Method | Panel mode | Bypasses open zones? | Exit delay / auto-stay | Possible end state |
|---|---|---|---|---|
| Arm away | Away | No (open zones: error 11015) | Normal | `armed_away`, or `armed_home` via auto-stay |
| Arm home | Stay | No (open zones: error 11015) | Normal | `armed_home` |
| Arm custom bypass | Away | Yes, open zones in this area only | Normal | `armed_away`, or `armed_home` via auto-stay |
| `elke27.alarm_arm_automatic` | Away or Stay | Yes, open zones in this area only (0.1.7+) | Both cancelled | `armed_away` / `armed_home` |

#### User codes

- Codes must be numeric, using the digits 0–9 only (other numeral characters are rejected
  before anything is sent).
- An all-zero code (such as `0000`) is rejected.
- Leading zeros are dropped: `0123` is sent to the panel as `123`.

### Binary sensors (zones)

- The sensor is **on** when the zone is open/violated.
- The device class is *motion*, *window* or *door* when the panel's zone type says so.
  Otherwise it is *opening*. Fire, CO and water zones also show as *opening*, not as
  smoke, CO or moisture sensors (see [Known issues](#known-issues)).
- The icon follows the zone definition (burglary entry/exit, perimeter, interior, 24-hour,
  tamper, fire, CO, panic, medical, automation, power supervision, water, hi/lo temp) and
  changes when the zone opens.
- Hi/lo temperature (HILO TEMP) zones are on/off binary sensors. They don't report a
  temperature.
- Attributes: `definition`, `bypassed`, `trouble`.
- To bypass a zone, use the [`elke27.zone_bypass`](#elke27zone_bypass) action.

### Lights

- On/off with brightness. The panel's 0–99 level is mapped to Home Assistant's brightness
  scale.
- Turning a light on without a brightness value sets it to full (level 99).
- After a change, the integration asks the panel for the light's status right away and
  again about 3 seconds later, because some (Z-Wave) dimmers report their new state late.
  A short delay before the state updates is normal.

### Locks

- Lock and unlock. The state updates when the panel reports the change.

### Climate (thermostats)

- **HVAC modes:** Off, Heat, Cool and Heat/Cool (the panel's *Auto* mode).
- **Fan modes:** Auto, On.
- **Setpoints:** a heat (low) and cool (high) setpoint, shown as a temperature range.
- **Units and range:** the thermostat works in °F, 40–99 °F, whole degrees. On a Home Assistant
  system set to °C, Home Assistant converts temperatures for display. Setpoints are sent to
  the panel as whole °F, so a °C setpoint is rounded and may shift slightly.
- **Current humidity** is shown when the thermostat reports it (a reading of 0 is treated as
  "no humidity sensor").

### Switches (outputs)

- Turn each panel output on or off. The state updates when the panel reports the change.

### Diagnostic sensors

- **Panel name**: the name reported by the panel.
- **Panel ready**: `connected` while the integration has a ready session with the panel,
  otherwise `disconnected`.

---

## Requirements

- **Home Assistant 2026.1.0 or newer.**
- An **Elk E27** panel on your local network that Home Assistant can reach over TCP. Manual
  setup connects on port **2101**.
  Tested on an E27 with firmware **0.0.6.4** (hardware 0.0.1.4, bootloader 0.0.2.24). Other
  versions may work but haven't been tested.
- The panel's **linking credentials**: an **access code** and a **passphrase**.
  - Your panel's installer sets these on the panel, so ask your installer for them.
  - They are used once, to link Home Assistant to the panel. They are **not** your alarm
    user code (PIN).
  - Your alarm user code is entered separately each time you arm or disarm.
- **Optional, for automatic discovery:** Home Assistant must be on the same network segment
  as the panel. Discovery uses a UDP broadcast on port **2362**. A Home Assistant container
  must use host networking for discovery to work. If discovery can't reach the panel, use
  manual setup.
- **[HACS](https://hacs.xyz/)**, if you want to install through HACS (recommended). The
  integration is added to HACS as a custom repository.

### Compatibility

| Integration | `elke27` library | Home Assistant tested | Panel firmware |
|---|---|---|---|
| 0.1.7 | 0.3.11 | minimum 2026.1.0 | Tested: firmware 0.0.6.4, hardware 0.0.1.4, bootloader 0.0.2.24 |
| 0.1.6 | 0.3.10 | 2026.1.3, 2026.10.0 (minimum 2026.1.0) | Tested: firmware 0.0.6.4, hardware 0.0.1.4, bootloader 0.0.2.24 |

---

## Installation

### HACS (recommended)

This integration is **not in the default HACS store**. You add it to HACS as a
**custom repository**:

1. In Home Assistant, open **HACS**.
2. Open the **⋮** (three-dot) menu in the top right and choose **Custom repositories**.
3. Enter `https://github.com/mitchmitchell/HACS-Elk-E27`, set **Type** to **Integration**,
   and click **Add**.
4. Search HACS for **Elk E27 Alarm Engine Integration**, open it and click **Download**.
5. **Restart Home Assistant.**

The button below opens this repository in HACS and offers to add it as a custom repository
for you:

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=mitchmitchell&repository=HACS-Elk-E27&category=integration)

Once added, HACS notifies you when new releases come out.

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

[![Open your Home Assistant instance and start setting up the Elk E27 Alarm Engine Integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=elke27)

1. Go to **Settings → Devices & services → Add integration** and search for **Elk E27 Alarm Engine Integration**.
2. **Choose a setup method:**
   - **Discover panels**: searches your network for E27 panels.
   - **Manual setup**: enter the panel's address yourself.

<!-- TODO: screenshots of the setup method choice and the credentials form. -->

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

There is no reconfigure option, and the integration doesn't follow a panel to a new IP address
on its own: reconnects use the stored address. **Give the panel a DHCP reservation** so its
address never changes.

If the address does change:

1. Go to **Settings → Devices & services → Add integration → Elk E27 Alarm Engine
   Integration → Manual setup**.
2. Enter the **new** host, plus the access code and passphrase.
3. The panel is recognized by its MAC address. The existing entry's address is updated in
   place, and setup ends with *"This panel is already configured."*

Nothing needs to be deleted, and your entities are kept.

### Re-linking (reauthentication)

If the stored link keys are missing or rejected when the integration **loads**, Home Assistant
shows a **reauthentication** notice. Open it and enter the **access code** and **passphrase**
again (*Relink panel*). The entry reloads with new link keys. Your entities and their IDs stay
the same. Re-linking only refreshes the link keys for the stored address; to change the
address, see [Changing the panel's address](#changing-the-panels-address).

If the keys are rejected while **reconnecting**, no notice appears: the integration stops
retrying and entities stay unavailable. **Reload the integration** (or restart Home
Assistant) to get the reauthentication notice.

---

## Actions

The standard Home Assistant actions for alarm panels (`alarm_control_panel.*`), lights,
locks, climate and switches work with this integration's entities. The integration also adds
two actions. Keep user codes in `secrets.yaml` rather than writing them into automations.

### `elke27.alarm_arm_automatic`

> **Changed in 0.1.7.** In 0.1.6 this action did not bypass zones, and an open zone made the arm
> fail with *area not ready (open or faulted zones) (error 11015)*.

Arms one or more Elk E27 areas from an automation or script. It is designed for unattended
arming when everyone has left, driven by geofencing (`person` / `device_tracker` entities
and zones) or occupancy sensors. Your Home Assistant automation decides **when** to arm.

| Field | Required | Description |
|---|---|---|
| `target` | yes | One or more Elk E27 alarm control panel entities, or a device or area that contains them |
| `mode` | yes | `away` or `home` |
| `code` | yes | Numeric alarm user code |

What it does, for each targeted area:

1. **Bypasses** every open (faulted), not-yet-bypassed zone **in that area** using your code.
   Zones in other areas are never changed.
2. **Arms immediately**, with the panel's exit delay and auto-stay both cancelled, so an away
   arm shows `armed_away` within about a second and stays away.

Disarming an **armed** area clears those bypasses (panel behavior).

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
finished bypassing, arming and any rollback. Before acting, each call asks the panel for
the area's arm state and the zones' bypass state and uses the panel's answers directly (one
request each, never retried; if one fails, that part uses the integration's latest copy). If the area is **already armed** (in any
mode), the call does nothing and succeeds: no bypass, no arm, no rollback. Zones that are
already bypassed are skipped, and only zones bypassed by this call are ever rolled back. If
the panel refuses a bypass or the arm because the area is already armed (error 11028), the
area status is read again: if it is armed, the call succeeds as a no-op (logged, no error,
notification or event); otherwise the result is reported as unknown. Nothing is rolled back
in either case. Arm away, arm home, arm custom bypass and `elke27.zone_bypass` (for zones with a
known area) share the same per-area lock, so a rollback can't race a manual change.

**A disarm always wins.** Disarming an area does not wait for the lock. The code is checked
first; a missing or non-numeric code is rejected and changes nothing. While the disarm is
being sent, any `elke27.alarm_arm_automatic` call for that area (running or still queued)
**pauses before its next command**, so it cannot send a bypass or an arm that lands after the
disarm. If the panel **accepts** the disarm, the paused call is cancelled and sends nothing
more: no further bypasses, no arm and no rollback. It reports `stage: cancelled`
(`outcome: not_armed`) with the zones it had bypassed, or was bypassing. If the panel
**refuses** the disarm (for example a wrong code), nothing is cancelled and automatic arming
carries on. A command the call had already sent before the disarm (a bypass, or the arm
itself) still reaches the panel first, so the disarm comes after it and the area ends
disarmed. Disarming an **armed** area clears its bypasses on the panel; if the area never
armed, the zones listed may still be bypassed, so clear any that are with
`elke27.zone_bypass` and `bypass: false`. Nothing is retried.

In each case Home Assistant raises an error, creates a persistent notification for that
area, and fires an [`elke27_arm_automatic_failed`](#elke27_arm_automatic_failed-event)
event, so you can alert yourself. If you want to retry, do it in your automation.

**Several targets.** Each area is handled on its own, in entity ID order. A failure in one
area doesn't stop the others, and areas armed earlier in the same call **stay armed**. Each
failed area gets its own notification and event. The action reports an error if any area
failed.

Use the standard `alarm_control_panel.alarm_arm_away` / `alarm_arm_home` actions when you
want the panel's normal exit delay and auto-stay behavior without automatic bypasses.

```yaml
action: elke27.alarm_arm_automatic
target:
  entity_id: alarm_control_panel.main_floor   # example entity ID
data:
  mode: away
  code: !secret elk_alarm_code
```

#### `elke27_arm_automatic_failed` event

Fired once for each area that `elke27.alarm_arm_automatic` could not arm (0.1.7+). The event
data includes:

| Key | Description |
|---|---|
| `entity_id` | Alarm control panel entity that was being armed |
| `area_id` | Elk area number |
| `stage` | `bypass` (a zone bypass failed, so the area was not armed), `arm` (the bypasses succeeded but the panel refused the arm, or it was never sent) or `arm_uncertain` (the arm result is unknown, for example a timeout) or `cancelled` (a disarm of the area cancelled the call; nothing more was sent) |
| `outcome` | `not_armed` (the area is known not to be armed; bypasses were rolled back, except when `stage` is `cancelled`) or `unknown` (check the panel; bypasses were left in place) |
| `zone_id` | Zone that could not be bypassed (`null` when `stage` is `arm`, `arm_uncertain` or `cancelled`) |
| `reason` | Panel or integration reason (the alarm code is never included) |
| `bypassed_zone_ids` | Zones in this area that this call bypassed before the failure |
| `rolled_back_zone_ids` | Of those, the zones whose bypass was undone after the failure |
| `still_bypassed_zone_ids` | Zones still bypassed: un-bypasses that failed, or, when `outcome` is `unknown`, every zone bypassed by the call. If the area is not armed, clear them with `elke27.zone_bypass` and `bypass: false` |

See [Retry automatic arming after a failure](#retry-automatic-arming-after-a-failure) for an
example.

### `elke27.zone_bypass`

Bypasses one or more zones so they are ignored while the area is armed, or removes the bypass.

| Field | Required | Description |
|---|---|---|
| `target` | yes | One or more Elk E27 zone binary sensors |
| `code` | yes | Numeric alarm user code |
| `bypass` | no | `true` (default) to bypass, `false` to remove the bypass |

```yaml
action: elke27.zone_bypass
target:
  entity_id: binary_sensor.back_door   # example entity ID
data:
  code: !secret elk_alarm_code
  bypass: true
```

The zone's `bypassed` attribute shows the result. If the panel refuses, the action fails with
the panel's reason, for example *zone cannot be bypassed (error 11023)*.

With several zones, they're bypassed one at a time. If the panel refuses one, or the
connection drops, the action stops with an error. Zones it already bypassed in that call
**stay bypassed**: there's no rollback, unlike `elke27.alarm_arm_automatic`. Check the zones'
`bypassed` attribute and use `bypass: false` if needed.

Disarming an armed area clears all bypasses (panel behavior); disarming an area that is
already disarmed does not.

---

## Examples

`!secret` only works in YAML files (such as `automations.yaml` or a package), not in the UI
automation editor. Store the code in `secrets.yaml`, for example `elk_alarm_code: "1234"`.

### Arm away when everyone leaves

Arms once nobody has been in the `home` zone for 5 minutes and the area is still disarmed.
`elke27.alarm_arm_automatic` bypasses any open zones in that area first (0.1.7+), so there's
nothing else to check.

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

The same pattern works with occupancy sensors: trigger when they have shown no presence for a
while, and use `mode: home` to arm stay instead.

### Retry automatic arming after a failure

Notifies you when automatic arming fails, waits up to 10
minutes for the area to become ready, and tries once more.

```yaml
- alias: "Retry Elk E27 automatic arm after a failure"
  triggers:
    - trigger: event
      event_type: elke27_arm_automatic_failed
  actions:
    - action: notify.notify
      data:
        title: "Elk E27 did not arm"
        message: >-
          Area {{ trigger.event.data.area_id }} not armed
          ({{ trigger.event.data.stage }}): {{ trigger.event.data.reason }}.
          Still bypassed: {{ trigger.event.data.still_bypassed_zone_ids }}
    - wait_template: "{{ state_attr(trigger.event.data.entity_id, 'ready') == true }}"
      timeout: "00:10:00"
      continue_on_timeout: false
    - action: elke27.alarm_arm_automatic
      target:
        entity_id: "{{ trigger.event.data.entity_id }}"
      data:
        mode: away
        code: !secret elk_alarm_code
  mode: single
```

`mode: single` and a single retry keep it from looping if the panel keeps refusing.

### Alert when the alarm is triggered

```yaml
- alias: "Elk E27 alarm triggered"
  triggers:
    - trigger: state
      entity_id: alarm_control_panel.main_floor
      to: triggered
  actions:
    - action: notify.notify
      data:
        message: "ALARM: {{ state_attr('alarm_control_panel.main_floor', 'friendly_name') }} triggered"
```

### Treat "armed" as away or home

Because auto-stay can turn arm away into armed home, check for both:

```yaml
conditions:
  - condition: state
    entity_id: alarm_control_panel.main_floor
    state:
      - armed_away
      - armed_home
```

---

## How it works

- **Local push:** the integration holds a persistent, encrypted session with the panel and
  updates entities when the panel reports changes. It doesn't poll on a schedule.
- **Linking identity:** Home Assistant identifies itself to the panel with a client serial
  number. The serial is taken from the MAC address of the network interface Home Assistant
  uses to reach the panel, or randomly generated if that MAC can't be found. It is stored in
  the config entry, so it stays the same across restarts.
- **Reconnects:** if the connection drops, entities become unavailable and the integration
  keeps reconnecting with a delay that starts at 2 seconds and doubles up to 5 minutes between
  attempts, with no limit on attempts. When the
  connection comes back, it refreshes everything from the panel. If the panel rejects the link
  keys, it stops retrying without showing a notice. Reload the integration (or restart Home
  Assistant) to get the re-link prompt.
- **Detecting a dropped connection:** the panel is probed after 30 seconds without traffic,
  and a probe or command that gets no answer triggers another probe with a 5-second wait, so a
  dead connection is usually noticed within about 35 seconds. *"Panel connection lost"* is
  logged once when it happens. Until it's noticed, commands fail with *"The panel did not
  respond in time; the command may not have been applied."* This disconnect handling may
  still change in later releases.
- **New items:** areas, zones, lights, locks, thermostats and outputs that show up after setup
  get entities automatically.

---

## Known limitations

These are by design or limits of the panel.

- **Arming**
  - A numeric user code is always required to arm or disarm. All-zero codes are rejected and
    leading zeros are dropped.
  - **Arm night** and **arm vacation** are not offered. The E27 has no Night mode.
  - Arm away and custom bypass may end as `armed_home` through the panel's auto-stay.
  - **Custom bypass** always arms in away mode, and only bypasses open zones in that area.
  - Disarming an armed area clears all zone bypasses (panel behavior).
  - There is no `arming`/`pending` state during the exit delay.
- **No code prompt for other devices:** lights, locks, outputs and thermostats are controlled
  without a user code. If your panel demands a code for one of those commands, the action
  fails with *"PIN required to perform this action."*
- **Thermostats**
  - Native °F, whole degrees, 40–99 °F. On °C systems Home Assistant converts for display, and
    setpoints are rounded to whole °F.
  - Heat/cool setpoints are always shown as a range, whatever the mode.
  - HVAC *action* (heating/cooling/off) is inferred from the selected mode. It does not
    show whether the equipment is actually running.
  - Only Auto and On fan modes are supported.
- **Garage doors:** garage door (barrier) support is a planned future feature.
- **Setup**
  - Manual setup always uses port 2101.
  - There is no options or reconfigure flow (see
    [Changing the panel's address](#changing-the-panels-address)).
  - Discovery only works on the same network segment as the panel.

---

## Known issues

These are problems that are tracked for a fix.
<!-- TODO: link each item to its GitHub issue number. -->

- **Stale zone entities:** a zone you delete in the panel, or change to `UNDEFINED`, keeps its
  entity. Delete it yourself in Home Assistant. (`UNDEFINED` zones are only skipped at first
  setup.)
- **Fire, CO and water zones show as generic openings**, not smoke, CO or moisture sensors.
- **Address changes:** after the panel's IP address changes, reconnects keep using the old
  address. Use a DHCP reservation, or run Manual setup again with the new address (see
  [Changing the panel's address](#changing-the-panels-address)).
- **No reauthentication prompt on reconnect:** if the link keys are rejected while
  reconnecting, entities stay unavailable with no notice. Reload the integration.
- **Occasional dropped connection during thermostat traffic.** The integration reconnects
  by itself. Entities may be briefly unavailable.

---

## Retries and errors

The integration handles connection problems and panel refusals differently.

**Connection drops are retried automatically.** If the connection to the panel drops, the
integration reconnects with a growing delay: it starts at 2 seconds and doubles after each
failed attempt, up to 5 minutes (300 seconds) between attempts. There is no limit on the
number of attempts. It only stops if the panel requires re-linking (see
[Re-linking](#re-linking-reauthentication)). Entities are unavailable while it reconnects.

**Single commands are not retried.** An arm, disarm, bypass or device command is sent once.
If the panel doesn't reply within 5 seconds, the command fails with *"The panel did not respond
in time; the command may not have been applied."* If the connection drops while it's being
sent, it fails with *"Lost connection to the panel; …"*. Check the device's state before
trying again.

**Panel refusals are never retried.** When the panel refuses a request, for example
*not authorized*, *zone cannot be bypassed* or *area not ready*, the integration reports it
immediately as a Home Assistant error with the reason and error code. See
[Panel error messages](#panel-error-messages).

If you want a failed command to be retried, do it in your automation or script. For
example, wait until the area is `ready` and call the action again.

**Rollback runs once.** When `elke27.alarm_arm_automatic` undoes its bypasses, each un-bypass
is sent once and never retried.

A failed `elke27.alarm_arm_automatic` also creates a persistent notification and fires the
[`elke27_arm_automatic_failed`](#elke27_arm_automatic_failed-event) event, which you can use
to retry (see [the example](#retry-automatic-arming-after-a-failure)).

---

## Troubleshooting

### Setup errors

| Message | What to check |
|---|---|
| *Unable to connect to the panel…* | The host/IP is correct, the panel is online, and Home Assistant can reach it on TCP port 2101 (or the port the panel advertised during discovery). |
| *Unable to authenticate with the panel…* | The access code and passphrase are correct. These are the linking credentials set by your installer, not your alarm code. Confirm them with your installer if in doubt. |
| *The panel requires linking…* | Enter the access code and passphrase again. |
| *No panels were discovered…* | The panel is online and on the same network segment, and UDP broadcast on port 2362 isn't blocked. Otherwise use **Manual setup**. |
| *This panel is already configured.* | The panel (matched by MAC address) already has an entry. |

### Panel error messages

When the panel rejects an arm, disarm or bypass, the error shows the panel's own reason and
error code (in 0.1.7, including 11004 and 11008). Codes not listed here are shown as a number.

| Code | Message | What to do |
|---|---|---|
| 11004 | invalid parameter (check the user code) | Check the user code. It must be numeric and not all zeros; leading zeros are dropped. |
| 11008 | not authorized | The user code isn't allowed to do this in that area. Check the user's permissions in the panel. |
| 11015 | area not ready (open or faulted zones) | Close the open zones (see the area's `faulted_zones` attribute), bypass them, or use arm custom bypass. |
| 11023 | zone cannot be bypassed | The zone's panel programming doesn't allow bypass. |
| 11028 | area already armed | `elke27.alarm_arm_automatic` treats an already-armed area as success. |
| 11027 | area is in alarm | Disarm the area to clear the alarm first. |
| 11037 | invalid user code | The code isn't a valid user code on the panel. |

### "PIN required to perform this action." / "Code must be numeric."

Enter your numeric alarm user code in the keypad dialog, or in the `code` field of an action.

### Thermostat shows a strange setpoint (for example 680°)

Versions before **0.1.5** sent thermostat setpoints to the panel multiplied by 10, so 68 °F
was stored as 680. Version 0.1.5 and later send whole degrees, and show any oversized stored
value correctly (680 is shown as 68.0).

To fix it, update and change the setpoint once. The panel then stores the correct value.

### Entities are unavailable

Check the **Panel ready** diagnostic sensor. If it says `disconnected`, the integration has
lost its session with the panel and is reconnecting. Make sure the panel is powered and
reachable on the network. Brief drops during thermostat changes are a
[known issue](#known-issues) and recover by themselves.

After the connection comes back, the integration reconnects and refreshes everything from the
panel; entities are typically available again within about 20 seconds.

While entities are unavailable, Home Assistant itself skips them when you call an entity
action (for example `light.turn_on`, `lock.unlock` or `alarm_control_panel.alarm_disarm`):
the call returns without an error, but nothing is sent to the panel. This is Home Assistant
core behavior for every integration, not something this integration can change. In
automations, check the entity's state first (or use a condition on it) if you need to know
the action ran. The integration's own actions, `elke27.alarm_arm_automatic` and
`elke27.zone_bypass`, do report an error when the panel is not connected.

If a command fails with *"The panel did not respond in time; the command may not have been
applied."* or *"Lost connection to the panel; the command may not have been applied."*, the
connection is being checked and will be re-established automatically. Check the device's
state before retrying.

### Zones still bypassed after a failed automatic arm

- **Rollback failed:** if an un-bypass fails, the notification and the event
  (`still_bypassed_zone_ids`) list the zones that are still bypassed. Rollback runs once and
  is not retried. Check the panel, then un-bypass them with `elke27.zone_bypass` and
  `bypass: false`.
- **Uncertain arm result** (`stage: arm_uncertain`, `outcome: unknown`): after a timeout or
  dropped connection, the bypasses are deliberately left in place, because the area may be
  armed. Check the panel. If the area is armed, there's nothing to do. If it isn't, un-bypass
  the listed zones.
- **Cancelled by a disarm** (`stage: cancelled`): if the area never armed, the listed zones may
  still be bypassed. Clear any that are.

### Entities are stuck, missing or stay unavailable

**Reload the integration** (**Settings → Devices & services → Elk E27 Alarm Engine Integration → ⋮ → Reload**). This
reconnects and recreates the entities from the panel. If the link keys were rejected, the
reload also brings up the re-link prompt.

### Light state updates late

Normal for some dimmers: the state is re-read about 3 seconds after a change.

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

Go to **Settings → Devices & services → Elk E27 Alarm Engine Integration**, open the **⋮** menu on the entry and choose
**Download diagnostics**.

- The download contains the panel snapshot, with sensitive values redacted by the library.
- It also contains the panel host/port and the integration's client serial number.

Attach it to bug reports.

---

## FAQ

**What's the difference between the access code, the passphrase and my user code?**
The access code and passphrase are linking credentials from your installer, used once at
setup. Your user code (PIN) is what you enter to arm, disarm or bypass.

**Why is there no night mode?** The E27 only has Away and Stay.

**I armed away, but it shows armed home.** That's the panel's auto-stay: no entry/exit zone
opened during the exit delay. `elke27.alarm_arm_automatic` cancels auto-stay.

**Does this need the cloud?** No. Everything is local.

**What happens if I disarm while an automatic arm is running?** The disarm wins. It's sent
right away, and if the panel accepts it, the automatic arm stops. See
[`elke27.alarm_arm_automatic`](#elke27alarm_arm_automatic).

**Why can't I find it in HACS?** It isn't in the default HACS store. Add
`https://github.com/mitchmitchell/HACS-Elk-E27` as a custom repository (type
**Integration**) first; see [Installation](#installation). Add only the canonical repository,
not the ElkProducts mirror as well.

**Does it retry if something fails?** It keeps reconnecting after a dropped connection, but a
single command or a refused request is not retried. See [Retries and errors](#retries-and-errors).

**Can I bypass a zone?** Yes, one or more, with [`elke27.zone_bypass`](#elke27zone_bypass).
Disarming an armed area clears bypasses.

**Can I control a garage door?** Not yet. Garage door (barrier) support is a planned future
feature.

**Does it support Celsius?** For display, yes: Home Assistant converts. The panel works in
whole °F, so setpoints you set in °C are rounded.

**Should I use this or the core Elk E27 Alarm Engine integration?** See
[Relationship to Home Assistant core](#relationship-to-home-assistant-core).

---

## Glossary

- **Area**: a partition of the panel, armed and disarmed on its own. One alarm panel entity each.
- **Away / Stay (Home)**: the E27's two arming modes. Stay leaves interior zones unarmed.
- **Auto-stay**: the panel switches Away to Stay if no entry/exit zone opens during the exit delay.
- **Exit delay**: time to leave after arming before the area is fully armed.
- **Bypass**: tell the panel to ignore a zone until the area is disarmed.
- **Linking / link keys**: the one-time pairing of Home Assistant with the panel; the panel
  issues link keys that the integration stores.

---

## Upgrading

### To 0.1.7

No breaking changes: unique IDs, entity IDs, settings and actions are kept.

- **Requires `elke27` 0.3.11** (installed automatically).
- **`elke27.alarm_arm_automatic` now bypasses open zones** in the targeted area, then arms
  immediately. Automations that relied on it refusing to arm with a door open need their own
  condition (for example the area's `ready` attribute).
- Failed bypasses or arms are **rolled back** (once), except when the result is uncertain.
  Failures create a persistent notification and fire `elke27_arm_automatic_failed`.
- **Disarm always wins** over an automatic arm in progress.
- Commands sent while the panel isn't connected now **fail with an error** instead of
  appearing to succeed. Entities go unavailable during a disconnect and recover after the
  automatic reconnect.
- Panel error messages show the panel's real reason; codes must use the digits 0–9.

### To 0.1.6

Upgrading from 0.1.5 keeps your entities and their unique IDs. Check your automations for
these changes:

- **Requires `elke27` 0.3.10** (installed automatically).
- **Arm night removed.** The E27 has no night mode.
- **Area attributes:** `ready_status_display` and `trouble` were removed. `ready` and
  `ready_status` now report real values. Faulted-zone attributes cover only zones in that area.
- **Custom bypass** now bypasses only open zones in the target area, and won't arm if a bypass
  isn't acknowledged.
- **`elke27.alarm_arm_automatic`** doesn't bypass open zones in 0.1.6; an open zone makes it
  fail with error 11015. Bypassing (in the targeted area only) arrived in 0.1.7.
- **New:** `elke27.zone_bypass` action, panel error reasons and codes in error messages,
  thermostat current humidity, faster light state updates.

---

## Removal

1. Go to **Settings → Devices & services → Elk E27 Alarm Engine Integration**, open the **⋮** menu on the entry and
   choose **Delete**.
2. If you installed through HACS, open **HACS**, find **Elk E27 Alarm Engine Integration**, and choose **Remove**.
   Then restart Home Assistant.
3. If you installed manually, delete `<config>/custom_components/elke27` and restart.

---

## Releases and changelog

Release notes for each version are on the
[Releases page](https://github.com/mitchmitchell/HACS-Elk-E27/releases). Library changes
are tracked in the [`elke27` repository](https://github.com/mitchmitchell/elke27).

---

## Relationship to Home Assistant core

A smaller, alarm-only version of this integration, called **Elk E27 Alarm Engine**, has been
proposed for Home Assistant core in [home-assistant/core#170241](https://github.com/home-assistant/core/pull/170241). It has not
been merged yet. This HACS integration is the full-featured version, with lights, locks,
thermostats and outputs, and it will continue to be maintained.

Both use the domain `elke27`. If the core Elk E27 Alarm Engine integration ships, having
this custom integration installed will override the built-in one.

---

## Support and contributing

- **Bugs and feature requests:**
  [open an issue](https://github.com/mitchmitchell/HACS-Elk-E27/issues). Please include:
  - Home Assistant version, integration version and `elke27` library version
  - Panel firmware version
  - A diagnostics file and debug logs (check them for codes first)
  - The exact error text, including the error code
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

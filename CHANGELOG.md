# Changelog

All notable changes to the Elk E27 Alarm Engine Integration. Full release notes are on the
[Releases page](https://github.com/mitchmitchell/HACS-Elk-E27/releases).

## [Unreleased] - 0.2.1 (draft)

### Changed
1. **Minimum Home Assistant is now 2026.10.0** (was 2026.1.0). HACS won't offer this
   release on older Home Assistant; update Home Assistant first or stay on 0.2.0.
2. **Uses `elke27` 0.3.15.**
3. Area devices link to the panel device by device ID, the form Home Assistant 2026.10
   expects. Entity IDs and device names don't change.
4. Setup forms and service checks use Home Assistant's current validation library, so
   invalid input shows the usual form error.
5. Development: CI, lint and type checks run on Python 3.14 against Home Assistant 2026.10.2.

## [Unreleased] - 0.2.0 (draft)

### Breaking
1. **Delete and re-add required** from any 0.1.x. Each install gets its own client ID, a
   random 12-digit number sent to the panel, and the device layout changed. There is no
   automatic migration and no Repairs issue; old entries fail setup with
   "This entry was created before the client ID existed, or its client ID is invalid. Delete the integration and add it again."
2. **Entity IDs and names change.** Unique IDs have a new format, and each area the panel
   reports gets a device named after it; its alarm panel is named after the area and its
   zones sit under it. Zones with no area and panel-wide entities stay on the panel device.
   After the re-add, Home Assistant creates new entity IDs from the new names, for example
   `alarm_control_panel.<panel>_<area>` → `alarm_control_panel.<area>` and
   `binary_sensor.<panel>_<zone>` → `binary_sensor.<area>_<zone>`. Identical names get `_2`
   IDs. Update automations, dashboards and scripts. Zone names are used as the panel reports
   them, so "Garage Garage door" can appear.
   <!-- PENDING MITCH: E2 Q2/Q3 defaults -->
3. **New client ID per install:** the panel's Network Devices list shows a new 12-digit
   entry. <!-- PENDING MITCH: tell users to remove the old 0.1.x entry -->
4. **Diagnostics changed:** new connection, reconnect, out-of-date-status, version and area
   device fields; `integration_serial` removed. Host, panel MAC, unique ID and client ID are
   redacted; link keys are never included.
5. **Setup errors** are reported through Home Assistant's first refresh: the same retry or
   re-link outcome, different log text. Reconnect log lines move from logger `elke27.hub`
   to `elke27.coordinator`; update log filters.
6. **Availability:** on connection loss all entities go unavailable together and recover
   together on reconnect.
7. **Out-of-date status** is shown in diagnostics only; status out of date for more than
   60 s while connected is read again once automatically (never a command).
8. **Internal:** `custom_components.elke27.hub` is removed; scripts or tests importing it break.
9. **Requires elke27 0.3.14** (was 0.3.13).

### Added
- Diagnostics: `client_present`, `client_ready`, `last_update_success`, `reconnect_attempts`,
  `repairs_issue_active`, `reauth_requested`, `snapshot_stale`, `stale_area_ids`,
  `stale_zone_ids`, `fallback_refresh_count`, `last_fallback_at`, `stale_auto_refresh_count`
  (soak counter, pending Mitch's OK), `elke27_version`, `integration_version`,
  `area_device_count`.
- Area devices the panel no longer reports can be deleted from the device page; setup doesn't remove them.

### Changed
- Unique IDs change from `<key>:<type>:<N>` to `<key>:<N>` on every platform; panel sensors become `<key>:panel_name` and `<key>:panel_ready`. Area device identifiers stay `<key>:area:<N>`. Entity IDs change after the re-add.
- Diagnostics redact host, panel MAC, unique ID and client ID.
- After arm, disarm or bypass the integration uses the library's refreshed status and reads once more only if that failed; each command is sent once.
- Reconnect handling moved into its own component; backoff, Repairs and re-link behave as in 0.1.x. A Repairs issue appears only after 3 consecutive non-network failures; a plain network outage raises none.

### Removed
- Diagnostics field `integration_serial`. `custom_components.elke27.hub`.

## [Unreleased] - 0.1.9 (draft; #50 merged as 139acbe)

### Changed
- Requires [elke27 0.3.13](https://pypi.org/project/elke27/0.3.13/), same as 0.1.8.1 (#50).
- The integration always starts from an empty panel snapshot, never `None` (#50).
- A zone with no reported open/closed state shows unknown with the default icon (#50).
- The panel name sensor falls back to the entry title (#50).
- A link-key decryption failure during setup shows invalid authentication (#50).
- Strict mypy runs in CI. Unused snapshot helpers were removed; link-check probing still
  uses `request_link_check` when the client supports it (#50).

### Removed
- Diagnostics field `snapshot_available` (a snapshot is always present). Update scripts that
  parse diagnostics (#50).

### Breaking
- No config, identity or entity ID changes compared with 0.1.8.1 (checked at #50 ef6ad2f).
  Upgrading from 0.1.8 or 0.1.8.1: update and restart, no re-add. Upgrading from 0.1.7?
  Follow the 0.1.8 delete-and-re-add steps.

## [0.1.8.1] - 2026-10-13

### Added
- Panel identity from the discovery MAC: the MAC is stored on the entry and used as its unique ID; 0.1.8 entries are filled in place on connect with no re-add, except that entries keyed by a real panel serial keep that unique ID, so an address change for them still means delete and re-add (#60).
- Repairs issue "Different panel at this address" when the panel's MAC doesn't match the stored one (#60).
- Diagnostics include `panel_mac`, `panel_mac_filled` and the entry unique ID (#60).

### Changed
- Requires [elke27 0.3.13](https://pypi.org/project/elke27/0.3.13/) (#60).
- README address-change steps match the shipped identity behaviour (#59).

See [release notes](docs/release-notes/v0.1.8.1.md).

## [0.1.8] - 2026-10-13

0.1.8 bundles **#48** (merged `2e589767`), **#53** (reconnect policy, merged `bac4c1ac`), and **#54**
(exit-delay / `elke27` 0.3.12, merged `e0dbdc11`). **Batch C (#50)** is planned for **0.1.9**.

### Fixed
- No duplicate arm when two `elke27.alarm_arm_automatic` calls for the same area overlap:
  only a call queued behind another call on that area polls for armed or exit-delay arming.
  There is no remembered-arm marker; a call made after the first has finished does one live
  status read (#49, fixes #43).
- Alarm control panel shows **ARMING** during the exit delay; automatic arm skips on a fresh
  status read when the area is fully armed or exit-delay arming (#54).

### Changed
- Requires [elke27 0.3.12](https://pypi.org/project/elke27/0.3.12/) ([release notes](https://github.com/mitchmitchell/elke27/releases/tag/v0.3.12)) (#54).
- Setup: bad credentials or an invalid link show a re-link prompt. Temporary errors retry
  setup (#48).
- A re-link prompt starts when the link is rejected while reconnecting, refreshing or running
  a command (#48).
- The re-link flow uses the standard `reauth_confirm` step and refuses a different panel
  (matched by MAC or panel hardware serial) (#48).
- New entries store the panel MAC in Home Assistant's standard format when reported (#48).
- Non-numeric codes are rejected with a validation error (#48).
- Refreshes are serialized and deduped; error handling is narrowed (#48).
- After disconnect, reconnect retries transport, protocol and unknown errors with exponential
  backoff and jitter (about 300 s cap before jitter). Auth refusals start re-link once instead
  of retrying (#53). Non-transport failures log WARNING once per streak (until the next
  successful connect), then DEBUG; transport at DEBUG (#53).

### Breaking
- **All 0.1.7 users must delete the integration entry and add it again when upgrading** (no
  migration). Expect entity IDs to change, so automations, scripts and dashboards may need
  updating. Steps: note the entity IDs you use, delete the entry, upgrade in HACS and restart,
  add the integration, then rename the new entities or update references (#48).
- Entity unique IDs and device identifiers: panel MAC (standard format), else panel hardware
  serial, else config entry ID (#48). In 0.1.7, device identifiers were always
  `<manufacturer number>-<integration serial>` (config entry ID fallback), never MAC-first;
  entity unique IDs used MAC, then integration serial, then the config entry unique ID (#48).
- Config entry unique ID: was the MAC as reported or the integration serial. Now the MAC in
  standard format, else the panel hardware serial, else unset (#48).
- Duplicate detection: by unique ID when the panel reports a MAC or serial (host and port are
  then updated in place); otherwise by host:port only. Without a MAC or serial, an IP change
  counts as a new panel: re-link can't change the address (stored host and port), so delete
  and re-add at the new address (#48).
- Upgrading from 0.1.7 requires delete and re-add; re-linking or keeping a 0.1.7 entry is not supported (#48).
- Rolling back to 0.1.7 also requires delete and re-add after redownloading in HACS.
- `elke27.alarm_arm_automatic` and `elke27.zone_bypass` reject a non-numeric `code`. Use
  digits only (#48).
- Re-link step id changed from `relink` to `reauth_confirm`. Reopen any re-link prompt that
  was open during the upgrade (#48).
- Auth or link failures at setup now give a setup error plus a re-link prompt (#48).
- Rejected link keys or credentials during **automatic reconnect** stop retrying and start
  re-link once (#53).
- After **three non-transport** reconnect failures **since the last successful connect**
  (transport failures in between do not reset the count), Repairs shows **`reconnect_failed`**
  for the entry (cleared on connect, unload, or entry removal); entities stay unavailable
  while disconnected (#53).

## [0.1.7]
See the [v0.1.7 release](https://github.com/mitchmitchell/HACS-Elk-E27/releases/tag/v0.1.7).

## [0.1.6]
See the [v0.1.6 release](https://github.com/mitchmitchell/HACS-Elk-E27/releases/tag/v0.1.6).

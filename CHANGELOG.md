# Changelog

All notable changes to the Elk E27 Alarm Engine Integration. Full release notes are on the
[Releases page](https://github.com/mitchmitchell/HACS-Elk-E27/releases).

## [0.2.1] - 2026-10-30

### Upgrade
- **Upgrade in place, one re-link, from any 0.1.x.** No delete and re-add. After updating,
  enter the access code and passphrase once on the Relink panel card. Entity IDs, registry
  IDs, icons, areas, labels, disabled/hidden state, automations, dashboards and history are
  kept. Config entry VERSION is now 2, so 0.1.x refuses to load an upgraded entry (restore a
  backup to go back).
- The migration runs in two steps: version 1 to 2 offline (plan only, no panel contact), then
  after the re-link and before any entity loads it rewrites unique IDs to the 0.2.x form
  (`<key>:<N>`, panel sensors `<key>:panel_name` / `<key>:panel_ready`) and moves the panel
  device identifier, keeping the device, its area and labels. All or nothing: a conflict
  writes nothing, raises the "Elk E27 upgrade blocked" Repairs issue and keeps the entry
  waiting. Older duplicate entities raise a "Duplicate Elk E27 entities" Repairs issue and
  are left for you to delete.
- Alarm panels and zones that inherited the panel device's area keep it (pinned on the
  entity) when they move to the per-area devices.
- A 0.2.0 install that was deleted and re-added is unchanged (version bump only).
- Entries whose unique ID came from the old client serial are cleared so the re-link can
  match the panel by host and identity, then get the panel MAC as the unique ID.
- Diagnostics: new `legacy_registry_migration` field (state, plan, with the old unique ID
  bases redacted).
- `scripts/restore_entity_names.py`: optional, renames entities back to saved names. Dry run
  by default; `--apply --map` writes and saves an undo map, `--undo` reverses it.
- The 0.2.0 "delete and re-add" advice is replaced by the above. Displayed names follow the
  0.2.x area-style names; entity IDs do not change.

### Added
- **Garage doors (#68):** each garage door set up on the panel appears as a garage door
  cover on the panel device, named from the panel or "Garage door <number>". Open, close and
  open/closed state only (no stop, position or opening/closing states). Each command is sent
  once and never retried; if the outcome is unknown the integration reads the door's status
  once and asks you to check the door. A panel refusal says the configured user may lack
  automation authority. Link or sign-in errors on a garage command start the re-link flow.
  See [Garage doors](README.md#garage-doors).

### Changed
- **Minimum Home Assistant is now 2026.10.0** (was 2026.1.0). HACS won't offer this
  release on older Home Assistant; update Home Assistant first or stay on 0.2.0.
- **Uses `elke27` 0.3.15.**
- **Area devices:** new installs link area devices to the panel device by device ID, so
  entity IDs follow the documented form (`alarm_control_panel.<area>`,
  `binary_sensor.<area>_<zone>`). Upgrading from 0.2.0 on Home Assistant 2026.10 keeps
  your existing area devices, device IDs and entity IDs as they are; on those installs
  area entity IDs keep the panel name prefix 0.2.0 gave them (for example
  `alarm_control_panel.<panel>_<area>`).
- Setup forms and service checks use Home Assistant's current validation library, so
  invalid input shows the usual form error.
- Development: CI, lint and type checks run on Python 3.14 against Home Assistant 2026.10.2.
- Zone sensors take their device class only from the panel's Zone Definition: `BURG INTERIOR`
  is motion, perimeter and entry/exit are opening, `FIRE` smoke, `CARBON MONOXIDE` carbon
  monoxide, `WATER` moisture, everything else generic. Zones the panel typed as Door/Window
  now show as opening; undefined or unlisted zones change from opening to generic. "Show as"
  overrides are kept across reloads and upgrades. (#110, supersedes #102; fixes #101)
- Entities go unavailable as soon as the panel connection drops, not on the next update.
  (#98, fixes #97)
- "Panel connection lost" is logged once per drop at WARNING (was INFO). (#83, fixes #80)
- Reconnect backoff is capped at 60 s (about 72 s with jitter), down from 5 minutes. (#82, fixes #79)
- Lights: full brightness is sent as level 99 (the panel reports 100). A plain turn-on restores
  the last level, and the reported level is scaled correctly. (#92, #100; fixes #91, #99)

### Fixed
- **Empty panel discovery opens manual host setup.** When discovery finds no panels, setup
  continues on the manual IP/host form (with the existing “no panels found” message) instead
  of staying on Discover with access code and passphrase fields and no panel to link.
- **Arming an area that is not ready now shows a clear message.** When the panel refused
   an arm or bypass because the area was not ready (a zone open), was in alarm, was
   already armed, or the zone can not be bypassed, Home Assistant returned a server error
   and logged an ERROR. It now shows a plain message saying why and logs nothing at ERROR
   level. Bypasses made by the same call are undone, except when the area turned out to be
   armed already, where they are kept as before. A duress code is treated as a rejected
   code.
- No brief false "off" after turning on or dimming a light; the immediate re-read is gone.
  (#93, fixes #89)
- An all-zero user code is a validation error, not a server error. (#94, fixes #90)
- A code the panel rejects (not authorized) is a user error (`code_rejected`) on arm, disarm,
  bypass and auto-arm. The code is never shown or logged. (#108, fixes #109)
- Every other way the panel rejects a code (including an invalid user code) is also a user
  error on disarm, arm, custom bypass and its rollback, automatic arm and zone bypass: the
  same "The panel rejected the code" message, event and notification, nothing at ERROR
  level. (#114)
- Upgrade Repairs issues for unreadable upgrade notes, leftover
  entities/devices/items, interrupted or failed saves, and duplicates (with guided
  delete-and-retry). Strict reads of the upgrade record: setup fails cleanly on a damaged one.
  Old identifiers are kept out of logs.

### Docs
- README accuracy fixes. (#95)

### Internal
- Integration ID read directly; typed library calls instead of getattr. (#88, #103)

## [0.2.0] - 2026-10-16

### Breaking
1. **No in-place upgrade from 0.1.x in 0.2.0; go straight to 0.2.1**, which upgrades in
   place with one re-link (see above). Each install gets its own client ID, a random
   12-digit number sent to the panel, and the device layout changed. 0.2.0 itself leaves a
   0.1.x entry failing setup with
   "This entry was created before the client ID existed, or its client ID is invalid."
   Don't delete it; update to 0.2.1.
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

## [0.1.9] - 2026-10-15

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

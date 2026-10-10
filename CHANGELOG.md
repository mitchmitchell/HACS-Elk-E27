# Changelog

All notable changes to the Elk E27 Alarm Engine Integration. Full release notes are on the
[Releases page](https://github.com/mitchmitchell/HACS-Elk-E27/releases).

## [Unreleased] - 0.1.8 (draft; unreleased until the exit-delay double-arm fix (#54) lands)

0.1.8 bundles **#48** (merged `2e589767`), **#53** (reconnect policy, merged `bac4c1ac`), and **#54**
(exit-delay / `elke27` 0.3.12). **Batch C (#50)** is planned for **0.1.9**.

### Fixed
- No duplicate arm when two `elke27.alarm_arm_automatic` calls for the same area overlap:
  only a call queued behind another call on that area polls for armed or exit-delay arming.
  There is no remembered-arm marker; a call made after the first has finished does one live
  status read (#49, fixes #43).
- Alarm control panel shows **ARMING** during the exit delay; automatic arm skips on a fresh
  status read when the area is fully armed or exit-delay arming (#54).

### Changed
- Requires **`elke27==0.3.12`** (#54).
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
- Re-linking a 0.1.7 entry usually stops with "wrong panel" because its stored unique ID
  (often the integration serial) isn't accepted; a lower-case colon MAC may still work. Delete
  and re-add instead (#48).
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

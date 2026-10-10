# Changelog

All notable changes to the Elk E27 Alarm Engine Integration. Full release notes are on the
[Releases page](https://github.com/mitchmitchell/HACS-Elk-E27/releases).

## [Unreleased] - 0.1.8 (draft)

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
- Upgrading from 0.1.7: delete the integration entry, upgrade, then re-add it. Entity unique
  IDs and device identifiers use panel MAC, then panel hardware serial, then the config entry
  ID. When the panel reports neither MAC nor serial, **unique_id stays unset**, dedupe uses
  **host:port**, and devices and entities use the **config entry ID**; an IP change requires
  delete and re-add. Automations, scripts, and dashboards may need updates (see release notes)
  (#48).
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

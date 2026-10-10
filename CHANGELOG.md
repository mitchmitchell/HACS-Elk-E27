# Changelog

All notable changes to the Elk E27 Alarm Engine Integration. Full release notes are on the
[Releases page](https://github.com/mitchmitchell/HACS-Elk-E27/releases).

## [Unreleased] - 0.1.8 (draft)

### Fixed
- No duplicate arm when two `elke27.alarm_arm_automatic` calls for the same area overlap:
  only a call queued behind another call on that area polls for armed or exit-delay arming.
  There is no remembered-arm marker; a call made after the first has finished does one live
  status read (#49, fixes #43).
- Alarm control panel shows **ARMING** during the exit delay; automatic arm skips on a fresh
  status read when the area is fully armed or exit-delay arming (#54).

### Changed
- Requires **`elke27==0.3.12`** (gated on #54).
- Setup: bad credentials or an invalid link show a re-link prompt. Temporary errors retry
  setup (#48).
- A re-link prompt starts when the link is rejected while reconnecting, refreshing or running
  a command (#48).
- The re-link flow uses the standard `reauth_confirm` step and refuses a different panel
  (matched by MAC or panel hardware serial) (#48).
- New entries store the panel MAC in Home Assistant's standard format when reported (#48).
- Non-numeric codes are rejected with a validation error (#48).
- Refreshes are serialized and deduped; error handling is narrowed (#48).

### Breaking
- Upgrading from 0.1.7: delete the integration entry, upgrade, then re-add it. Entity unique
  IDs and device identifiers use panel MAC, then panel hardware serial, then the config entry
  ID. When the panel reports neither MAC nor serial, **unique_id stays unset**, dedupe uses
  **host:port**, and devices and entities use the **config entry ID**; an IP change requires
  delete and re-add. Automations, scripts, and dashboards may need updates (see release notes).
- Rolling back to 0.1.7 also requires delete and re-add after redownloading in HACS.
- `elke27.alarm_arm_automatic` and `elke27.zone_bypass` reject a non-numeric `code`. Use
  digits only (#48).
- Re-link step id changed from `relink` to `reauth_confirm`. Reopen any re-link prompt that
  was open during the upgrade (#48).
- Auth or link failures at setup now give a setup error plus a re-link prompt (#48).

## [0.1.7]
See the [v0.1.7 release](https://github.com/mitchmitchell/HACS-Elk-E27/releases/tag/v0.1.7).

## [0.1.6]
See the [v0.1.6 release](https://github.com/mitchmitchell/HACS-Elk-E27/releases/tag/v0.1.6).

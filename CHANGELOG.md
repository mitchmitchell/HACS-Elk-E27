# Changelog

All notable changes to the Elk E27 Alarm Engine Integration. Full release notes are on the
[Releases page](https://github.com/mitchmitchell/HACS-Elk-E27/releases).

## [Unreleased] - 0.1.8 (draft)

### Fixed
- No duplicate arm when two `elke27.alarm_arm_automatic` calls for the same area overlap
  (#49, fixes #43).

### Changed (pending #48, not merged yet)
- Setup: bad credentials or an invalid link show a re-link prompt. Temporary errors retry
  setup.
- A re-link prompt starts when the link is rejected while reconnecting, refreshing or running
  a command.
- The re-link flow uses the standard `reauth_confirm` step, keeps the panel identity and
  refuses a different panel (matched by MAC or integration serial).
- New entries store the panel MAC in Home Assistant's standard format.
- Non-numeric codes are rejected with a validation error.
- Refreshes are serialized and deduped; error handling is narrowed.

### Breaking (pending #48)
- `elke27.alarm_arm_automatic` and `elke27.zone_bypass` reject a non-numeric `code`. Use
  digits only.
- Re-link step id changed from `relink` to `reauth_confirm`. Reopen any re-link prompt that
  was open during the upgrade.
- Auth or link failures at setup now give a setup error plus a re-link prompt.

## [0.1.7]
See the [v0.1.7 release](https://github.com/mitchmitchell/HACS-Elk-E27/releases/tag/v0.1.7).

## [0.1.6]
See the [v0.1.6 release](https://github.com/mitchmitchell/HACS-Elk-E27/releases/tag/v0.1.6).
